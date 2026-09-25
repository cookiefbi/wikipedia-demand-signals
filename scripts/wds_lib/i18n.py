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
        # confidence
        "conf.checks_passed": "checks passed: at least {min_views} views a month, "
        "{min_months}+ months of history{signs}",
        "conf.signs_agree": ", absolute and per-million growth agree",
        "conf.volume_low": "median {median:,.0f} views a month is below {min_views}: "
        "percentages this small are mostly noise",
        "conf.history_short": "only {months} months of history ({min_months} wanted)",
        "conf.history_too_short": "only {months} months of history: too short for "
        "any trend",
        "conf.signs_differ": "absolute and per-million growth point in opposite "
        "directions",
        "conf.insufficient": "confidence is low whenever growth cannot be measured",
        # warnings: problems with the data itself
        "warn.base_incomplete": "views start only in {since}: the 12 months before "
        "the last 12 are incomplete, growth not computed",
        "warn.no_views": "no views recorded in the analysed months",
        "warn.zero_base": "no views in the 12 months before the last 12: growth "
        "from zero is not computed",
        # ranking
        "rank.growth": "per-million growth {value:+.1f}%, {confidence} confidence",
        "rank.share": "{value:.2f} views per million, {confidence} confidence",
        "rank.size": "{value:,} views in the last 12 months, {confidence} confidence",
        "rank.unknown": "no value to rank by: listed last",
        "rank.low_after": "{base}; listed after confident results",
        # assumptions
        "assume.article": "{lang}: topic measured by article '{title}'",
        "assume.topic_sum": "{lang}: topic = sum of {count} articles (topic_totals)",
        "assume.no_redirects": "views arriving through redirects (other names of an "
        "article) are not included",
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
        "caveat.proxy": "one article stands for the topic: related articles and "
        "other names are not counted",
        "caveat.bots": "automated traffic is filtered (agent=user) but not "
        "perfectly: single spikes can be news or bots",
    },
}

CAVEATS = ("caveat.country", "caveat.pay", "caveat.proxy", "caveat.bots")


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
