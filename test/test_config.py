"""Criterion 1.1: configuration is layered, validated, fails fast, and hides secrets."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.core.config import REPO_ROOT, Settings

SECRET = "s3cr3t-Pa55word"
SECRET_URL = f"postgresql+psycopg://lab_app:{SECRET}@db.internal:5432/lab"


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No LAB_* variables from the developer shell; an empty, private config directory."""
    for key in list(os.environ):
        if key.startswith("LAB_"):
            monkeypatch.delenv(key)
    config_dir = tmp_path / "config"
    (config_dir / "environments").mkdir(parents=True)
    monkeypatch.setenv("LAB_CONFIG_DIR", str(config_dir))
    return config_dir


def load(**kwargs: Any) -> Settings:
    """Build settings while ignoring any developer `.env` file."""
    return Settings(_env_file=None, **kwargs)


# --- layering -------------------------------------------------------------------------------


def test_defaults_without_any_configuration(isolated_env: Path) -> None:
    s = load()
    assert s.env == "dev"
    assert s.api.port == 8000
    assert s.auth.cookie_secure is True  # secure unless an environment explicitly relaxes it


def test_environment_file_overrides_base_file(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_env / "base.toml").write_text('[api]\nport = 9000\n[logging]\nlevel = "INFO"\n')
    (isolated_env / "environments" / "staging.toml").write_text(
        'env = "staging"\n[api]\nport = 9100\n'
    )
    monkeypatch.setenv("LAB_ENV", "staging")
    s = load()
    assert s.env == "staging"
    assert s.api.port == 9100  # environment file wins
    assert s.logging.level == "INFO"  # untouched base value survives the merge


def test_environment_variables_override_files(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_env / "base.toml").write_text("[api]\nport = 9000\n")
    monkeypatch.setenv("LAB_API__PORT", "9200")
    assert load().api.port == 9200


def test_repository_config_files_are_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """The committed config/ files themselves must load for every environment that has one."""
    for key in list(os.environ):
        if key.startswith("LAB_"):
            monkeypatch.delenv(key)
    for env_file in sorted((REPO_ROOT / "config" / "environments").glob("*.toml")):
        monkeypatch.setenv("LAB_ENV", env_file.stem)
        assert load().env == env_file.stem


# --- validation -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variable", "value", "field"),
    [
        ("LAB_API__PORT", "70000", "api.port"),
        ("LAB_DATABASE__URL", "mysql://user@host/db", "database.url"),
        ("LAB_LOGGING__LEVEL", "LOUD", "logging.level"),
        ("LAB_AUTH__MAX_FAILURES", "0", "auth.max_failures"),
        ("LAB_ENV", "production", "env"),
    ],
)
def test_invalid_values_are_rejected_with_the_field_named(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, variable: str, value: str, field: str
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError) as exc:
        load()
    assert field in str(exc.value)


def test_unknown_keys_in_config_files_are_rejected(isolated_env: Path) -> None:
    """A typo in a TOML file must fail loudly instead of being silently ignored."""
    (isolated_env / "base.toml").write_text("[api]\nprot = 9000\n")
    with pytest.raises(ValidationError) as exc:
        load()
    assert "api.prot" in str(exc.value)


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_server_environments_refuse_insecure_cookies(isolated_env: Path, environment: str) -> None:
    with pytest.raises(ValidationError, match="cookie_secure must be true"):
        load(env=environment, auth={"cookie_secure": False})


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_server_environments_require_json_logs(isolated_env: Path, environment: str) -> None:
    with pytest.raises(ValidationError, match=re.escape("logging.format must be 'json'")):
        load(env=environment, logging={"format": "console"})


def test_invalid_configuration_aborts_application_start(tmp_path: Path) -> None:
    """Start-up (importing the ASGI app) must exit non-zero with a readable English error."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("LAB_")}
    env.update({"LAB_CONFIG_DIR": str(tmp_path), "LAB_API__PORT": "not-a-port"})
    result = subprocess.run(
        [sys.executable, "-c", "import app"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "api.port" in result.stderr
    assert "Input should be a valid integer" in result.stderr


# --- secrets --------------------------------------------------------------------------------


def test_secrets_never_appear_in_repr_or_serialisation(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAB_DATABASE__URL", SECRET_URL)
    monkeypatch.setenv("LAB_DATABASE__MIGRATION_URL", SECRET_URL)
    s = load()
    assert s.database.url.get_secret_value() == SECRET_URL  # still usable where needed
    for rendered in (repr(s), str(s), s.model_dump_json(), repr(s.model_dump())):
        assert SECRET not in rendered


def test_secrets_can_be_read_from_mounted_secret_files(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Kubernetes Secret mounted as files (one file per value) is a supported source."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "database__url").write_text(SECRET_URL + "\n")  # trailing newline is common
    monkeypatch.setenv("LAB_SECRETS_DIR", str(secrets_dir))
    assert load().database.url.get_secret_value() == SECRET_URL


def test_missing_secrets_directory_is_an_error(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LAB_SECRETS_DIR", str(tmp_path / "does-not-exist"))
    with pytest.raises(Exception, match="does-not-exist"):
        load()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"reaper_interval_s": 7}, "must divide 60"),
        ({"heartbeat_interval_s": 10, "stall_timeout_s": 15}, "twice the heartbeat"),
    ],
)
def test_runner_settings_reject_unsafe_timing(values: dict[str, float], message: str) -> None:
    from src.core.config import RunnerSettings

    with pytest.raises(ValueError, match=message):
        RunnerSettings(**values)
