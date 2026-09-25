"""Library behind the wikipedia-demand-signals CLI (scripts/wds.py)."""

import json
import sys

__version__ = "0.1.0"

# Goes into the User-Agent: Wikimedia gives identified clients 200 req/min instead of 10.
# A repository URL is an accepted contact; no email on purpose.
REPO_URL = "https://github.com/cookiefbi/wikipedia-demand-signals"


class WdsError(Exception):
    """An error the agent can act on: what went wrong plus a hint what to do next."""

    def __init__(self, error: str, hint: str) -> None:
        super().__init__(error)
        self.error = error
        self.hint = hint

    def as_dict(self) -> dict:
        return {"status": "error", "error": self.error, "hint": self.hint}


def log(message: str) -> None:
    """Progress for humans and the agent: stderr only, stdout stays pure JSON.

    Never fails on encoding: a title the console cannot show becomes '?'.
    """
    encoding = getattr(sys.stderr, "encoding", None) or "utf-8"
    line = f"[wds] {message}\n".encode(encoding, "replace").decode(encoding)
    sys.stderr.write(line)
    sys.stderr.flush()


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def dumps(obj: dict) -> str:
    """JSON with one line per top-level key and per object in a list; the rest inline.

    Keeps a full result at roughly 20-40 lines: readable for the agent, cheap in tokens.
    """
    keys = list(obj)
    lines = ["{"]
    for i, key in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        value = obj[key]
        if isinstance(value, list) and value and isinstance(value[0], dict):
            lines.append(f"  {_compact(key)}: [")
            lines += [f"    {_compact(item)}," for item in value[:-1]]
            lines.append(f"    {_compact(value[-1])}")
            lines.append(f"  ]{comma}")
        else:
            lines.append(f"  {_compact(key)}: {_compact(value)}{comma}")
    lines.append("}")
    return "\n".join(lines)
