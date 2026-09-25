"""Stand-ins for the clock and the network, shared by the offline tests."""

import email.message
import io
import json
import urllib.error


class FakeClock:
    """Stands in for the time module: sleeping advances the clock instantly."""

    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def http_error(
    code: int, retry_after: str | None = None, url: str = "https://example.org/"
) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(url, code, "error", headers, None)


def _response(body) -> io.BytesIO:
    return io.BytesIO(json.dumps(body).encode("utf-8"))


class FakeNetwork:
    """Replays scripted outcomes: a dict/list is a 200 JSON body, an exception is raised."""

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.requests: list = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if not self.outcomes:
            raise AssertionError("unexpected network request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _response(outcome)


class UrlNetwork:
    """Answers by URL: a known URL gets its body, any other URL a 404."""

    def __init__(self, bodies: dict[str, object]) -> None:
        self.bodies = bodies
        self.urls: list[str] = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.urls.append(url)
        if url not in self.bodies:
            raise http_error(404, url=url)
        return _response(self.bodies[url])
