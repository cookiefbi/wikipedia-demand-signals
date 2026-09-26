"""The `analyze` command: QIDs or titles -> metrics, verdicts, ranking and files.

Requests, all cached: one Wikidata call for the sitelinks of every --qid, one
MediaWiki call per --article, one edition answer per language and, per article,
one MediaWiki call (first edit, redirects) plus one pageviews answer for the
article and one for each counted redirect (at most 10). Everything is kept as
messages (i18n.Msg) until the very end: the JSON gets English text, the PDF the
report language.
"""

import csv
import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from wds_lib import WdsError, i18n, langs, log, pageviews, resolve, series, verdict
from wds_lib.i18n import Msg
from wds_lib.langs import Lang
from wds_lib.series import Span
from wds_lib.verdict import Metrics, Monthly, Verdict

RESULT_JSON = "result.json"
DATA_CSV = "data.csv"
CHART_PNG = "chart.png"
REPORT_PDF = "report.pdf"

QID_PATTERN = re.compile(r"Q[1-9]\d*")

# Without --out every question gets its own folder, so a second topic never
# overwrites the first one's report. last-result.json always holds the latest
# run's JSON (errors too): a fixed ASCII path to fall back on when the console
# garbles the printed output, including the paths inside it.
DEFAULT_ROOT = "wds-output"
LAST_RESULT = "last-result.json"
# Keeps folder names short: Windows paths are limited to 260 characters in total.
TOPIC_SLUG_MAX = 40

# Wikimedia filters bots more strictly from this day on and did not reprocess
# earlier data (Wikitech, Data Issues 2025-06-03): growth whose two years straddle
# it partly measures the rule change, which makes it the key caveat in must_say.
BOT_RULES_CHANGED = dt.date(2025, 3, 20)
# Letters that Unicode normalization cannot split into an ASCII letter + accent.
ASCII_LETTERS = str.maketrans(
    {"ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O", "ß": "ss", "æ": "ae"}
)


@dataclass(frozen=True)
class Target:
    lang: Lang
    qid: str | None  # None for an --article page without a Wikidata item
    title: str | None  # None: no article in this edition


@dataclass(frozen=True)
class Measured:
    data: Monthly
    metrics: Metrics
    verdict: Verdict


@dataclass(frozen=True)
class Analysis:
    """Everything the JSON, the CSV and the report are made from."""

    label: str
    item_labels: dict[str, str]  # qid -> English label, to name a missing article
    langs: list[str]  # requested order: a language keeps its chart color across runs
    generated: dt.date
    period: Span
    span: Span  # period + growth base: every month a number is computed from
    rank_by: str
    targets: list[Target]
    measured: dict[Target, Measured]  # targets that have an article
    totals: dict[str, Measured]  # lang code -> sum, only for 2+ articles in a language
    ranking: list[tuple[str, Msg]]
    assumptions: list[Msg]


def utc_today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


# --- arguments --------------------------------------------------------------


def parse_qids(values: list[str]) -> list[str]:
    """['Q1,q2', 'Q3'] -> ['Q1', 'Q2', 'Q3']; order kept, duplicates dropped."""
    qids: list[str] = []
    for value in values:
        for token in value.split(","):
            qid = token.strip().upper()
            if not qid:
                continue
            if not QID_PATTERN.fullmatch(qid):
                raise WdsError(
                    f"'{token.strip()}' is not a Wikidata item id",
                    hint="pass ids like --qid Q1666254, as printed by `resolve`",
                )
            if qid not in qids:
                qids.append(qid)
    return qids


def parse_article(value: str) -> tuple[Lang, str]:
    """'pl:Głodówka lecznicza' -> (pl, 'Głodówka lecznicza')."""
    code, sep, title = value.partition(":")
    if not sep or not code.strip() or not title.strip():
        raise WdsError(
            f"--article must look like 'pl:Title', got '{value}'",
            hint="prefix the article title with its language code and a colon",
        )
    return langs.parse_langs(code)[0], title.strip()


def slug(text: str, max_len: int = TOPIC_SLUG_MAX) -> str:
    """ASCII-only piece of a folder name: 'Głodówka lecznicza' -> 'glodowka-lecznicza'.

    ASCII on purpose: the path must stay usable where the console garbles
    everything else. Scripts without Latin letters (Cyrillic) give ''.
    """
    decomposed = unicodedata.normalize("NFKD", text.translate(ASCII_LETTERS))
    ascii_text = decomposed.encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:max_len].strip("-")


