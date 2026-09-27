"""Run the evals in evals.json on Haiku with and without the skill, grade them, report.

    python evals/run_evals.py run    --out "C:/Users/<user>/wds runs/t16"
    python evals/run_evals.py grade  --out "C:/Users/<user>/wds runs/t16"
    python evals/run_evals.py report --out "C:/Users/<user>/wds runs/t16"

Conditions (SPEC section 7):
- every run is a new, clean folder with spaces in its path: only the skill junction
  (with_skill) or nothing (without_skill), no wds-output/ of earlier runs;
- an isolated Claude Code config: `--setting-sources project` leaves out the developer's
  personal skills, plugins and settings, `--strict-mcp-config` their MCP servers and
  connectors; the login stays, the built-in skills stay (a normal user has them too);
- the skill's four permission rules come with `--allowedTools`, the same in both configs;
- a multi-turn eval is one session: `--session-id` for turn 1, then `--resume`.

`run` also writes the reference JSON of each turn (evals.json "reference"), which warms
the cache before the parallel runs. `grade` checks commands, flags, files and numbers by
script and the rest with an LLM grader (claude -p, Sonnet, structured output).
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import check_numbers

REPO = Path(__file__).resolve().parent.parent
EVALS = json.loads((REPO / "evals" / "evals.json").read_text(encoding="utf-8"))
SKILL_NAME = EVALS["skill_name"]
# without_skill_web: the baseline allowed to read the web (WebFetch, WebSearch), as a user
# who approves those prompts; it shows whether Haiku then fetches or invents numbers.
CONFIGS = ("with_skill", "without_skill", "without_skill_web")
WEB_TOOLS = ["WebFetch", "WebSearch"]
TURN_TIMEOUT_S = 900
GRADER_MODEL = "sonnet"
# `--period` options in years and months: "a longer period, 3–5 years" is not a data number.
PERIOD_OPTIONS = {"period_options": [1, 2, 3, 5, 12, 24, 36, 60]}

# Topic resolves the agent may make besides the references, run once to warm the cache.
WARMUP = [
    ["resolve", "intermittent fasting", "--langs", "pl,cs"],
    ["resolve", "intermittent fasting", "--langs", "pl,cs,sk"],
    ["resolve", "intermittent fasting", "--langs", "pl", "--search-lang", "pl"],
    ["resolve", "intermittent fasting", "--langs", "sk", "--search-lang", "sk"],
    ["resolve", "astronomy", "--langs", "uk"],
    ["resolve", "astronomy", "--langs", "pl,cs,uk"],
    ["resolve", "learning English", "--langs", "uk,pl,cs"],
    ["resolve", "English language learning", "--langs", "uk,pl,cs"],
    ["resolve", "English as a second or foreign language", "--langs", "uk,pl,cs"],
    ["resolve", "English language", "--langs", "uk,pl,cs"],
    ["resolve", "Java", "--langs", "pl,cs"],
    ["analyze", "--qid", "Q44602", "--langs", "pl,cs", "--answer-lang", "uk"],
    ["analyze", "--qid", "Q1860", "--langs", "uk,pl,cs", "--answer-lang", "uk"],
]


def claude_exe() -> str:
    """The Claude Code bundled with the desktop app (newest version), else `claude` on PATH."""
    if os.environ.get("WDS_EVAL_CLAUDE"):
        return os.environ["WDS_EVAL_CLAUDE"]
    root = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude-code"
    versions = sorted(
        (p for p in root.glob("*/claude.exe")),
        key=lambda p: [int(x) for x in re.findall(r"\d+", p.parent.name)],
    )
    return str(versions[-1]) if versions else "claude"


def clean_env() -> dict[str, str]:
    """Without the variables of the Claude session that started us (they cause 401s)."""
    return {
        k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))
    }


def run_dir(out: Path, name: str, config: str, run: int) -> Path:
    return out / "runs" / f"{name} {config} r{run}"


def log_dir(out: Path, name: str, config: str, run: int) -> Path:
    return out / "logs" / name / config / f"r{run}"


def allowed_tools(cwd: Path, config: str) -> list[str]:
    skill = f"{cwd.as_posix()}/.claude/skills/{SKILL_NAME}"
    web = WEB_TOOLS if config == "without_skill_web" else []
    return [
        *web,
        f"Skill({SKILL_NAME})",
        f"Skill({SKILL_NAME} *)",
        f'Bash(uv run "{skill}/scripts/wds.py" *)',
        f'Bash(python -m uv run "{skill}/scripts/wds.py" *)',
    ]


def wds(args: list[str], cwd: Path) -> dict:
    """Run the skill's CLI directly and return its JSON."""
    cmd = [sys.executable, "-m", "uv", "run", str(REPO / "scripts" / "wds.py"), *args]
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, encoding="utf-8", timeout=600, check=False
    )
    return json.loads(proc.stdout)


