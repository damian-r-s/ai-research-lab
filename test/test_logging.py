"""Criterion 1.1 (logging part): structured JSON logs, context propagation, secret redaction."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.core.config import DatabaseSettings, LoggingSettings, Settings
from src.core.logging import bind_context, clear_context, configure_logging, get_logger
from test.conftest import ScratchDatabase

SECRET = "Sup3r-Secret-Pa55"


@pytest.fixture
def json_logs(capfd: pytest.CaptureFixture[str]) -> Iterator[pytest.CaptureFixture[str]]:
    configure_logging(LoggingSettings(level="DEBUG", format="json"))
    clear_context()
    yield capfd
    clear_context()
    configure_logging(LoggingSettings(level="WARNING", format="console"))


def _records(capfd: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    err = capfd.readouterr().err
    return [json.loads(line) for line in err.splitlines() if line.startswith("{")]


def test_json_format_has_timestamp_level_logger_and_fields(
    json_logs: pytest.CaptureFixture[str],
) -> None:
    get_logger("lab.test").info("thing.happened", count=3)
    (record,) = [r for r in _records(json_logs) if r["event"] == "thing.happened"]
    assert record["level"] == "info"
    assert record["logger"] == "lab.test"
    assert record["count"] == 3
    assert str(record["timestamp"]).endswith("Z")  # UTC, ISO 8601


def test_bound_context_is_added_to_every_line(json_logs: pytest.CaptureFixture[str]) -> None:
    bind_context(request_id="req-123")
    log = get_logger("lab.test")
    log.info("first")
    log.warning("second")
    records = [r for r in _records(json_logs) if r["event"] in {"first", "second"}]
    assert len(records) == 2
    assert all(r["request_id"] == "req-123" for r in records)


def test_sensitive_keys_are_redacted_but_similar_names_are_kept(
    json_logs: pytest.CaptureFixture[str],
) -> None:
    get_logger("lab.test").info(
        "login.attempt",
        password=SECRET,
        api_key=SECRET,
        session_token=SECRET,
        Authorization=SECRET,
        tokens_in=42,  # a metric, not a secret: must survive
    )
    (record,) = [r for r in _records(json_logs) if r["event"] == "login.attempt"]
    assert SECRET not in json.dumps(record)
    assert record["password"] == record["api_key"] == record["session_token"] == "***"
    assert record["Authorization"] == "***"
    assert record["tokens_in"] == 42


def test_stdlib_loggers_are_rendered_through_the_same_pipeline(
    json_logs: pytest.CaptureFixture[str],
) -> None:
    logging.getLogger("third.party").warning("plain stdlib message")
    records = [r for r in _records(json_logs) if r["event"] == "plain stdlib message"]
    assert records and records[0]["level"] == "warning"


def test_debug_lines_are_dropped_at_info_level(capfd: pytest.CaptureFixture[str]) -> None:
    configure_logging(LoggingSettings(level="INFO", format="json"))
    try:
        get_logger("lab.test").debug("noisy.detail")
        assert "noisy.detail" not in capfd.readouterr().err
    finally:
        configure_logging(LoggingSettings(level="WARNING", format="console"))


@pytest.mark.integration
def test_database_password_never_logged_during_app_lifecycle(
    migrated_db: ScratchDatabase, capfd: pytest.CaptureFixture[str]
) -> None:
    """Start, serve and stop the app with a password in its DB URL; the password must not leak."""
    url_with_password = migrated_db.app_url.replace("lab_app@", f"lab_app:{SECRET}@", 1)
    settings = Settings(
        env="test",
        logging=LoggingSettings(level="DEBUG", format="json"),
        database=DatabaseSettings(url=url_with_password, migration_url=migrated_db.owner_url),
    )
    try:
        with TestClient(create_app(settings)) as client:
            client.get("/api/health/ready")
        captured = capfd.readouterr()
        assert "api.started" in captured.err  # the lifecycle was actually logged
        assert SECRET not in captured.err
        assert SECRET not in captured.out
    finally:
        configure_logging(LoggingSettings(level="WARNING", format="console"))
