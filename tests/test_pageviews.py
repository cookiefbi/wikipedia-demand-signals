"""Pageviews requests through the real cache, with a fake network."""

import datetime as dt

import pytest
from helpers import UrlNetwork
from wds_lib import api, langs, pageviews, series

TODAY = dt.date(2026, 9, 25)
WINDOW = series.fetch_window(TODAY)
CS = langs.lookup("cs")
TITLE = "Přerušovaný půst"


def body(start: dt.date, end: dt.date, views: int = 10) -> dict:
    days = (end - start).days + 1
    return {
        "items": [
            {
                "timestamp": (start + dt.timedelta(days=i)).strftime("%Y%m%d00"),
                "views": views,
            }
            for i in range(days)
        ]
    }


def test_urls_use_the_fixed_window_and_utf8_titles():
    assert pageviews.article_url(CS, TITLE, WINDOW) == (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        "cs.wikipedia/all-access/user/P%C5%99eru%C5%A1ovan%C3%BD_p%C5%AFst/"
        "daily/20200901/20260831"
    )
    assert pageviews.edition_url(CS, WINDOW) == (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/aggregate/"
        "cs.wikipedia/all-access/user/daily/20200901/20260831"
    )


def test_404_for_an_existing_article_is_zeros(clock, network):
    network(UrlNetwork({}))
    daily = pageviews.article_daily(CS, TITLE, WINDOW)
    assert len(daily) == (WINDOW.end - WINDOW.start).days + 1
    assert daily.sum() == 0


def test_second_run_makes_no_network_requests(clock, network):
    fake = network(
        UrlNetwork(
            {
                pageviews.edition_url(CS, WINDOW): body(WINDOW.start, WINDOW.end, 1000),
                pageviews.article_url(CS, TITLE, WINDOW): body(
                    WINDOW.start, WINDOW.end
                ),
            }
        )
    )

    def run():
        window, moved = pageviews.published_window(TODAY, CS)
        assert (window, moved) == (WINDOW, False)
        pageviews.edition_daily(CS, window)
        return pageviews.article_daily(CS, TITLE, window)

    first = run()
    assert len(fake.urls) == 2
    # A month later (still the same window until Oct 1) and ten years of cache age.
    clock.now += 10 * 365 * 24 * 3600
    api.stats.update(network=0, cache=0)
    assert run().tolist() == first.tolist()
    assert api.stats["network"] == 0
    assert len(fake.urls) == 2


def test_window_moves_back_a_month_while_the_last_day_is_unpublished(clock, network):
    earlier = WINDOW.shifted(-1)
    late = body(WINDOW.start, WINDOW.end - dt.timedelta(days=1))  # no Aug 31 yet
    network(
        UrlNetwork(
            {
                pageviews.edition_url(CS, WINDOW): late,
                pageviews.edition_url(CS, earlier): body(earlier.start, earlier.end),
            }
        )
    )
    assert pageviews.published_window(TODAY, CS) == (earlier, True)


def test_unpublished_answer_is_rechecked_after_an_hour(clock, network):
    url = pageviews.edition_url(CS, WINDOW)
    fake = network(UrlNetwork({url: body(WINDOW.start, WINDOW.end - dt.timedelta(1))}))
    with pytest.raises(pageviews.NotPublishedYet):
        pageviews.edition_daily(CS, WINDOW)
    assert fake.urls == [url]  # just fetched: not asked twice in the same run

    fake.bodies[url] = body(WINDOW.start, WINDOW.end)
    clock.now += pageviews.UNPUBLISHED_RECHECK_S + 1
    assert pageviews.edition_daily(CS, WINDOW).sum() > 0
    assert fake.urls == [url, url]


def test_both_windows_unpublished_is_an_error_with_a_hint(clock, network):
    earlier = WINDOW.shifted(-1)
    network(
        UrlNetwork(
            {
                pageviews.edition_url(CS, w): body(w.start, w.end - dt.timedelta(1))
                for w in (WINDOW, earlier)
            }
        )
    )
    with pytest.raises(pageviews.NotPublishedYet) as info:
        pageviews.published_window(TODAY, CS)
    assert "rerun" in info.value.hint


def test_edition_404_is_an_error_not_zeros(clock, network, monkeypatch):
    # 404 for a whole edition is not "no interest": the project has no data at all.
    monkeypatch.setattr(api, "get_json", lambda url, *, ttl: None)
    with pytest.raises(pageviews.WdsError) as info:
        pageviews.edition_daily(CS, WINDOW)
    assert "cs.wikipedia" in info.value.error