# ---------------------------------------------------------------- run


def prepare_references(out: Path) -> None:
    ref_dir = out / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)
    for args in WARMUP:
        status = wds(args, ref_dir).get("status")
        print(f"warmup {' '.join(args)}: {status}", flush=True)
    for ev in EVALS["evals"]:
        for n, turn in enumerate(ev["turns"], 1):
            if turn["reference"]:
                data = wds(turn["reference"], ref_dir)
                path = ref_dir / f"{ev['name']}-t{n}.json"
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
                )
                print(f"reference {path.name}: {data.get('status')}", flush=True)


def make_run_dir(cwd: Path, config: str) -> None:
    if cwd.exists():
        raise SystemExit(f"{cwd} exists: every run needs a new, clean folder")
    cwd.mkdir(parents=True)
    if config == "with_skill":
        skills = cwd / ".claude" / "skills"
        skills.mkdir(parents=True)
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(skills / SKILL_NAME), str(REPO)],
            check=True,
            capture_output=True,
        )


def run_session(out: Path, ev: dict, config: str, run: int) -> str:
    cwd = run_dir(out, ev["name"], config, run)
    logs = log_dir(out, ev["name"], config, run)
    make_run_dir(cwd, config)
    logs.mkdir(parents=True, exist_ok=True)
    session = str(uuid.uuid4())
    base = [
        claude_exe(),
        "-p",
        "--model",
        "haiku",
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    for n, turn in enumerate(ev["turns"], 1):
        cmd = [*base, *(["--session-id", session] if n == 1 else ["--resume", session])]
        cmd += ["--allowedTools", *allowed_tools(cwd, config)]
        started = time.time()
        with (
            open(logs / f"turn{n}.jsonl", "w", encoding="utf-8") as stdout,
            open(logs / f"turn{n}.err", "w", encoding="utf-8") as stderr,
        ):
            try:
                code = subprocess.run(
                    cmd,
                    cwd=cwd,
                    env=clean_env(),
                    input=turn["prompt"],
                    stdout=stdout,
                    stderr=stderr,
                    encoding="utf-8",
                    timeout=TURN_TIMEOUT_S,
                    check=False,
                ).returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        meta = {
            "session": session,
            "started": started,
            "ended": time.time(),
            "exit": code,
        }
        (logs / f"turn{n}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        if code != 0:
            return f"{ev['name']} {config} r{run}: turn {n} exit {code}"
    return f"{ev['name']} {config} r{run}: ok"


def cmd_run(args: argparse.Namespace) -> None:
    out = Path(args.out)
    if not args.skip_references:
        prepare_references(out)
    jobs = [
        (ev, config, run)
        for run in range(1, max(args.runs, args.web_runs) + 1)
        for ev in EVALS["evals"]
        if not args.only or ev["name"] in args.only
        for config in args.configs
        if run <= (args.web_runs if config == "without_skill_web" else args.runs)
    ]
    with ThreadPoolExecutor(args.workers) as pool:
        for line in pool.map(lambda job: run_session(out, *job), jobs):
            print(line, flush=True)


# ---------------------------------------------------------------- parse a turn

WDS_CALL = re.compile(r"wds\.py\"?\s+(resolve|analyze)\b")


def flag(command: str, name: str) -> str | None:
    match = re.search(rf"--{name}[ =](\"[^\"]*\"|'[^']*'|\S+)", command)
    return match.group(1).strip("\"'") if match else None


def first_json(text: str) -> dict | None:
    """The JSON object in a tool result: stdout after the [wds] progress lines."""
    for match in re.finditer(r"(?m)^\{", text):
        try:
            return json.JSONDecoder().raw_decode(text[match.start() :])[0]
        except json.JSONDecodeError:
            continue
    return None


def parse_turn(path: Path) -> dict:
    """Commands, wds outputs, the final answer and costs of one `claude -p` turn."""
    calls: list[dict] = []
    by_id: dict[str, dict] = {}
    tools: dict[str, int] = {}
    errors: dict[str, int] = {}
    names: dict[str, str] = {}
    turn = {"calls": calls, "answer": "", "skill_invoked": False, "denials": []}
    turn |= {"tools": tools, "tool_errors": errors}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "assistant":
            for block in event["message"].get("content", []):
                if block.get("type") != "tool_use":
                    continue
                if block["name"] == "Skill":
                    turn["skill_invoked"] = True
                tools[block["name"]] = tools.get(block["name"], 0) + 1
                names[block["id"]] = block["name"]
                command = block.get("input", {}).get("command", "")
                match = WDS_CALL.search(command)
                if block["name"] in ("Bash", "PowerShell") and match:
                    call = {"kind": match.group(1), "command": command, "json": None}
                    calls.append(call)
                    by_id[block["id"]] = call
        elif kind == "user":
            content = event.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                call = by_id.get(block.get("tool_use_id", ""))
                if block.get("type") == "tool_result" and block.get("is_error"):
                    name = names.get(block.get("tool_use_id", ""), "?")
                    errors[name] = errors.get(name, 0) + 1
                if block.get("type") == "tool_result" and call:
                    body = block.get("content")
                    if isinstance(body, list):
                        body = "".join(
                            b.get("text", "") for b in body if isinstance(b, dict)
                        )
                    call["json"] = first_json(str(body))
        elif kind == "result":
            turn["answer"] = event.get("result") or ""
            turn["denials"] = [
                d.get("tool_name") for d in event.get("permission_denials", [])
            ]
            turn["cost_usd"] = event.get("total_cost_usd")
            turn["duration_s"] = round(event.get("duration_ms", 0) / 1000)
            turn["model_turns"] = event.get("num_turns")
    return turn


# ---------------------------------------------------------------- script checks


def is_topic_resolve(call: dict) -> bool:
    """A resolve of the topic; with --search-lang it is a local search for one edition."""
    return call["kind"] == "resolve" and flag(call["command"], "search-lang") is None


def check_chain(chain: str, calls: list[dict]) -> bool:
    kinds = [
        "local search"
        if c["kind"] == "resolve" and not is_topic_resolve(c)
        else c["kind"]
        for c in calls
    ]
    analyzed = "analyze" in kinds
    if chain == "resolve->analyze":
        return (
            analyzed
            and "resolve" in kinds
            and kinds.index("resolve") < kinds.index("analyze")
        )
    if chain == "analyze":
        return analyzed and "resolve" not in kinds
    if chain == "any->analyze":
        return analyzed
    if chain == "resolve, no analyze":
        return "resolve" in kinds and not analyzed
    return not analyzed  # "no analyze"


def check_params(expect: dict, calls: list[dict]) -> bool | None:
    analyzes = [c for c in calls if c["kind"] == "analyze"]
    keys = ("qid", "qid_not", "langs", "rank_by", "report")
    wanted = {k: expect[k] for k in keys if k in expect}
    if not wanted:
        return None
    if not analyzes:
        return False
    last = analyzes[-1]["command"]
    qids = set(re.split(r"[ ,]", flag(last, "qid") or ""))
    ok = True
    if "qid" in wanted:
        ok &= bool(qids & set(wanted["qid"]))
    if "qid_not" in wanted:
        ok &= not qids & set(wanted["qid_not"])
    if "langs" in wanted:
        langs = (flag(last, "langs") or "").split(",")
        ok &= sorted(langs) == sorted(wanted["langs"])
    if "rank_by" in wanted:
        ok &= flag(last, "rank-by") == wanted["rank_by"]
    if "report" in wanted:
        ok &= bool(re.search(r"--report(?![\w-])", last)) == wanted["report"]
    return ok


def check_answer_lang(expected: str, calls: list[dict]) -> bool | None:
    """Every analyze call of the turn speaks the user's language (default en)."""
    analyzes = [c["command"] for c in calls if c["kind"] == "analyze"]
    if not analyzes:
        return None
    for command in analyzes:
        answer_lang = flag(command, "answer-lang") or "en"
        report_lang = flag(command, "report-lang") or answer_lang
        if answer_lang != expected or (
            re.search(r"--report(?![\w-])", command) and report_lang != expected
        ):
            return False
    return True


def pdf_written(cwd: Path, meta: dict) -> bool:
    """A PDF written during the turn, in the run folder or wds-output/ (not the skill junction)."""
    pdfs = [*cwd.glob("*.pdf"), *(cwd / "wds-output").rglob("*.pdf")]
    return any(meta["started"] <= p.stat().st_mtime <= meta["ended"] + 1 for p in pdfs)


def result_files(cwd: Path, until: float) -> list[dict]:
    """result.json files the skill wrote in this session up to the end of a turn."""
    found = []
    for path in (
        (cwd / "wds-output").rglob("result.json")
        if (cwd / "wds-output").exists()
        else []
    ):
        if path.stat().st_mtime <= until + 1:
            found.append(json.loads(path.read_text(encoding="utf-8")))
    return found


# ---------------------------------------------------------------- LLM grader

GRADER_SCHEMA = {
    "type": "object",
    "properties": {
        "must_say": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "point": {"type": "integer"},
                    "delivered": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
                "required": ["point", "delivered", "evidence"],
            },
        },
        "clarifying_questions": {"type": "integer"},
        "clarification_ok": {"type": "boolean"},
        "clarification_evidence": {"type": "string"},
        "no_article_offer": {"type": "boolean"},
        "substitute_offered": {"type": "boolean"},
        "local_article_offered": {"type": "boolean"},
        "no_article_evidence": {"type": "string"},
        "edition_not_country": {"type": "boolean"},
        "country_evidence": {"type": "string"},
        "reply_language_ok": {"type": "boolean"},
    },
    "required": [
        "reply_language_ok",
        "substitute_offered",
        "must_say",
        "clarifying_questions",
        "clarification_ok",
        "clarification_evidence",
        "no_article_offer",
        "local_article_offered",
        "no_article_evidence",
        "edition_not_country",
        "country_evidence",
    ],
}

