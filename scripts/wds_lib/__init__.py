"""Library behind the wikipedia-demand-signals CLI (scripts/wds.py)."""

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
