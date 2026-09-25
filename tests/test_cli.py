import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import wds

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "wds.py"


def run_cli(
    *args: str, cwd: Path, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        check=False,
    )


def test_pep723_header_matches_requirements_txt():
    header = re.search(
        r"^# /// script\n(.*?)^# ///$",
        SCRIPT.read_text(encoding="utf-8"),
        re.DOTALL | re.MULTILINE,
    )
    toml = "\n".join(
        line.removeprefix("# ").removeprefix("#") for line in header[1].splitlines()
    )
    in_script = set(tomllib.loads(toml)["dependencies"])
    lines = (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines()
    in_requirements = {
        line.strip() for line in lines if line.strip() and not line.startswith("#")
    }
    assert in_script == in_requirements


def test_help_works_from_another_folder(tmp_path):
    proc = run_cli("--help", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert b"resolve" in proc.stdout and b"analyze" in proc.stdout


def test_non_ascii_json_survives_a_legacy_code_page_pipe(tmp_path):
    # Simulates a Windows pipe (ANSI code page) on any OS: without reconfigure,
    # printing 'ř' raises UnicodeEncodeError.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHONUTF8", "PYTHONIOENCODING")
    }
    env["PYTHONIOENCODING"] = "cp1251"
    proc = run_cli(
        "resolve", "Přerušovaný půst", "--langs", "cs", cwd=tmp_path, env=env
    )
    assert b"UnicodeEncodeError" not in proc.stderr
    result = json.loads(proc.stdout.decode("utf-8"))
    assert "Přerušovaný půst" in result["error"]


def test_usage_error_is_the_standard_json_error(tmp_path):
    proc = run_cli("resolve", "astronomy", cwd=tmp_path)  # --langs missing
    assert proc.returncode == wds.EXIT_USAGE
    result = json.loads(proc.stdout.decode("utf-8"))
    assert result["status"] == "error"
    assert "--langs" in result["error"]
    assert result["hint"]


def test_dumps_is_valid_json_with_one_line_per_list_item():
    obj = {
        "status": "ok",
        "results": [
            {"lang": "cs", "title": "Přerušovaný půst"},
            {"lang": "pl", "status": "no_article"},
        ],
        "empty": [],
        "files": {"a": "b"},
    }
    text = wds.dumps(obj)
    assert json.loads(text) == obj
    assert "Přerušovaný půst" in text
    assert len(text.splitlines()) == 9
