import types

import pytest
from helpers import FakeClock
from wds_lib import api


@pytest.fixture
def clock(monkeypatch, tmp_path):
    """Instant fake time, an empty cache in tmp_path and fresh request counters."""
    fake = FakeClock()
    monkeypatch.setattr(
        api,
        "time",
        types.SimpleNamespace(
            **{name: getattr(fake, name) for name in ("time", "monotonic", "sleep")}
        ),
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv(api.USER_AGENT_ENV, raising=False)
    monkeypatch.setattr(api, "_last_request_at", None)
    monkeypatch.setattr(api, "stats", {"network": 0, "cache": 0})
    return fake


@pytest.fixture
def network(monkeypatch):
    """Install a fake urlopen: network(FakeNetwork(...)) or network(UrlNetwork({...}))."""

    def install(fake):
        monkeypatch.setattr(api.urllib.request, "urlopen", fake)
        return fake

    return install
