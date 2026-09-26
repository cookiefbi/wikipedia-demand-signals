"""Check that every number in an agent's answer appears in the skill's JSON output.

Usage: python evals/check_numbers.py ANSWER.txt RESULT.json [MORE.json ...]
Prints {"passed", "numbers", "missing"} as JSON; exit code 1 when a number is missing.

Numbers are compared by absolute value: signs and wording belong to the grader. Before
comparing, both sides are normalised to the formats an answer can use: thousands grouped by
a space, no-break space or comma (6 712, 6,712), a decimal comma (-46,4 %), the minus sign
U+2212 and "тис."/"k" suffixes. A rounded number matches when a JSON value rounds to it at the
precision the answer shows (45 % for 44.9, 97 тис. for 97042). List markers, QIDs, file
paths and links are not data and are skipped.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

# Group separators the answer may put inside a number: space, NBSP, thin and narrow NBSP.
_GROUP = " \u00a0\u2009\u202f"
_THOUSANDS = re.compile(rf"(?<=\d)[{_GROUP},](?=\d{{3}}(?!\d))")
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")
_MULTIPLIERS = {
    "тис": 1e3,
    "тыс": 1e3,
    "k": 1e3,
    "thousand": 1e3,
    "млн": 1e6,
    "million": 1e6,
}
# Not glued to a preceding letter, digit or dot (B2C, t16, v2.1); a suffix may be a whole
# word ("тисяч").
_NUMBER = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)"
    r"(?:[ \u00a0]?(тис\w*|тыс\w*|млн\w*|thousand\w*|million\w*|k\b))?"
)

_SKIP = [
    re.compile(r"\]\([^)]*\)"),  # markdown link targets
    re.compile(r"<[^<>\s]*[\\/][^<>]*>"),  # <path> link targets and autolinks
    re.compile(r"`[^`\n]*[\\/][^`\n]*`"),  # code spans holding a path
    re.compile(r"https?://\S+"),
    re.compile(r"[A-Za-z]:[\\/][^\n]*?\.(?:pdf|png|json|csv)\b"),  # paths with spaces
    re.compile(r"[A-Za-z]:[\\/]\S*"),
    re.compile(r"\S*wds-output\S*"),
    re.compile(r"\bQ\d+\b"),  # Wikidata ids
    re.compile(r"(?m)^[\s>#*-]*\d+[.)](?=\s)"),  # list markers and numbered headings
]


_APPROX = re.compile(r"[~≈]|близько|приблизно|about|around|approx", re.IGNORECASE)
_DOTTED_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")


def normalize(text: str) -> str:
    """One notation for numbers: no group separators, decimal point, ASCII minus, ISO dates."""
    text = _DOTTED_DATE.sub(r"\3-\2-\1", text.replace("\u2212", "-"))
    text = _THOUSANDS.sub("", text)
    return _DECIMAL_COMMA.sub(".", text)


def answer_numbers(answer: str) -> list[tuple[str, float, float]]:
    """(as written, value, precision) for each number the answer states as data."""
    for pattern in _SKIP:
        answer = pattern.sub(" ", answer)
    found = []
    text = normalize(answer)
    for match in _NUMBER.finditer(text):
        digits, suffix = match.groups()
        multiplier = next(
            (m for w, m in _MULTIPLIERS.items() if (suffix or "").startswith(w)), 1.0
        )
        whole, _, fraction = digits.partition(".")
        precision = 10.0 ** -len(fraction)
        # "~6200" or "about 6,200": trailing zeros are rounding, not digits
        if not fraction and _APPROX.search(
            text[max(0, match.start() - 12) : match.start()]
        ):
            precision = 10.0 ** (len(whole) - len(whole.rstrip("0")))
        found.append(
            (match.group(0), float(digits) * multiplier, precision * multiplier)
        )
    return found


def json_numbers(data: Any) -> set[float]:
    """Absolute values of every number in the JSON, including numbers inside its strings."""
    values: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool) or node is None:
            return
        if isinstance(node, int | float):
            values.add(abs(float(node)))
        elif isinstance(node, str):
            values.update(float(m.group(1)) for m in _NUMBER.finditer(normalize(node)))
        elif isinstance(node, dict):
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(data)
    return values


def check(answer: str, sources: list[Any]) -> dict:
    """Numbers of the answer that no JSON source contains, even after rounding."""
    known = (
        set().union(*(json_numbers(source) for source in sources)) if sources else set()
    )
    numbers = answer_numbers(answer)
    missing = [
        text
        for text, value, precision in numbers
        # half a unit of the shown precision, plus float noise
        if not any(abs(k - value) <= precision / 2 + 1e-9 for k in known)
    ]
    return {
        "passed": not missing,
        "numbers": [n[0] for n in numbers],
        "missing": missing,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    answer = Path(argv[1]).read_text(encoding="utf-8")
    sources = [json.loads(Path(p).read_text(encoding="utf-8")) for p in argv[2:]]
    result = check(answer, sources)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