GRADER_PROMPT = """You grade one reply of an AI assistant that answers questions about interest \
in topics across Wikipedia language editions. Judge only what the reply says; be strict \
and quote the reply as evidence. Replies may be in Ukrainian or English.

Conversation so far (user messages; earlier assistant replies shortened):
{history}

THE REPLY TO GRADE:
<<<
{answer}
>>>

1. must_say. The data tool produced these key points for this reply (numbered from 1). \
For each point: delivered = the reply states the same facts with the same meaning — the same \
language edition(s), the same trend word and confidence level, each number with the same \
metric (views vs views per million) and direction, negations kept. Translation, paraphrase, \
reordering or merging sentences are fine if nothing is lost or changed. Missing, softened or \
distorted = not delivered.
{must_say}

2. Clarifying questions: count the questions the reply asks that the user must answer before \
the requested analysis can be done (which language editions, which meaning of a name, \
permission to install something). Offers of optional next steps ("Want me to add Slovak?") \
do not count. Expected here: {expected_question}. clarification_ok = {question_rule}

3. no_article_offer (only meaningful if some edition has no article on the topic: {no_article}): \
true if the reply offers at least one substitute to measure such an edition (a broader \
concept, or a closer local article in that edition) AND says concretely how the substitute's \
meaning differs from the user's topic: what it covers beyond or instead of it (e.g. religious \
fasting as well, the language in general rather than learning it). Merely calling it \
"broader", "general" or "related" is not enough. substitute_offered (the milder test): true \
if the reply offers a substitute for such an edition (a broader concept or a separate local \
article, named or not, e.g. "say so and I will look for one") AND warns that its meaning \
differs from the topic; a general warning is enough. local_article_offered: true if it names \
a specific article of that edition that is closer to the user's topic than the broader \
concept (e.g. a Polish-only article shown with --article); the local title of the broader \
concept itself does not count. If not meaningful, answer true, true, false.

4. edition_not_country: false if the reply attributes interest, audience, growth, potential or \
a market to a country or nation ("Poland has potential", "у Чехії інтерес падає", "польський \
ринок") instead of the language edition or its readers. Mentioning countries only to explain \
that an edition is not a country is fine; "Polish Wikipedia" / "польськомовна Вікіпедія" is \
correct. If the reply has no analysis at all, answer true.

5. reply_language_ok: true if the reply is written in the language of the user's messages \
({user_lang}); article titles, quoted data and technical terms in other languages are fine.
"""