def output_dir(out: str) -> Path:
    """--out relative to the user's current folder, not to the skill."""
    return Path(out).expanduser().resolve()


def default_dir(
    a: "Analysis", period: str | None, date_from: str | None, date_to: str | None
) -> Path:
    """./wds-output/<topic>_<langs>_<period>, e.g. intermittent-fasting_pl-cs_24m."""
    qids = "-".join(t.qid for t in a.targets if t.qid)
    topic = slug(a.label) or slug(qids) or "article"
    if date_from or date_to:
        span = (
            f"{series.month_label(a.period.first)}-{series.month_label(a.period.last)}"
        )
    else:
        span = period or series.DEFAULT_PERIOD
    return Path.cwd() / DEFAULT_ROOT / f"{topic}_{'-'.join(a.langs)}_{span}"


def save_result(out: str | None, result: dict, text: str) -> None:
    """Write the printed JSON to result.json next to the run's files and, without
    --out, to wds-output/last-result.json. Errors are written too, so a stale
    result of an earlier run is never mistaken for this one's.
    """
    paths = []
    if saved := result.get("files", {}).get("result_json"):
        paths.append(Path(saved))
    if out:
        paths.append(output_dir(out) / RESULT_JSON)
    else:
        paths.append(Path.cwd() / DEFAULT_ROOT / LAST_RESULT)
    for path in dict.fromkeys(paths):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text + "\n")
        except OSError as exc:
            log(f"cannot write {path}: {exc}")


# --- analysis ---------------------------------------------------------------


def collect_targets(
    qids: list[str], articles: list[tuple[Lang, str]], requested: list[Lang]
) -> tuple[list[Target], str, dict[str, str]]:
    """One target per (item, language), then one per --article; a topic label; and
    the label of each item."""
    targets: list[Target] = []
    labels: list[str] = []
    item_labels: dict[str, str] = {}
    if qids:
        items = resolve.sitelink_titles(qids)
        for qid in qids:
            item_labels[qid] = items[qid]["label"] or qid
            labels.append(item_labels[qid])
            titles = items[qid]["titles"]
            targets += [
                Target(lang, qid, titles.get(lang.dbname)) for lang in requested
            ]
    for lang, title in articles:
        canonical, qid = resolve.lookup_article(lang, title)
        if not any(t.lang == lang and t.title == canonical for t in targets):
            labels.append(canonical)
            targets.append(Target(lang, qid, canonical))
    return targets, " + ".join(labels), item_labels


def _measure(data: Monthly, period: Span) -> Measured:
    metrics = verdict.measure(data, period)
    return Measured(data, metrics, verdict.judge(metrics))


def article_data(
    target: Target,
    meta: resolve.ArticleMeta,
    window: Span,
    span: Span,
    edition: np.ndarray,
) -> Monthly:
    """Monthly views of an article plus its redirects, from its first edit on.

    Before a rename the article's views were recorded under its old title, now a
    redirect: the sum keeps the series continuous across the rename.
    """
    created = meta.created
    if created is not None and created < window.start:
        created = None  # older than the window: nothing to cut

    def monthly(title: str) -> np.ndarray:
        daily = pageviews.article_daily(target.lang, title, window)
        if created is not None:
            daily = series.drop_before(daily, window.start, created)
        months, values = series.monthly_totals(daily, window.start)
        return series.cut(months, values, span)

    article = monthly(target.title)
    redirects = np.zeros_like(article)
    for title in meta.redirects:
        redirects += monthly(title)
    warnings: tuple[Msg, ...] = ()
    if meta.redirects_total > len(meta.redirects):
        total = str(meta.redirects_total) + ("+" if meta.more_redirects else "")
        params = {"title": target.title, "total": total, "counted": len(meta.redirects)}
        warnings = (Msg("warn.redirects_capped", params),)
    return Monthly(
        span=span,
        views=article + redirects,
        redirects=redirects,
        edition=edition,
        created=created,
        warnings=warnings,
    )


