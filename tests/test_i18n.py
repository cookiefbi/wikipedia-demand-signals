"""Templates: every language has every key, with the same placeholders."""

import re
import string

import pytest
from wds_lib import i18n
from wds_lib.i18n import NBSP, Msg

OTHER_LANGS = [lang for lang in i18n.TEXTS if lang != "en"]

# Latin words a Ukrainian text may keep: API names, file names, JSON fields.
LATIN_ALLOWED = {
    "agent",
    "user",
    "data",
    "csv",
    "result",
    "json",
    "topic_totals",
    "article",
    "Wikimedia",
    "Pageviews",
    "API",
    "Wikidata",
    "wikipedia",
    "demand",
    "signals",
}


def fields(template: str) -> set[tuple[str, str]]:
    """{(name, format spec)} of a template's placeholders."""
    return {
        (name, spec)
        for _, name, spec, _ in string.Formatter().parse(template)
        if name is not None
    }


@pytest.mark.parametrize("lang", OTHER_LANGS)
def test_every_key_in_every_language_with_the_same_placeholders(lang):
    en, other = i18n.TEXTS["en"], i18n.TEXTS[lang]
    assert set(other) == set(en)
    for key, template in en.items():
        assert fields(other[key]) == fields(template), key


def test_a_missing_key_is_an_error_not_english(monkeypatch):
    monkeypatch.delitem(i18n.TEXTS["uk"], "must.low")
    with pytest.raises(KeyError):
        i18n.render("must.low", lang="uk")


def test_ukrainian_templates_leave_no_english_words():
    for key, template in i18n.TEXTS["uk"].items():
        text = re.sub(r"\{[^}]*\}", "", template)
        latin = set(re.findall(r"[A-Za-z_]+", text))
        assert latin <= LATIN_ALLOWED, (key, latin - LATIN_ALLOWED)


def test_ukrainian_numbers_use_a_decimal_comma_and_no_break_spaces():
    assert i18n.number(1904394, ",", "uk") == f"1{NBSP}904{NBSP}394"
    assert i18n.number(-8.0, "+.1f", "uk") == "-8,0"
    assert i18n.number(705.14, ",.2f", "uk") == "705,14"
    assert i18n.render("num.pct", {"value": 523.5}, "uk") == f"+523,5{NBSP}%"
    # English keeps Python's own format
    assert i18n.number(1904394, ",", "en") == "1,904,394"
    assert i18n.render("num.pct", {"value": 523.5}) == "+523.5%"


def test_only_numbers_change_format_not_texts_with_digits():
    params = {
        "title": "Karel III. Britský",
        "total": "11",
        "counted": 10,
    }
    text = i18n.render("warn.redirects_capped", params, "uk")
    assert text.startswith("«Karel III. Britský»: перенаправлень на статтю 11,")
    spike = i18n.render(
        "conf.spike",
        {
            "month": "2025-11",
            "ratio": 2.8,
            "pair": "2024-11",
            "effect": Msg("spike.last"),
        },
        "uk",
    )
    assert spike.startswith("одноразовий сплеск у 2025-11: у 2,8 раза вище")
    # a number inside a nested message is formatted in that message's language too
    typical = Msg("conf.typical", {"pct": -47.2})
    params = {"count": 10, "of": 12, "dir": Msg("dir.below"), "typical": typical}
    assert i18n.render("conf.steady", params, "uk") == (
        f"стійко: 10 з 12 місяців нижчі за той самий місяць рік тому (типовий "
        f"місяць -47,2{NBSP}%)"
    )
