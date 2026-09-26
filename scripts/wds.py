# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "numpy==2.4.6",
#     "matplotlib==3.11.2",
# ]
# ///
"""wikipedia-demand-signals CLI: interest in a topic across Wikipedia language editions.

Only argument parsing and output live here; all logic is in wds_lib/.
Every run prints exactly one JSON object to stdout; progress goes to stderr.
"""

import argparse
import sys
from collections.abc import Callable
from typing import NoReturn

from wds_lib import WdsError, __version__, analyze, api, dumps, langs, log, resolve

# Exit codes: the agent reads the JSON, but a non-zero code keeps shell pipelines honest.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def force_utf8_streams() -> None:
    """Make stdout/stderr UTF-8 regardless of the console code page.

    On Windows a piped stdout defaults to the ANSI code page (cp1251 etc.), where
    'ř' in 'Přerušovaný půst' raises UnicodeEncodeError.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def emit(obj: dict) -> None:
    sys.stdout.write(dumps(obj) + "\n")
    sys.stdout.flush()


class JsonArgumentParser(argparse.ArgumentParser):
    """Usage errors come out as the same JSON error object as every other failure."""

    def error(self, message: str) -> NoReturn:
        emit(
            WdsError(
                message, hint=f"see `{self.prog} --help` for the expected arguments"
            ).as_dict()
        )
        sys.exit(EXIT_USAGE)


def cmd_resolve(args: argparse.Namespace) -> dict:
    requested = langs.parse_langs(args.langs)
    search_lang = langs.parse_langs(args.search_lang)[0]
    return resolve.resolve(args.query, requested, search_lang)


def cmd_analyze(args: argparse.Namespace) -> dict:
    return analyze.run(
        {
            "qids": args.qid,
            "articles": args.article,
            "langs_arg": args.langs,
            "period": args.period,
            "date_from": args.date_from,
            "date_to": args.date_to,
            "rank_by": args.rank_by,
        },
        args.out,
        report=args.report,
        answer_lang=args.answer_lang,
        report_lang=args.report_lang,
        note=args.note,
    )


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(
        prog="wds.py",
        description="Interest in a topic across Wikipedia language editions "
        "(Wikimedia pageviews). Prints one JSON object to stdout.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "resolve",
        help="find the Wikidata item (QID) for a topic",
        description="Find up to 5 candidate Wikidata items for a topic, with the "
        "article title in each requested language.",
    )
    p.add_argument("query", help="topic in English, e.g. 'intermittent fasting'")
    p.add_argument(
        "--langs", required=True, help="comma-separated language codes or English names"
    )
    p.add_argument(
        "--search-lang",
        default="en",
        help="Wikipedia to search in (default: en); use for local topics missing in en",
    )
    p.set_defaults(handler=cmd_resolve)

    p = sub.add_parser(
        "analyze",
        help="measure interest for resolved articles",
        description="Pageviews, year-over-year growth, share of the edition's traffic "
        "and a verdict per language.",
    )
    p.add_argument(
        "--qid",
        action="append",
        default=[],
        help="Wikidata item, e.g. Q1666254 (repeat or comma-separate for several)",
    )
    p.add_argument(
        "--article",
        action="append",
        default=[],
        metavar="LANG:TITLE",
        help="article without a Wikidata item, e.g. 'pl:Głodówka lecznicza' (repeatable)",
    )
    p.add_argument(
        "--langs",
        help="comma-separated language codes or English names; required with --qid "
        "(an --article language is added automatically)",
    )
    p.add_argument(
        "--period",
        choices=["12m", "24m", "36m", "5y"],
        help="analysis period ending with the last full month (default: 24m)",
    )
    p.add_argument("--from", dest="date_from", metavar="YYYY-MM", help="period start")
    p.add_argument("--to", dest="date_to", metavar="YYYY-MM", help="period end")
    p.add_argument(
        "--rank-by",
        choices=["growth", "share", "size"],
        default="growth",
        help="how to order languages (default: growth)",
    )
    p.add_argument(
        "--answer-lang",
        choices=["en", "uk"],
        default="en",
        help="language of must_say, to pass on word for word (default: en); "
        "uk when the user writes Ukrainian",
    )
    p.add_argument("--report", action="store_true", help="also write chart PNG and PDF")
    p.add_argument(
        "--report-lang",
        choices=["en", "uk"],
        help="PDF language (default: --answer-lang)",
    )
    p.add_argument(
        "--out",
        help="output folder (default: ./wds-output/<topic>_<langs>_<period>/)",
    )
    p.add_argument(
        "--note",
        help="agent's recommendation for the PDF, in words: a word of digits and "
        "punctuation only ('22%%', '2024') is rejected; 'B2C', 'COVID-19' pass",
    )
    p.set_defaults(handler=cmd_analyze)
    return parser


def main(argv: list[str] | None = None) -> int:
    force_utf8_streams()
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], dict] = args.handler
    api.stats.update(network=0, cache=0)  # per run, also when called in-process
    code = EXIT_OK
    try:
        result = handler(args)
    except WdsError as exc:
        result, code = exc.as_dict(), EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - last resort: the agent still gets parseable JSON
        result, code = (
            {
                "status": "error",
                "error": f"unexpected {type(exc).__name__}: {exc}",
                "hint": "this is a bug in the skill; retrying will not help, "
                "tell the user the command failed",
            },
            EXIT_ERROR,
        )
    finally:
        log(f"requests: {api.stats['network']} network, {api.stats['cache']} cached")
    if args.command == "analyze":
        # Written first and on errors too: the fallback when the console garbles
        # the output, and never a stale answer from an earlier run.
        analyze.save_result(args.out, result, dumps(result))
    emit(result)
    return code


if __name__ == "__main__":
    sys.exit(main())