def _topic_total(parts: list[Measured], period: Span) -> Measured:
    """Several articles in one language: the topic is their sum."""
    first = parts[0].data
    created = [p.data.created for p in parts]
    data = Monthly(
        span=first.span,
        views=sum(p.data.views for p in parts),
        redirects=sum(p.data.redirects for p in parts),
        edition=first.edition,
        created=None if None in created else min(created),
        warnings=tuple(w for p in parts for w in p.data.warnings),
    )
    return _measure(data, period)


def _article_note(t: Target, meta: resolve.ArticleMeta, data: Monthly) -> Msg:
    """'cs: topic measured by article ... (+3 redirects), created ...'."""
    count = len(meta.redirects)
    redirects: Msg | str = ""
    if count == 1:
        redirects = Msg("assume.redirects_one")
    elif count > 1:
        redirects = Msg("assume.redirects_many", {"count": count})
    created: Msg | str = ""
    if data.created is not None and data.created_month >= data.span.first:
        created = Msg("assume.created", {"day": str(data.created)})
    return Msg(
        "assume.article",
        {
            "lang": t.lang.code,
            "title": t.title,
            "redirects": redirects,
            "created": created,
        },
    )


def _assumptions(
    measured: dict[Target, Measured],
    metas: dict[Target, resolve.ArticleMeta],
    counts: dict[str, int],
    period: Span,
    moved_from: Span | None,
    added: list[tuple[str, str]],
) -> list[Msg]:
    last, prev = series.yoy_spans(period)
    notes = [
        Msg("assume.lang_added", {"lang": code, "article": article})
        for code, article in added
    ]
    notes += [_article_note(t, metas[t], m.data) for t, m in measured.items()]
    notes += [
        Msg("assume.topic_sum", {"lang": code, "count": count})
        for code, count in counts.items()
        if count > 1
    ]
    notes += [
        Msg("assume.redirects", {"max_redirects": resolve.MAX_REDIRECTS}),
        Msg("assume.traffic"),
        Msg(
            "assume.growth",
            {
                "last_from": series.month_label(last.first),
                "last_to": series.month_label(last.last),
                "prev_from": series.month_label(prev.first),
                "prev_to": series.month_label(prev.last),
            },
        ),
        Msg("assume.per_million"),
        Msg("assume.period_end", {"to": series.month_label(period.last)}),
    ]
    if moved_from is not None:
        notes.append(Msg("assume.window_moved", {"day": str(moved_from.end)}))
    return notes


def analyze(
    *,
    qids: list[str],
    articles: list[str],
    langs_arg: str | None,
    period: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    rank_by: str = "growth",
    today: dt.date | None = None,
) -> Analysis:
    qid_list = parse_qids(qids)
    article_list = [parse_article(value) for value in articles]
    if not qid_list and not article_list:
        raise WdsError(
            "nothing to analyze",
            hint="pass --qid Q... from `resolve`, or --article lang:Title",
        )
    requested = langs.parse_langs(langs_arg) if langs_arg else []
    if qid_list and not requested:
        raise WdsError(
            "--langs is required with --qid",
            hint="pass the language editions to compare, e.g. --langs pl,cs",
        )
    # An --article brings its own language: added, and said so in the assumptions.
    added: list[tuple[str, str]] = []
    for lang, title in article_list:
        if lang not in requested:
            requested.append(lang)
            added.append((lang.code, f"{lang.code}:{title}"))
    if len(requested) > langs.MAX_LANGS:
        raise WdsError(
            f"{len(requested)} languages with the --article ones, the limit is "
            f"{langs.MAX_LANGS} per run",
            hint="split into runs of up to 10 languages; overlapping runs reuse the cache",
        )
    today = today or utc_today()
    # Check the period before any request; it is resolved again below against the
    # window the API has actually published.
    series.resolve_period(series.fetch_window(today), period, date_from, date_to)

    targets, label, item_labels = collect_targets(qid_list, article_list, requested)
    found = [t for t in targets if t.title]
    window, moved = series.fetch_window(today), False
    if found:
        window, moved = pageviews.published_window(today, found[0].lang)
    chosen = series.resolve_period(window, period, date_from, date_to)
    span = series.analysis_span(chosen)

    editions = {}
    for lang in dict.fromkeys(t.lang for t in found):
        months, values = series.monthly_totals(
            pageviews.edition_daily(lang, window), window.start
        )
        editions[lang.code] = series.cut(months, values, span)

    measured: dict[Target, Measured] = {}
    metas: dict[Target, resolve.ArticleMeta] = {}
    for target in found:
        metas[target] = resolve.article_meta(target.lang, target.title)
        data = article_data(
            target, metas[target], window, span, editions[target.lang.code]
        )
        measured[target] = _measure(data, chosen)

    by_lang: dict[str, list[Measured]] = {}
    for target in found:
        by_lang.setdefault(target.lang.code, []).append(measured[target])
    totals = {
        code: _topic_total(parts, chosen)
        for code, parts in by_lang.items()
        if len(parts) > 1
    }
    per_lang = {code: totals.get(code) or parts[0] for code, parts in by_lang.items()}
    ranking = verdict.rank(
        [(code, m.metrics, m.verdict) for code, m in per_lang.items()], rank_by
    )
    counts = {code: len(parts) for code, parts in by_lang.items()}
    return Analysis(
        label=label,
        item_labels=item_labels,
        langs=[lang.code for lang in requested],
        generated=today,
        period=chosen,
        span=span,
        rank_by=rank_by,
        targets=targets,
        measured=measured,
        totals=totals,
        ranking=ranking,
        assumptions=_assumptions(
            measured,
            metas,
            counts,
            chosen,
            window.shifted(1) if moved else None,
            added,
        ),
    )