QUESTION_RULES = {
    "languages": "true only if there is exactly one clarifying question and it proposes a "
    "concrete set of language editions the user can accept with a yes",
    "meaning": "true only if there is exactly one clarifying question and it lists the "
    "possible meanings of the name",
    None: "true only if there are no clarifying questions",
}


def grade_with_llm(prompt: str, cwd: Path, cache: Path) -> dict:
    """The grader's structured verdict; cached per turn, so `grade` reruns cost nothing."""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached["prompt_sha256"] == digest:
            return cached["result"]
    cmd = [
        claude_exe(),
        "-p",
        "--model",
        GRADER_MODEL,
        "--tools",
        "",
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(GRADER_SCHEMA),
    ]
    for _ in range(3):
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=clean_env(),
            input=prompt,
            capture_output=True,
            encoding="utf-8",
            timeout=600,
            check=False,
        )
        try:
            result = json.loads(proc.stdout)["structured_output"]
        except (json.JSONDecodeError, KeyError, TypeError):
            time.sleep(5)
            continue
        record = {"prompt_sha256": digest, "model": GRADER_MODEL, "result": result}
        cache.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return result
    raise RuntimeError(f"grader failed: {proc.stderr[-500:]}")


def shorten(text: str, limit: int = 600) -> str:
    return text if len(text) <= limit else text[:limit] + " …"


