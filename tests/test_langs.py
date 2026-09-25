import pytest
from wds_lib import WdsError, langs


def codes(value: str) -> list[str]:
    return [lang.code for lang in langs.parse_langs(value)]


def test_codes_map_to_projects_and_wikidata_keys():
    pl = langs.lookup("pl")
    assert (pl.project, pl.dbname, pl.name) == ("pl.wikipedia", "plwiki", "Polish")
    assert pl.api_url == "https://pl.wikipedia.org/w/api.php"


@pytest.mark.parametrize("token", ["Norwegian", "nb", "no", "NO", "Bokmål"])
def test_norwegian_variants_go_to_no_wikipedia(token):
    assert langs.lookup(token).project == "no.wikipedia"


def test_english_names_are_case_insensitive_and_trimmed():
    assert codes(" Polish, czech ,UKRAINIAN") == ["pl", "cs", "uk"]


def test_iso_codes_that_differ_from_the_domain():
    yue = langs.lookup("yue")
    assert (yue.project, yue.dbname) == ("zh-yue.wikipedia", "zh_yuewiki")
    assert langs.lookup("be-tarask").dbname == "be_x_oldwiki"


def test_order_is_kept_and_duplicates_dropped():
    assert codes("uk,pl,Polish,uk,cs") == ["uk", "pl", "cs"]


@pytest.mark.parametrize(
    ("typed", "suggested"),
    [
        ("polski", "pl"),
        ("ua", "uk"),
        ("cz", "cs"),
        ("Ukranian", "uk"),
        ("Slovac", "sk"),
    ],
)
def test_unknown_language_suggests_a_code(typed, suggested):
    with pytest.raises(WdsError) as info:
        langs.parse_langs(f"pl,{typed}")
    assert (
        f"unknown language '{typed}', did you mean '{suggested}'?" == info.value.error
    )
    assert info.value.hint


def test_nonsense_gets_no_suggestion_and_all_unknowns_are_listed():
    with pytest.raises(WdsError) as info:
        langs.parse_langs("qqqzzz,polski")
    assert "'qqqzzz'" in info.value.error and "did you mean 'pl'" in info.value.error


def test_limit_of_ten_languages():
    ten = "pl,cs,uk,sk,de,fr,es,it,pt,nl"
    assert len(codes(ten)) == langs.MAX_LANGS
    with pytest.raises(WdsError) as info:
        langs.parse_langs(ten + ",sv")
    assert "split" in info.value.hint


def test_empty_list_is_an_error():
    with pytest.raises(WdsError):
        langs.parse_langs(" , ")


def test_table_integrity():
    by_code, accepted, _ = langs._tables()
    assert len(by_code) > 300
    assert all(accepted[code] == code for code in by_code)
    # hints only for tokens that are not real editions, pointing at real ones
    assert not set(langs.COUNTRY_CODE_HINTS) & set(accepted)
    targets = [*langs.COUNTRY_CODE_HINTS.values(), *langs.EXTRA_CODES.values()]
    assert all(target in by_code for target in [*targets, *langs.EXTRA_NAMES.values()])
