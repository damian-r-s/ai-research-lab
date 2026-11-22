"""Criterion 1.13 (HTTP part): audit log listing and the live SSE endpoint, behind login.

The stream is tested against a real uvicorn server over TCP, as a browser would use it.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import httpx
import pytest
import uvicorn

from src.api.app import create_app
from src.core.config import ApiSettings, AuthSettings, DatabaseSettings, Settings
from test.conftest import PASSWORD, ScratchDatabase, make_user

pytestmark = pytest.mark.integration

CSRF = {"X-Lab-CSRF": "1"}
KEEPALIVE_S = 0.3


@dataclass
class LiveServer:
    base_url: str
    server: uvicorn.Server
    thread: threading.Thread

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(10.0))


@pytest.fixture
def live(fresh_db: ScratchDatabase) -> Iterator[LiveServer]:
    settings = Settings(
        env="test",
        database=DatabaseSettings(url=fresh_db.app_url, migration_url=fresh_db.owner_url),
        auth=AuthSettings(cookie_secure=False),
        api=ApiSettings(sse_keepalive_s=KEEPALIVE_S),
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_config=None)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("live server did not start")
        time.sleep(0.02)
    yield LiveServer(f"http://127.0.0.1:{port}", server, thread)
    server.should_exit = True
    thread.join(timeout=10)


@contextmanager
def logged_in(
    live: LiveServer, db: ScratchDatabase, username: str = "watcher"
) -> Iterator[httpx.Client]:
    make_user(db, username, "viewer")
    with live.client() as c:
        r = c.post(
            "/api/auth/login", json={"username": username, "password": PASSWORD}, headers=CSRF
        )
        assert r.status_code == 200
        yield c


def failed_login(live: LiveServer, username: str) -> None:
    """Produces a real `auth.login.failed` audit event from outside the stream's session."""
    with live.client() as c:
        c.post("/api/auth/login", json={"username": username, "password": "x" * 12}, headers=CSRF)


@dataclass
class SseReader:
    """Parses an SSE response line by line in a background thread."""

    response: httpx.Response
    events: list[dict[str, str]] = field(default_factory=list)
    comments: int = 0
    retry: str | None = None
    closed: bool = False

    def __post_init__(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        current: dict[str, str] = {}
        try:
            for line in self.response.iter_lines():
                if line == "":
                    if current:
                        self.events.append(current)
                    current = {}
                elif line.startswith(":"):
                    self.comments += 1
                elif line.startswith("retry: "):
                    self.retry = line.removeprefix("retry: ")
                else:
                    key, _, value = line.partition(": ")
                    current[key] = value
        except httpx.HTTPError:
            pass
        finally:
            self.closed = True

    def wait(self, predicate: object, limit_s: float = 5.0) -> None:
        deadline = time.monotonic() + limit_s
        while not predicate():  # type: ignore[operator]
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out; events so far: {self.events}")
            time.sleep(0.02)

    def audit(self) -> list[dict[str, str]]:
        return [e for e in self.events if e.get("event", "").startswith("auth.")]


def test_stream_delivers_new_events_live(live: LiveServer, fresh_db: ScratchDatabase) -> None:
    with logged_in(live, fresh_db) as c, c.stream("GET", "/api/events/stream") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"
        reader = SseReader(response)
        reader.wait(lambda: any(e.get("event") == "auth.login.succeeded" for e in reader.events))
        failed_login(live, "intruder")
        reader.wait(lambda: any(e.get("event") == "auth.login.failed" for e in reader.events))
        assert reader.retry == "3000"
        ids = [int(e["id"]) for e in reader.events]
        assert ids == sorted(ids) and len(ids) == len(set(ids))


def test_stream_resumes_from_last_event_id(live: LiveServer, fresh_db: ScratchDatabase) -> None:
    with logged_in(live, fresh_db) as c:
        page = c.get("/api/events").json()
        last_seen = page["next_after"]
        failed_login(live, "missed-1")  # happen while the client is "disconnected"
        failed_login(live, "missed-2")
        headers = {"Last-Event-ID": str(last_seen)}
        with c.stream("GET", "/api/events/stream", headers=headers) as response:
            reader = SseReader(response)
            reader.wait(lambda: len(reader.audit()) >= 2)
            time.sleep(KEEPALIVE_S * 2)  # give duplicates a chance to appear
            got = reader.audit()
    assert [int(e["id"]) for e in got] == [last_seen + 1, last_seen + 2]
    assert all(e["event"] == "auth.login.failed" for e in got)


def test_idle_stream_sends_keepalives(live: LiveServer, fresh_db: ScratchDatabase) -> None:
    with logged_in(live, fresh_db) as c, c.stream("GET", "/api/events/stream") as response:
        reader = SseReader(response)
        reader.wait(lambda: reader.comments >= 2)


def test_logout_ends_an_open_stream(live: LiveServer, fresh_db: ScratchDatabase) -> None:
    with logged_in(live, fresh_db) as c:
        with c.stream("GET", "/api/events/stream") as response:
            reader = SseReader(response)
            reader.wait(lambda: reader.comments >= 1)
            assert c.post("/api/auth/logout", headers=CSRF).status_code == 204
            reader.wait(lambda: reader.closed, limit_s=KEEPALIVE_S * 10)
        assert reader.events[-1]["event"] == "session_ended"


def test_stream_requires_login(live: LiveServer) -> None:
    with live.client() as c:
        assert c.get("/api/events/stream").status_code == 401


def test_invalid_last_event_id_is_rejected(live: LiveServer, fresh_db: ScratchDatabase) -> None:
    with logged_in(live, fresh_db) as c:
        r = c.get("/api/events/stream", headers={"Last-Event-ID": "abc"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_last_event_id"


def test_listing_pages_and_filters(live: LiveServer, fresh_db: ScratchDatabase) -> None:
    with logged_in(live, fresh_db) as c:
        for i in range(3):
            failed_login(live, f"guess{i}")
        first = c.get("/api/events", params={"limit": 2}).json()
        rest = c.get("/api/events", params={"after": first["next_after"]}).json()
        all_seqs = [e["seq"] for e in first["events"] + rest["events"]]
        assert all_seqs == sorted(all_seqs) and len(all_seqs) == len(set(all_seqs))
        failed_only = c.get("/api/events", params={"type": "auth.login.failed"}).json()
        assert {e["type"] for e in failed_only["events"]} == {"auth.login.failed"}
        latest = c.get("/api/events/latest", params={"limit": 2}).json()
        assert [e["seq"] for e in latest] == sorted(all_seqs, reverse=True)[:2]
        assert all(len(e["hash"]) == 64 for e in latest)
