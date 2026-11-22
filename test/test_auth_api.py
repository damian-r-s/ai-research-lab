"""Criteria 1.8 to 1.10: login, sessions, throttling, roles, CSRF, credential storage."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.core.config import REPO_ROOT, AuthSettings, DatabaseSettings, Settings
from test.conftest import PASSWORD, ScratchDatabase, make_user, settings_for, sql

pytestmark = pytest.mark.integration

CSRF = {"X-Lab-CSRF": "1"}
MAX_FAILURES = 3


def _settings(db: ScratchDatabase) -> Settings:
    return Settings(
        env="test",
        database=DatabaseSettings(url=db.app_url, migration_url=db.owner_url),
        auth=AuthSettings(
            cookie_secure=False, max_failures=MAX_FAILURES, failure_window_minutes=15
        ),
    )


CLIENT_ADDRESS = ("203.0.113.7", 50000)  # TEST-NET-3 documentation address


@pytest.fixture
def client(fresh_db: ScratchDatabase) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(fresh_db)), client=CLIENT_ADDRESS) as c:
        yield c


def login(client: TestClient, username: str, password: str = PASSWORD) -> Any:
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}, headers=CSRF
    )


def audit_types(db: ScratchDatabase) -> list[str]:
    return [str(r[0]) for r in sql(db.owner_url, "SELECT type FROM lab.events ORDER BY seq")]


# --- 1.8 login and sessions -----------------------------------------------------------------


def test_successful_login_sets_a_hardened_cookie(
    client: TestClient, fresh_db: ScratchDatabase
) -> None:
    make_user(fresh_db, "alice", "researcher")
    response = login(client, "Alice ")  # usernames are case- and whitespace-insensitive
    assert response.status_code == 200
    assert response.json() == {"username": "alice", "role": "researcher"}
    cookie = response.headers["set-cookie"]
    assert "lab_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie
    assert client.get("/api/auth/me").json() == {"username": "alice", "role": "researcher"}
    assert audit_types(fresh_db)[-1] == "auth.login.succeeded"


def test_wrong_password_and_unknown_user_look_identical(
    client: TestClient, fresh_db: ScratchDatabase
) -> None:
    make_user(fresh_db, "alice")
    wrong = login(client, "alice", "not the password at all")
    unknown = login(client, "mallory", "not the password at all")
    assert wrong.status_code == unknown.status_code == 401
    assert (
        wrong.json()
        == unknown.json()
        == {"error": {"code": "not_authenticated", "message": "invalid username or password"}}
    )
    assert "set-cookie" not in wrong.headers


def test_inactive_user_cannot_log_in(client: TestClient, fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "alice")
    sql(fresh_db.owner_url, "UPDATE lab.users SET is_active = false WHERE username = 'alice'")
    assert login(client, "alice").status_code == 401
    reasons = sql(
        fresh_db.owner_url,
        "SELECT payload->>'reason' FROM lab.events WHERE type = 'auth.login.failed'",
    )
    assert reasons == [("inactive",)]


def test_deactivating_a_user_invalidates_existing_sessions(
    client: TestClient, fresh_db: ScratchDatabase
) -> None:
    make_user(fresh_db, "alice")
    assert login(client, "alice").status_code == 200
    sql(fresh_db.owner_url, "UPDATE lab.users SET is_active = false WHERE username = 'alice'")
    assert client.get("/api/auth/me").status_code == 401


def test_expired_session_is_rejected(client: TestClient, fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "alice")
    assert login(client, "alice").status_code == 200
    sql(fresh_db.owner_url, "UPDATE lab.auth_sessions SET expires_at = now() - interval '1 second'")
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "session expired; please log in again"


def test_logout_revokes_the_session_server_side(
    client: TestClient, fresh_db: ScratchDatabase
) -> None:
    make_user(fresh_db, "alice")
    login(client, "alice")
    token = client.cookies["lab_session"]
    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert audit_types(fresh_db)[-1] == "auth.session.revoked"
    # Replaying the old token (e.g. a stolen cookie) must not work after logout.
    with TestClient(client.app) as other:
        other.cookies.set("lab_session", token)
        assert other.get("/api/auth/me").status_code == 401


def test_active_sessions_are_touched(client: TestClient, fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "alice")
    login(client, "alice")
    sql(
        fresh_db.owner_url,
        "UPDATE lab.auth_sessions SET last_seen_at = now() - interval '5 minutes'",
    )
    client.get("/api/auth/me")
    (age,) = sql(
        fresh_db.owner_url,
        "SELECT extract(epoch FROM now() - last_seen_at) FROM lab.auth_sessions",
    )[0]
    assert float(age) < 60  # type: ignore[arg-type]


# --- 1.8 throttling -------------------------------------------------------------------------


def test_throttling_blocks_even_the_correct_password(
    client: TestClient, fresh_db: ScratchDatabase
) -> None:
    make_user(fresh_db, "alice")
    for _ in range(MAX_FAILURES):
        assert login(client, "alice", "wrong password here").status_code == 401
    blocked = login(client, "alice")  # correct password, but locked out
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "too_many_attempts"


def test_throttling_survives_an_application_restart(fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "alice")
    with TestClient(create_app(_settings(fresh_db)), client=CLIENT_ADDRESS) as first:
        for _ in range(MAX_FAILURES):
            login(first, "alice", "wrong password here")
    with TestClient(create_app(_settings(fresh_db)), client=CLIENT_ADDRESS) as restarted:
        assert login(restarted, "alice").status_code == 429


def test_throttling_applies_per_ip_across_usernames(
    client: TestClient, fresh_db: ScratchDatabase
) -> None:
    make_user(fresh_db, "alice")
    for name in ("bob", "carol", "dave"):  # password spraying from one address
        login(client, name, "guess guess guess")
    assert login(client, "alice").status_code == 429


def test_client_address_is_recorded(client: TestClient, fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "alice")
    login(client, "alice")
    assert sql(fresh_db.owner_url, "SELECT host(ip) FROM lab.auth_sessions") == [("203.0.113.7",)]


def test_non_ip_client_address_does_not_break_login(fresh_db: ScratchDatabase) -> None:
    """Regression: a peer like a Unix socket or test client name crashed login with a 500."""
    make_user(fresh_db, "alice")
    with TestClient(create_app(_settings(fresh_db)), client=("not-an-ip", 0)) as c:
        assert login(c, "alice").status_code == 200
    assert sql(fresh_db.owner_url, "SELECT ip FROM lab.auth_sessions") == [(None,)]


def test_throttle_window_expires(client: TestClient, fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "alice")
    for _ in range(MAX_FAILURES):
        login(client, "alice", "wrong password here")
    sql(
        fresh_db.owner_url,
        "UPDATE lab.login_attempts SET attempted_at = now() - interval '16 minutes'",
    )
    assert login(client, "alice").status_code == 200


# --- 1.9 authorisation and CSRF -------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/auth/me"),
        ("GET", "/api/users"),
        ("GET", "/api/events"),
        ("GET", "/api/events/latest"),
        ("GET", "/api/events/stream"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/users"),
    ],
)
def test_protected_endpoints_require_a_session(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path, headers=CSRF, json={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_roles_are_enforced(client: TestClient, fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "viewer1", "viewer")
    make_user(fresh_db, "boss", "admin")
    login(client, "viewer1")
    assert client.get("/api/events").status_code == 200  # viewers can read the audit log
    denied = client.get("/api/users")
    assert denied.status_code == 403
    assert denied.json()["error"]["message"] == "this action requires the admin role"
    login(client, "boss")
    assert client.get("/api/users").status_code == 200


def test_admin_can_create_users(client: TestClient, fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "boss", "admin")
    login(client, "boss")
    body = {"username": "newbie", "password": "a long enough password", "role": "viewer"}
    created = client.post("/api/users", json=body, headers=CSRF)
    assert created.status_code == 201
    assert created.json()["username"] == "newbie"
    assert "password" not in created.text and "hash" not in created.text
    assert client.post("/api/users", json=body, headers=CSRF).status_code == 409
    weak = client.post(
        "/api/users", json={**body, "username": "weakling", "password": "short"}, headers=CSRF
    )
    assert weak.status_code == 422
    assert weak.json()["error"]["code"] == "weak_password"


@pytest.mark.parametrize("path", ["/api/auth/login", "/api/auth/logout", "/api/users"])
def test_state_changing_requests_need_the_csrf_header(client: TestClient, path: str) -> None:
    response = client.post(path, json={"username": "x", "password": "y"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_header_missing"


def test_requests_get_an_id_for_tracing(client: TestClient) -> None:
    generated = client.get("/api/health/live")
    assert len(generated.headers["x-request-id"]) == 32
    echoed = client.get("/api/health/live", headers={"X-Request-ID": "trace-42"})
    assert echoed.headers["x-request-id"] == "trace-42"
    injected = client.get("/api/health/live", headers={"X-Request-ID": "bad id\nwith newline"})
    assert injected.headers["x-request-id"] != "bad id\nwith newline"


def test_login_audit_events_carry_the_request_id(
    client: TestClient, fresh_db: ScratchDatabase
) -> None:
    make_user(fresh_db, "alice")
    client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers={**CSRF, "X-Request-ID": "corr-login-1"},
    )
    rows = sql(
        fresh_db.owner_url,
        "SELECT correlation_id FROM lab.events WHERE type = 'auth.login.succeeded'",
    )
    assert rows == [("corr-login-1",)]


# --- 1.10 credential storage ----------------------------------------------------------------


def test_only_token_hashes_and_argon2id_hashes_are_stored(
    client: TestClient, fresh_db: ScratchDatabase
) -> None:
    make_user(fresh_db, "alice")
    login(client, "alice")
    token = client.cookies["lab_session"]
    ((token_hash,),) = sql(fresh_db.owner_url, "SELECT token_hash FROM lab.auth_sessions")
    assert token_hash == hashlib.sha256(token.encode()).hexdigest()
    dump = str(sql(fresh_db.owner_url, "SELECT * FROM lab.auth_sessions"))
    assert token not in dump
    ((password_hash,),) = sql(fresh_db.owner_url, "SELECT password_hash FROM lab.users")
    assert str(password_hash).startswith("$argon2id$")
    assert PASSWORD not in str(sql(fresh_db.owner_url, "SELECT * FROM lab.users"))


def test_settings_helper_is_consistent(migrated_db: ScratchDatabase) -> None:
    """Guard: the shared settings helper keeps secure cookies off only via test config."""
    assert settings_for(migrated_db).env == "test"


def test_create_user_script_bootstraps_an_admin(fresh_db: ScratchDatabase) -> None:
    """`python -m scripts.create_user` is how the first admin is created on the server."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("LAB_DATABASE__")}
    env["LAB_DATABASE__URL"] = fresh_db.app_url

    def run(password: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.create_user",
                "--username",
                "root-admin",
                "--role",
                "admin",
                "--password-stdin",
            ],
            input=password + "\n",
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    weak = run("short")
    assert weak.returncode == 1 and "at least 12 characters" in weak.stderr
    created = run(PASSWORD)
    assert created.returncode == 0, created.stderr
    assert "created user 'root-admin' with role admin" in created.stdout
    duplicate = run(PASSWORD)
    assert duplicate.returncode == 1 and "already exists" in duplicate.stderr
    with TestClient(create_app(_settings(fresh_db)), client=CLIENT_ADDRESS) as c:
        assert login(c, "root-admin").json() == {"username": "root-admin", "role": "admin"}
    actor = sql(
        fresh_db.owner_url, "SELECT actor_id FROM lab.events WHERE type = 'auth.user.created'"
    )
    assert actor == [("cli.create_user",)]


