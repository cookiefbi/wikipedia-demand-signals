"""Every sentence the skill shows to people, as templates keyed by message id.

Verdict code never builds sentences itself: it returns Msg(key, params). The JSON
for the agent gets the English text; the PDF renders the same messages in the
report language, so a Ukrainian report never contains English reasons.
"""

from typing import NamedTuple

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        # trend: one signal, normalized year-over-year growth
        "trend.rising": "views per million edition views {pm_growth:+.1f}% year over "
        "year (+{threshold:g}% or more counts as rising)",
        "trend.falling": "views per million edition views {pm_growth:+.1f}% year over "
        "year (-{threshold:g}% or less counts as falling)",
        "trend.flat": "views per million edition views {pm_growth:+.1f}% year over "
        "year (within ±{threshold:g}% counts as flat)",
        "trend.insufficient": "no year-over-year growth, so no trend",
        "signs.differ": "absolute views {raw_dir} {raw_abs:.1f}% while the whole "
        "edition {edition_dir} {edition_abs:.1f}%",
        "dir.grew": "grew",
        "dir.fell": "fell",
        "dir.above": "above",
        "dir.below": "below",
        "dir.higher": "higher",
        "dir.lower": "lower",
        # confidence
        "conf.checks_passed": "checks passed: at least {min_views} views a month, "
        "{min_months}+ months of history, no one-off spikes or data warnings{signs}",
        "conf.signs_agree": ", absolute and per-million growth agree",
        "conf.volume_low": "median {median:,.0f} views a month is below {min_views}: "
        "percentages this small are mostly noise",
        "conf.history_short": "only {months} months of history ({min_months} wanted)",
        "conf.history_too_short": "only {months} months of history: too short for "
        "any trend",
        "conf.insufficient": "confidence is low whenever growth cannot be measured",
        # month by month: each of the last 12 months against the same month a year
        # earlier (seasonal Mann-Kendall; typical month = seasonal Theil-Sen)
        "conf.steady": "steady: {count} of {of} months are {dir} the same month a "
        "year earlier{typical}",
        "conf.unsteady": "not steady: only {count} of {of} months are {dir} the same "
        "month a year earlier{typical}: the change may rest on a few months",
        "conf.disagree": "signals disagree: the 12-month total {total_dir}, but "
        "{count} of {of} months are {dir} the same month a year earlier{typical}",
        "conf.no_drift": "no steady drift: {higher} of {of} months are above the same "
        "month a year earlier, {lower} below{typical}",
        "conf.drift": "slow steady drift: {count} of {of} months are {dir} the same "
        "month a year earlier{typical}, though the 12-month total moved less than "
        "{threshold:g}%",
        "conf.typical": " (typical month {pct:+.1f}%)",
        "conf.spike": "one-off spike in {month}: {ratio:.1f}x the months around it, "
        "with no such peak in {pair}; it inflates {effect}",
        "spike.last": "the last 12 months, so growth looks higher",
        "spike.base": "the 12 months before the last 12, so growth looks lower",
        "conf.level_change": "a sharp lasting change of level (see warnings) makes "
        "the growth compare two different levels",
        "conf.redirects_capped": "not every redirect is counted (see warnings): "
        "views may be undercounted",
        # warnings: problems with the data itself
        "warn.base_incomplete": "article created {created}: the 12 months before "
        "the last 12 are incomplete, growth not computed",
        "warn.redirects_capped": "'{title}': {total} redirects lead to it, only "
        "{counted} are counted (redirects to the whole article first, oldest "
        "first): views may be undercounted",
        "warn.level_change": "sharp lasting change of level around {month}: views "
        "per million are {factor:.1f}x {dir} in the {months} months from then on "
        "than in the {months} before (medians); possibly a rename or merge that "
        "redirects do not cover, a change in how views are counted, or a long news "
        "event",
        "warn.no_views": "no views recorded in the analysed months",
        "warn.zero_base": "no views in the 12 months before the last 12: growth "
        "from zero is not computed",
        # ranking: the metric is named with its value, as in the trend reason
        "rank.growth": "views per million {value:+.1f}% year over year, {confidence} "
        "confidence",
        "rank.share": "{value:.2f} views per million edition views in the last 12 "
        "months, {confidence} confidence",
        "rank.size": "{value:,} views in the last 12 months, {confidence} confidence",
        "rank.unknown": "{metric} not measured (see warnings): listed last",
        "rank.low_after": "{base}: listed after confident results",
        "rank.low_outscores": "{base}: higher than {langs} by this measure, but "
        "listed after confident results",
        # must_say: 3-5 sentences the agent passes on point by point (SPEC 3)
        "must.main": "Ranked by {by} — {items}.",
        "must.main_one": "{items}.",
        "must.item.growth": "{lang}: {trend}, views per million {value:+.1f}% year "
        "over year",
        "must.item.share": "{lang}: {trend}, {value:.2f} views per million edition "
        "views in the last 12 months",
        "must.item.size": "{lang}: {trend}, {value:,} views in the last 12 months",
        "must.item.unknown": "{lang}: {trend}",
        "must.low": "{item} (low confidence)",
        "must.confidence": "{langs}: {level} confidence — {reason}.",
        "must.checks_passed": "checks passed",
        "must.no_article": "{langs}: no article on this topic — little local "
        "coverage; interest there cannot be measured this way.",
        "must.limits": "A language edition is not a country (its readers live in "
        "many countries), and interest is not willingness to pay: views show "
        "curiosity, not demand for a product.",
        "must.bot_rules": "Wikimedia filters bots more strictly since {day} and did "
        "not reprocess earlier data, so growth across that date partly reflects the "
        "rule change.",
        "must.proxy": "One article (with its redirects) stands for the topic: "
        "related articles are not counted.",
        "must.proxy_sum": "The chosen articles (with their redirects) stand for the "
        "topic: related articles are not counted.",
        # assumptions
        "assume.lang_added": "{lang} added to the compared languages for --article "
        "'{article}'",
        "assume.article": "{lang}: topic measured by article '{title}'{redirects}"
        "{created}",
        "assume.redirects_one": " (+1 redirect)",
        "assume.redirects_many": " (+{count} redirects)",
        "assume.created": ", created {day}: earlier days are not counted",
        "assume.topic_sum": "{lang}: topic = sum of {count} articles (topic_totals)",
        "assume.redirects": "views include up to {max_redirects} redirects per "
        "article (its other and former titles; data.csv lists them apart) and start "
        "at the article's first edit",
        "assume.traffic": "views = human traffic (agent=user), desktop + mobile web + "
        "apps",
        "assume.growth": "growth = last 12 months ({last_from}..{last_to}) vs the 12 "
        "before ({prev_from}..{prev_to})",
        "assume.per_million": "per million = views per million views of the whole "
        "language edition: removes edition size and Wikipedia-wide traffic changes",
        "assume.period_end": "the period ends with the last complete month ({to}); "
        "the running month is left out",
        "assume.window_moved": "Wikimedia has not published {day} yet, so the period "
        "ends one month earlier",
        # caveats: always the same
        "caveat.country": "language edition ≠ country: an edition's readers live in "
        "many countries, and many people read the English edition instead",
        "caveat.pay": "interest ≠ willingness to pay: views show curiosity, not "
        "demand for a product",
        "caveat.proxy": "one article (with its redirects) stands for the topic: "
        "related articles are not counted",
        "caveat.bots": "automated traffic is filtered (agent=user) but not "
        "perfectly: single spikes can be news or bots",
        "caveat.bot_rules": "Wikimedia filters bots more strictly since 2025-03-20 "
        "(earlier data were not reprocessed): growth across that date partly "
        "reflects the rule change",
        # report (PDF and PNG)
        "report.kicker": "Interest in a topic across Wikipedia language editions",
        "report.subtitle": "{items} · {langs} · {first} to {last} ({months} months) "
        "· ranked by {rank_by}",
        "rank_by.growth": "growth of share",
        "rank_by.share": "share of edition views",
        "rank_by.size": "audience size",
        "col.lang": "Language",
        "col.article": "Article",
        "col.views": "Views, 12 mo",
        "col.growth": "Growth",
        "col.per_million": "Per million",
        "col.pm_growth": "Share growth",
        "col.trend": "Trend",
        "col.confidence": "Confidence",
        "row.total": "topic total ({count} articles)",
        "row.no_article": "no article",
        "row.no_article_item": "no article on '{item}'",
        "row.more": "... {count} more rows in data.csv and result.json",
        "trend_name.rising": "rising",
        "trend_name.falling": "falling",
        "trend_name.flat": "flat",
        "trend_name.insufficient_data": "insufficient data",
        "conf_name.high": "high",
        "conf_name.medium": "medium",
        "conf_name.low": "low",
        "chart.per_million": "Views per million views of the edition, by month",
        "chart.views": "Views per month",
        "chart.more_langs": "The chart shows the first {shown} languages; all are "
        "in the table.",
        "section.verdicts": "Verdicts",
        "section.note": "Assistant's note",
        "section.assumptions": "Assumptions",
        "section.caveats": "Caveats",
        "verdict.head": "{rank}. {lang}: {trend}, {confidence} confidence",
        "verdict.no_article": "{lang}: no article on this topic, which is itself a "
        "signal of low local coverage",
        "report.truncated": "... more in result.json",
        "report.footer": "Source: Wikimedia Pageviews API (human traffic, all "
        "devices) and Wikidata. Generated {date} by wikipedia-demand-signals "
        "{version}. All numbers and verdicts are computed by code; only the "
        "assistant's note is written by the assistant.",
    },
}

CAVEATS = (
    "caveat.country",
    "caveat.pay",
    "caveat.proxy",
    "caveat.bots",
    "caveat.bot_rules",
)


class Msg(NamedTuple):
    key: str
    params: dict | None = None

    def render(self, lang: str = "en") -> str:
        return render(self.key, self.params, lang)


def render(key: str, params: dict | None = None, lang: str = "en") -> str:
    """Fill a template; a parameter that is itself a Msg is rendered in `lang` too."""
    template = TEXTS[lang].get(key) or TEXTS["en"][key]
    filled = {
        name: value.render(lang) if isinstance(value, Msg) else value
        for name, value in (params or {}).items()
    }
    return template.format(**filled)


def texts(messages: list[Msg], lang: str = "en") -> list[str]:
    return [message.render(lang) for message in messages]
