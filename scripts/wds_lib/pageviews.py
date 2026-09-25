"""Wikimedia Pageviews API: daily views of an article and of a whole language edition.

Always the fixed, month-aligned window from series.fetch_window and always daily
data: months are summed by us (the API's monthly granularity includes the
incomplete current month), and daily values are what spike detection needs.
"""

import datetime as dt

import numpy as np

from wds_lib import WdsError, api, log, series
from wds_lib.langs import Lang
from wds_lib.series import Span

PAGEVIEWS_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews"

# Human readers on desktop, mobile web and apps. Crawlers ("spider") and traffic
# the API recognises as automated are left out.
ACCESS = "all-access"
AGENT = "user"

# A window's answers are cached forever. An edition answer fetched before Wikimedia
# published the window's last day must not stay that way: it is asked again, at
# most once per this interval, until the day appears.
UNPUBLISHED_RECHECK_S = 3600


class NotPublishedYet(WdsError):
    def __init__(self, day: dt.date) -> None:
        super().__init__(
            f"Wikimedia has not published pageviews for {day} yet",
            hint="pageviews are usually a day behind; rerun the same command in a few "
            "hours, or pass --to with an earlier month",
        )


def _stamp(day: dt.date) -> str:
    return day.strftime("%Y%m%d")


def article_url(lang: Lang, title: str, window: Span) -> str:
    return (
        f"{PAGEVIEWS_API}/per-article/{lang.project}/{ACCESS}/{AGENT}/"
        f"{api.quote_title(title)}/daily/{_stamp(window.start)}/{_stamp(window.end)}"
    )


def edition_url(lang: Lang, window: Span) -> str:
    return (
        f"{PAGEVIEWS_API}/aggregate/{lang.project}/{ACCESS}/{AGENT}/"
        f"daily/{_stamp(window.start)}/{_stamp(window.end)}"
    )


def _items(body: dict | None) -> list[dict] | None:
    return None if body is None else body.get("items", [])


def _covers(body: dict | None, day: dt.date) -> bool:
    items = _items(body)
    return bool(items) and items[-1]["timestamp"][:8] >= _stamp(day)


def article_daily(lang: Lang, title: str, window: Span) -> np.ndarray:
    """Zero-filled daily views. For an article known to exist, 404 means no views.

    The API answers 404 for a title that does not exist too, so existence must be
    settled before (Wikidata sitelink or a MediaWiki lookup), never from here.
    """
    body = api.get_json(article_url(lang, title, window), ttl=api.TTL_FOREVER)
    return series.daily_counts(_items(body), window.start, window.end)


def edition_daily(lang: Lang, window: Span) -> np.ndarray:
    """Zero-filled daily views of the whole edition (all articles, human traffic)."""
    url = edition_url(lang, window)
    body = api.get_json(url, ttl=api.TTL_FOREVER)
    if body is None:
        raise WdsError(
            f"the Pageviews API has no data for {lang.project}",
            hint=f"'{lang.code}' may be too new or closed; drop it from --langs",
        )
    if not _covers(body, window.end):
        body = api.get_json(url, ttl=UNPUBLISHED_RECHECK_S)
    if not _covers(body, window.end):
        raise NotPublishedYet(window.end)
    return series.daily_counts(_items(body), window.start, window.end)


def published_window(today: dt.date, lang: Lang) -> tuple[Span, bool]:
    """(fixed window, whether it had to move one month back).

    The window ends with the last month whose last day is published. The date
    arithmetic in series.last_full_month is only an estimate: if the pipeline is
    late, the edition answer shows it, and the window moves back a month.
    """
    window = series.fetch_window(today)
    try:
        edition_daily(lang, window)
    except NotPublishedYet:
        log(f"{window.end} not published yet; using the window one month earlier")
        window = window.shifted(-1)
        edition_daily(lang, window)
        return window, True
    return window, False
