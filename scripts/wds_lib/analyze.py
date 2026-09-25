"""The `analyze` command: QIDs or titles -> metrics, verdicts, ranking and files.

Requests, all cached: one Wikidata call for the sitelinks of every --qid, one
MediaWiki call per --article, one edition answer per language and one answer per
article. Everything is kept as messages (i18n.Msg) until the very end: the JSON
gets English text, the PDF the report language.
"""

import csv
import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from wds_lib import WdsError, i18n, langs, log, pageviews, resolve, series, verdict
from wds_lib.i18n import Msg
from wds_lib.langs import Lang
from wds_lib.series import Span
from wds_lib.verdict import Metrics, Monthly, Verdict

RESULT_JSON = "result.json"
DATA_CSV = "data.csv"

QID_PATTERN = re.compile(r"Q[1-9]\d*")


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


def parse_article(value: str, requested: list[Lang]) -> tuple[Lang, str]:
    """'pl:Głodówka lecznicza' -> (pl, 'Głodówka lecznicza')."""
    code, sep, title = value.partition(":")
    if not sep or not code.strip() or not title.strip():
        raise WdsError(
            f"--article must look like 'pl:Title', got '{value}'",
            hint="prefix the article title with its language code and a colon",
        )
    lang = langs.parse_langs(code)[0]
    if lang not in requested:
        raise WdsError(
            f"--article '{value}' is in '{lang.code}', which is not in --langs",
            hint=f"add {lang.code} to --langs",
        )
    return lang, title.strip()


def output_dir(out: str) -> Path:
    """--out relative to the user's current folder, not to the skill."""
    return Path(out).expanduser().resolve()


def save_result(out: str, text: str) -> Path | None:
    """Write result.json. Also called with an error object, so a stale result of an
    earlier run is never mistaken for this one's.
    """
    path = output_dir(out) / RESULT_JSON
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text + "\n")
    except OSError as exc:
        log(f"cannot write {path}: {exc}")
        return None
    return path


# --- analysis ---------------------------------------------------------------


def collect_targets(
    qids: list[str], articles: list[tuple[Lang, str]], requested: list[Lang]
) -> tuple[list[Target], str]:
    """One target per (item, language), then one per --article; and a topic label."""
    targets: list[Target] = []
    labels: list[str] = []
    if qids:
        items = resolve.sitelink_titles(qids)
        for qid in qids:
            labels.append(items[qid]["label"] or qid)
            titles = items[qid]["titles"]
            targets += [
                Target(lang, qid, titles.get(lang.dbname)) for lang in requested
            ]
    for lang, title in articles:
        canonical, qid = resolve.lookup_article(lang, title)
        if not any(t.lang == lang and t.title == canonical for t in targets):
            labels.append(canonical)
            targets.append(Target(lang, qid, canonical))
    return targets, " + ".join(labels)


def _measure(data: Monthly, period: Span) -> Measured:
    metrics = verdict.measure(data, period)
    return Measured(data, metrics, verdict.judge(metrics))


def _topic_total(parts: list[Measured], period: Span) -> Measured:
    """Several articles in one language: the topic is their sum."""
    first = parts[0].data
    created = [p.data.created for p in parts]
    data = Monthly(
        span=first.span,
        views=sum(p.data.views for p in parts),
        edition=first.edition,
        created=None if None in created else min(created),
    )
    return _measure(data, period)


def _assumptions(
    targets: list[Target], counts: dict[str, int], period: Span, moved_from: Span | None
) -> list[Msg]:
    last, prev = series.yoy_spans(period)
    notes = [
        Msg("assume.article", {"lang": t.lang.code, "title": t.title})
        for t in targets
        if t.title
    ]
    notes += [
        Msg("assume.topic_sum", {"lang": code, "count": count})
        for code, count in counts.items()
        if count > 1
    ]
    notes += [
        Msg("assume.no_redirects"),
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
    langs_arg: str,
    period: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    rank_by: str = "growth",
    today: dt.date | None = None,
) -> Analysis:
    requested = langs.parse_langs(langs_arg)
    qid_list = parse_qids(qids)
    article_list = [parse_article(value, requested) for value in articles]
    if not qid_list and not article_list:
        raise WdsError(
            "nothing to analyze",
            hint="pass --qid Q... from `resolve`, or --article lang:Title",
        )
    today = today or utc_today()
    # Check the period before any request; it is resolved again below against the
    # window the API has actually published.
    series.resolve_period(series.fetch_window(today), period, date_from, date_to)

    targets, label = collect_targets(qid_list, article_list, requested)
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
    for target in found:
        months, values = series.monthly_totals(
            pageviews.article_daily(target.lang, target.title, window), window.start
        )
        first = series.first_month_with_views(months, values)
        data = Monthly(
            span=span,
            views=series.cut(months, values, span),
            edition=editions[target.lang.code],
            created=None if first is None or first == window.first else first,
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
        period=chosen,
        span=span,
        rank_by=rank_by,
        targets=targets,
        measured=measured,
        totals=totals,
        ranking=ranking,
        assumptions=_assumptions(
            targets, counts, chosen, window.shifted(1) if moved else None
        ),
    )


# --- outputs ----------------------------------------------------------------


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
                share = verdict.per_million(views, edition)
                writer.writerow(
                    [
                        series.month_label(month),
                        target.lang.code,
                        target.qid or "",
                        target.title,
                        views,
                        "",  # redirects are not fetched yet
                        views,
                        edition,
                        "" if share is None else round(share, 3),
                    ]
                )


def run(args: dict, out: str, *, today: dt.date | None = None) -> dict:
    """analyze() plus files; returns the result JSON (result.json is written by the
    caller with exactly what goes to stdout).
    """
    analysis = analyze(**args, today=today)
    folder = output_dir(out)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        csv_path = folder / DATA_CSV
        write_csv(analysis, csv_path)
    except OSError as exc:
        raise WdsError(
            f"cannot write to {folder}: {exc}",
            hint="pass --out with a folder you can write to",
        ) from exc
    files = {"result_json": str(folder / RESULT_JSON), "data_csv": str(csv_path)}
    return to_json(analysis, files)
