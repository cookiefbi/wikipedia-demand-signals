"""evals/check_numbers.py: every number of an answer must come from the skill's JSON."""

import json

import check_numbers
import pytest

RESULT = {
    "must_say": [
        "cs: спадає (низька довіра), перегляди на мільйон -47,0\u00a0% рік до року.",
        "З 2025-03-20 Вікімедіа фільтрує ботів суворіше.",
    ],
    "results": [
        {
            "qid": "Q1666254",
            "lang": "cs",
            "views_last_12m": 2198,
            "views_prev_12m": 97042,
            "per_million_growth_pct": -47.0,
            "growth_pct": 44.9,
            "per_million_last_12m": 3.08,
        },
    ],
}


@pytest.mark.parametrize(
    "answer",
    [
        "перегляди на мільйон \u221247,0 % рік до року, 2\u00a0198 переглядів",  # uk formats
        "views per million -47.0%, 2,198 views, +44.9%",  # en formats
        "приблизно 45 % і 3 перегляди на мільйон",  # rounded to the shown precision
        "97 тис. переглядів торік, 2,2 тис. тепер",  # thousands suffix
        "з 20.03.2025 фільтр суворіший; з 2025-03-20",  # dates in both notations
    ],
)
def test_numbers_from_json_pass(answer):
    result = check_numbers.check(answer, [RESULT])
    assert result["numbers"]
    assert result["missing"] == []


def test_invented_number_is_reported():
    result = check_numbers.check("частка зросла на +15 %, 2 198 переглядів", [RESULT])
    assert result["passed"] is False
    assert result["missing"] == ["15"]


def test_rounding_needs_the_shown_precision():
    # 47.5 is not -47.0 rounded to one decimal
    assert check_numbers.check("-47,5 %", [RESULT])["missing"] == ["47.5"]


def test_trailing_zeros_are_rounding_only_when_marked_approximate():
    assert check_numbers.check("~2200 переглядів", [RESULT])["missing"] == []
    assert check_numbers.check("близько 97 000", [RESULT])["missing"] == []
    assert check_numbers.check("2200 переглядів", [RESULT])["missing"] == ["2200"]


def test_markers_ids_paths_and_codes_are_not_data():
    answer = (
        "## 1. Відповідь\n"
        "1. Стаття Q1666254, B2C, раунд t16.\n"
        "- [report.pdf](<C:\\Users\\x\\wds runs\\t16 ex1 r2\\wds-output\\report.pdf>)\n"
        "- `wds-output/intermittent-fasting_pl-cs_24m/chart.png`\n"
        "- C:\\Users\\x\\wds runs\\t16 ex1 r2\\wds-output\\a_24m\\report.pdf\n"
    )
    assert check_numbers.check(answer, [RESULT]) == {
        "passed": True,
        "numbers": [],
        "missing": [],
    }


def test_cli_exit_code(tmp_path, capsys):
    answer = tmp_path / "answer.txt"
    answer.write_text("+15 % і 2 198", encoding="utf-8")
    result = tmp_path / "result.json"
    result.write_text(json.dumps(RESULT), encoding="utf-8")
    assert check_numbers.main(["check_numbers.py", str(answer), str(result)]) == 1
    assert json.loads(capsys.readouterr().out)["missing"] == ["15"]