# ---------------------------------------------------------------- grade


def grade_session(out: Path, ev: dict, config: str, run: int) -> list[dict]:
    cwd = run_dir(out, ev["name"], config, run)
    logs = log_dir(out, ev["name"], config, run)
    grades, sources, history, earlier_calls = [], [], [], []
    skill = config == "with_skill"
    for n, turn in enumerate(ev["turns"], 1):
        expect = turn["expect"]
        meta_path = logs / f"turn{n}.meta.json"
        if not meta_path.exists():
            break
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        parsed = parse_turn(logs / f"turn{n}.jsonl")
        calls = parsed["calls"]
        reference = None
        if turn["reference"]:
            ref_path = out / "references" / f"{ev['name']}-t{n}.json"
            reference = json.loads(ref_path.read_text(encoding="utf-8"))
        analyzed = [
            c["json"]
            for c in calls
            if c["kind"] == "analyze" and c["json"] and c["json"].get("status") == "ok"
        ]
        if skill:
            sources += [c["json"] for c in calls if c["json"]]
            truth_json = analyzed[-1] if analyzed else reference
            all_sources = sources + result_files(cwd, meta["ended"])
        else:
            truth_json = reference
            if reference:
                sources.append(reference)
            all_sources = list(sources)
        # The turn's analysis was already done in an earlier turn (e.g. no question was asked
        # and turn 1 analysed right away): judge that in the earlier turn, not twice.
        done_before = (
            skill
            and not analyzed
            and expect["asks"] is None
            and bool(check_params(expect, earlier_calls))
        )
        earlier_calls += calls
        answered = (expect["asks"] is None and not done_before) or bool(analyzed)
        must_say = (truth_json or {}).get("must_say", []) if answered else []
        no_article = expect.get("no_article", False)

        history.append(f"USER (turn {n}): {turn['prompt']}")
        prompt = GRADER_PROMPT.format(
            history="\n".join(history),
            answer=parsed["answer"],
            must_say="\n".join(f"{i}. {p}" for i, p in enumerate(must_say, 1))
            or "(none for this reply: answer with an empty list)",
            expected_question={
                "languages": "one question about which language editions, with a proposed set",
                "meaning": "one question about which meaning of an ambiguous name",
                None: "no clarifying question",
            }[expect["asks"]],
            question_rule=QUESTION_RULES[expect["asks"]],
            no_article="yes" if no_article else "no",
            user_lang={"uk": "Ukrainian", "en": "English"}[ev["user_lang"]],
        )
        llm = grade_with_llm(prompt, out, logs / f"turn{n}.grader.json")
        history.append(f"ASSISTANT (turn {n}): {shorten(parsed['answer'])}")

        numbers = check_numbers.check(
            parsed["answer"], [PERIOD_OPTIONS, *(s for s in all_sources if s)]
        )
        verdicts = {m["point"]: m for m in llm["must_say"]}
        points = [
            {
                "point": text,
                "delivered": verdicts.get(i, {}).get("delivered", False),
                "evidence": verdicts.get(i, {}).get("evidence", "(not graded)"),
            }
            for i, text in enumerate(must_say, 1)
        ]
        scripted = skill and not done_before
        criteria = {
            "chain": check_chain(expect["chain"], calls) if scripted else None,
            "params": check_params(expect, calls) if scripted else None,
            "answer_lang": check_answer_lang(ev["user_lang"], calls) if skill else None,
            "must_say": all(p["delivered"] for p in points) if points else None,
            "numbers": numbers["passed"] if numbers["numbers"] else None,
            "no_article_offer": llm["no_article_offer"] if no_article else None,
            "substitute_offered": llm["substitute_offered"] if no_article else None,
            "questions": llm["clarification_ok"],
            "edition_not_country": llm["edition_not_country"] if answered else None,
            "pdf": pdf_written(cwd, meta) if expect["pdf"] else None,
            "reply_lang": llm["reply_language_ok"],
        }
        grades.append(
            {
                "eval": ev["name"],
                "config": config,
                "run": run,
                "turn": n,
                "criteria": criteria,
                "details": {
                    "commands": [c["command"] for c in calls],
                    "skill_invoked": parsed["skill_invoked"],
                    "denied_tools": parsed["denials"],
                    "tools": parsed["tools"],
                    "tool_errors": parsed["tool_errors"],
                    "must_say": points,
                    "numbers_missing": numbers["missing"],
                    "clarifying_questions": llm["clarifying_questions"],
                    "clarification_evidence": llm["clarification_evidence"],
                    "local_article_offered": llm["local_article_offered"]
                    if no_article
                    else None,
                    "no_article_evidence": llm["no_article_evidence"]
                    if no_article
                    else "",
                    "country_evidence": llm["country_evidence"],
                    "cost_usd": parsed.get("cost_usd"),
                    "duration_s": parsed.get("duration_s"),
                    "exit": meta["exit"],
                    "done_in_earlier_turn": done_before,
                },
                "answer": parsed["answer"],
            }
        )
    return grades