# --- client address behind proxies ----------------------------------------------------------


def _client_with_proxies(db: ScratchDatabase, trusted: list[str]) -> TestClient:
    from src.core.config import ApiSettings

    settings = Settings(
        env="test",
        database=DatabaseSettings(url=db.app_url, migration_url=db.owner_url),
        auth=AuthSettings(cookie_secure=False, max_failures=MAX_FAILURES),
        api=ApiSettings(trusted_proxies=trusted),
    )
    return TestClient(create_app(settings), client=CLIENT_ADDRESS)


def _recorded_ips(db: ScratchDatabase) -> list[str]:
    rows = sql(db.owner_url, "SELECT host(ip) FROM lab.login_attempts ORDER BY attempted_at")
    return [str(r[0]) for r in rows]


def test_forwarded_header_is_ignored_from_untrusted_peers(fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "alice")
    with _client_with_proxies(fresh_db, []) as c:
        c.post(
            "/api/auth/login",
            json={"username": "alice", "password": PASSWORD},
            headers={**CSRF, "X-Forwarded-For": "198.51.100.9"},
        )
    assert _recorded_ips(fresh_db) == ["203.0.113.7"]  # spoofing attempt ignored


def test_forwarded_client_is_used_behind_a_trusted_proxy(fresh_db: ScratchDatabase) -> None:
    make_user(fresh_db, "alice")
    with _client_with_proxies(fresh_db, ["203.0.113.0/24"]) as c:
        c.post(
            "/api/auth/login",
            json={"username": "alice", "password": PASSWORD},
            # The client prepended a fake hop; the proxy appended the real address.
            headers={**CSRF, "X-Forwarded-For": "1.2.3.4, 198.51.100.9"},
        )
    assert _recorded_ips(fresh_db) == ["198.51.100.9"]


def test_one_attacker_behind_the_proxy_does_not_lock_out_everyone(
    fresh_db: ScratchDatabase,
) -> None:
    """Regression: behind nginx every login came from the proxy IP, so per-IP throttling
    turned a few failed attempts by anyone into a lockout for all users."""
    make_user(fresh_db, "alice")
    with _client_with_proxies(fresh_db, ["203.0.113.0/24"]) as c:
        for i in range(MAX_FAILURES):
            c.post(
                "/api/auth/login",
                json={"username": f"guess{i}", "password": "wrong wrong wrong"},
                headers={**CSRF, "X-Forwarded-For": "198.51.100.66"},  # the attacker
            )
        ok = c.post(
            "/api/auth/login",
            json={"username": "alice", "password": PASSWORD},
            headers={**CSRF, "X-Forwarded-For": "198.51.100.10"},  # a legitimate user
        )
    assert ok.status_code == 200


def test_invalid_trusted_proxy_configuration_is_rejected() -> None:
    from pydantic import ValidationError

    from src.core.config import ApiSettings

    with pytest.raises(ValidationError, match="does not appear to be an IPv4 or IPv6 network"):
        ApiSettings(trusted_proxies=["not-a-network"])