# --- outputs ----------------------------------------------------------------


def per_language(a: Analysis) -> dict[str, Measured]:
    """The measurement that stands for each language: the topic total, or the
    language's only article."""
    out: dict[str, Measured] = {}
    for target, measured in a.measured.items():
        code = target.lang.code
        out.setdefault(code, a.totals.get(code, measured))
    return out


def _main_point(a: Analysis, data: dict[str, Measured], lang: str) -> str:
    """Languages in ranking order, each with its trend word and the value it is
    ranked by, the metric named next to every number."""
    field = verdict.RANK_FIELDS[a.rank_by]
    items = []
    for code, _ in a.ranking:
        m = data[code]
        value = getattr(m.metrics, field)
        params = {
            "lang": code,
            "trend": Msg(f"trend_name.{m.verdict.trend}"),
            "value": value,
        }
        key = "must.item.unknown" if value is None else f"must.item.{a.rank_by}"
        item = i18n.render(key, params, lang)
        if m.verdict.confidence == verdict.LOW:
            item = i18n.render("must.low", {"item": item}, lang)
        items.append(item)
    if len(items) == 1:
        return i18n.render("must.main_one", {"items": items[0]}, lang)
    params = {"by": Msg(f"rank_by.{a.rank_by}"), "items": "; ".join(items)}
    return i18n.render("must.main", params, lang)


def _confidence_point(a: Analysis, data: dict[str, Measured], lang: str) -> str:
    """Confidence per language with its main reason; languages that share both
    are named together."""
    groups: dict[tuple[str, str], list[str]] = {}
    for code, _ in a.ranking:
        v = data[code].verdict
        limit = Msg("must.checks_passed") if v.limit is None else v.limit
        groups.setdefault((v.confidence, limit.render(lang)), []).append(code)
    return " ".join(
        i18n.render(
            "must.confidence",
            {
                "langs": ", ".join(codes),
                "level": Msg(f"conf_name.{level}"),
                "reason": reason,
            },
            lang,
        )
        for (level, reason), codes in groups.items()
    )


def must_say(a: Analysis, lang: str = "en") -> list[str]:
    """3-5 sentences the agent passes on point by point (SPEC 3), made only of what
    the JSON already holds: the answer, how far to trust it, missing articles, what
    pageviews cannot show, and the one data caveat that matters most here."""
    data = per_language(a)
    points = []
    if a.ranking:
        points += [_main_point(a, data, lang), _confidence_point(a, data, lang)]
    languages = dict.fromkeys(t.lang.code for t in a.targets if t.title is None)
    missing = [code for code in languages if code not in data]
    if missing:
        params = {"langs": ", ".join(missing)}
        points.append(i18n.render("must.no_article", params, lang))
    points.append(i18n.render("must.limits", None, lang))
    last, prev = series.yoy_spans(a.period)
    if prev.start < BOT_RULES_CHANGED <= last.end:
        params = {"day": str(BOT_RULES_CHANGED)}
        points.append(i18n.render("must.bot_rules", params, lang))
    else:
        key = "must.proxy_sum" if a.totals else "must.proxy"
        points.append(i18n.render(key, None, lang))
    return points