def anonymize(dumped_json: str) -> str:
    """The user's home folder in paths of a JSON dump -> <user> (the repo is public)."""
    home = Path.home()
    escaped = json.dumps(str(home))[1:-1]  # C:\\Users\\name as it stands in JSON text
    dumped_json = dumped_json.replace(
        escaped, escaped.rsplit("\\\\", 1)[0] + "\\\\<user>"
    )
    return dumped_json.replace(home.as_posix(), home.parent.as_posix() + "/<user>")


def cmd_grade(args: argparse.Namespace) -> None:
    out = Path(args.out)
    jobs = [
        (ev, config, run)
        for ev in EVALS["evals"]
        for config in CONFIGS
        for run in range(1, max(args.runs, args.web_runs) + 1)
        if log_dir(out, ev["name"], config, run).exists()
    ]
    with ThreadPoolExecutor(args.workers) as pool:
        grades = [
            g
            for session in pool.map(lambda j: grade_session(out, *j), jobs)
            for g in session
        ]
    text = anonymize(json.dumps(grades, ensure_ascii=False, indent=1))
    (out / "grades.json").write_text(text, encoding="utf-8")
    print(f"{len(grades)} graded turns -> {out / 'grades.json'}")


# ---------------------------------------------------------------- report

CRITERIA_UK = {
    "chain": "ланцюжок resolve → analyze",
    "params": "очікувані QID, мови, `--rank-by`, `--report`",
    "answer_lang": "`--answer-lang` = мова користувача",
    "must_say": "усі пункти `must_say` передано без зміни змісту",
    "numbers": "кожне число є в JSON навички",
    "no_article_offer": "для `no_article` запропоновано заміну, сказано, чим інша",
    "substitute_offered": "для `no_article` запропоновано заміну із застереженням",
    "questions": "одне питання, коли треба, і жодного, коли не треба",
    "edition_not_country": "мовний розділ, а не країна",
    "pdf": "PDF є, коли просили",
    "reply_lang": "відповідь мовою користувача (додатково)",
}