def _numbers(m: Measured) -> dict:
    metrics, v = m.metrics, m.verdict
    return {
        "views_last_12m": metrics.views_last_12m,
        "views_prev_12m": metrics.views_prev_12m,
        "growth_pct": metrics.growth_pct,
        "per_million_last_12m": metrics.per_million_last_12m,
        "per_million_growth_pct": metrics.per_million_growth_pct,
        "trend": v.trend,
        "confidence": v.confidence,
        "reasons": i18n.texts(list(v.reasons)),
        "warnings": i18n.texts(list(v.warnings)),
    }


def to_json(a: Analysis, files: dict[str, str]) -> dict:
    results = []
    for t in a.targets:
        head = {"qid": t.qid, "lang": t.lang.code}
        if t in a.measured:
            results.append(
                head | {"status": "ok", "title": t.title} | _numbers(a.measured[t])
            )
        else:
            results.append(head | {"status": "no_article"})
    out: dict = {
        "status": "ok",
        "period": a.period.as_dict(),
        # right after the period, so a cut-off output still carries it
        "must_say": must_say(a),
        "assumptions": i18n.texts(a.assumptions),
        "results": results,
    }
    if a.totals:
        out["topic_totals"] = [
            {"lang": code, "articles": sum(t.lang.code == code for t in a.measured)}
            | _numbers(m)
            for code, m in a.totals.items()
        ]
    out["ranking"] = {
        "by": a.rank_by,
        "order": [{"lang": code, "why": why.render()} for code, why in a.ranking],
    }
    out["caveats"] = [i18n.render(key) for key in i18n.CAVEATS]
    out["files"] = files
    return out


def write_csv(a: Analysis, path: Path) -> None:
    """Monthly rows for every month any number was computed from, so each figure in
    result.json can be recomputed. UTF-8 with BOM: Excel then shows ł, ř, ї right.
    """
    columns = [
        "month",
        "lang",
        "qid",
        "title",
        "article_views",
        "redirect_views",
        "views",
        "edition_views",
        "views_per_million",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(columns)
        for target, m in a.measured.items():
            for i, month in enumerate(range(a.span.first, a.span.last + 1)):
                views, edition = int(m.data.views[i]), int(m.data.edition[i])
                redirects = int(m.data.redirects[i])
                share = verdict.per_million(views, edition)
                writer.writerow(
                    [
                        series.month_label(month),
                        target.lang.code,
                        target.qid or "",
                        target.title,
                        views - redirects,
                        redirects,
                        views,
                        edition,
                        "" if share is None else round(share, 3),
                    ]
                )


def run(
    args: dict,
    out: str | None,
    *,
    report: bool = False,
    report_lang: str = "en",
    note: str | None = None,
    today: dt.date | None = None,
) -> dict:
    """analyze() plus files; returns the result JSON (result.json is written by the
    caller with exactly what goes to stdout).
    """
    if report and report_lang not in i18n.TEXTS:
        raise WdsError(
            f"--report-lang {report_lang} is not available yet",
            hint="use --report-lang en",
        )
    analysis = analyze(**args, today=today)
    if out:
        folder = output_dir(out)
    else:
        folder = default_dir(
            analysis, args.get("period"), args.get("date_from"), args.get("date_to")
        )
    try:
        folder.mkdir(parents=True, exist_ok=True)
        files = {"result_json": str(folder / RESULT_JSON)}
        write_csv(analysis, folder / DATA_CSV)
        files["data_csv"] = str(folder / DATA_CSV)
        if report:
            # matplotlib takes a second to import: only when a report is asked for.
            from wds_lib import report as charts

            charts.write_png(analysis, folder / CHART_PNG, report_lang)
            files["chart_png"] = str(folder / CHART_PNG)
            charts.write_pdf(analysis, folder / REPORT_PDF, report_lang, note)
            files["report_pdf"] = str(folder / REPORT_PDF)
    except OSError as exc:
        raise WdsError(
            f"cannot write to {folder}: {exc}",
            hint="pass --out with a folder you can write to; if report.pdf is open "
            "in a viewer, close it and rerun",
        ) from exc
    return to_json(analysis, files)