def share(values: list[bool | None]) -> str:
    applicable = [v for v in values if v is not None]
    if not applicable:
        return "—"
    return f"{sum(applicable)}/{len(applicable)}"


def load_grades(out: str) -> list[dict]:
    return json.loads((Path(out) / "grades.json").read_text(encoding="utf-8"))


def cmd_report(args: argparse.Namespace) -> None:
    """Criteria by config; with --before, that workspace's skill runs as "before"."""
    grades = load_grades(args.out)
    before = load_grades(args.before) if args.before else []

    def pick(rows: list[dict], config: str) -> list[dict]:
        return [g for g in rows if g["config"] == config]

    if before:
        columns = [
            ("з навичкою, до", pick(before, "with_skill")),
            ("з навичкою, після", pick(grades, "with_skill")),
            ("без навички", pick(before, "without_skill")),
            ("без навички + веб", pick(grades, "without_skill_web")),
        ]
    else:
        columns = [(c, pick(grades, c)) for c in CONFIGS]
    columns = [(c, rows) for c, rows in columns if rows]
    lines = [
        "| Критерій | " + " | ".join(c for c, _ in columns) + " |",
        "|---" * (len(columns) + 1) + "|",
    ]
    for key, label in CRITERIA_UK.items():
        cells = [share([g["criteria"].get(key) for g in rows]) for _, rows in columns]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "| Запит · хід | Конфігурація | " + " | ".join(CRITERIA_UK) + " |"]
    lines.append("|---" * (len(CRITERIA_UK) + 2) + "|")
    for ev in EVALS["evals"]:
        for n in range(1, len(ev["turns"]) + 1):
            for config in CONFIGS:
                rows = [
                    g
                    for g in grades
                    if (g["eval"], g["turn"], g["config"]) == (ev["name"], n, config)
                ]
                if not rows:
                    continue
                cells = [
                    share([g["criteria"].get(k) for g in rows]) for k in CRITERIA_UK
                ]
                lines.append(
                    f"| {ev['name']} · {n} | {config} | " + " | ".join(cells) + " |"
                )
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "grade", "report"):
        p = sub.add_parser(name)
        p.add_argument(
            "--out", required=True, help="workspace folder (spaces are welcome)"
        )
        p.add_argument("--runs", type=int, default=3, help="runs per eval and config")
        p.add_argument("--workers", type=int, default=3, help="sessions in parallel")
        p.add_argument("--only", nargs="*", help="eval names")
        p.add_argument("--skip-references", action="store_true")
        p.add_argument(
            "--configs", nargs="*", default=list(CONFIGS[:2]), choices=CONFIGS
        )
        p.add_argument(
            "--web-runs", type=int, default=2, help="runs of the web baseline"
        )
        p.add_argument("--before", help="report: an earlier workspace to compare with")
    args = parser.parse_args()
    {"run": cmd_run, "grade": cmd_grade, "report": cmd_report}[args.command](args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
