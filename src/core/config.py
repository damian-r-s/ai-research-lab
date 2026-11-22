"""Typed, validated configuration.

Precedence (highest first): explicit kwargs → environment variables (``LAB_*``, nested with
``__``) → ``.env`` file (dev only, git-ignored) → secret files (``LAB_SECRETS_DIR``) →
``config/environments/<LAB_ENV>.toml`` → ``config/base.toml`` → defaults below.

Hardware- and environment-specific values belong in the environment TOML files, never in code.

Secrets (database passwords, API keys) are only ever read from the environment or secret files
and are typed as ``SecretStr`` so they never appear in logs or ``repr``.
"""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NestedSecretsSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"

Environment = Literal["dev", "test", "staging", "prod"]
_SERVER_ENVIRONMENTS = ("staging", "prod")


class _Section(BaseModel):
    """Base for config sections: unknown keys (typos) are errors, values are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DatabaseSettings(_Section):
    # SQLAlchemy URL used by the running application (least-privilege role).
    url: SecretStr = Field(default=SecretStr("postgresql+psycopg://lab_app@localhost:5432/lab"))
    # URL used for migrations (schema owner). Defaults to `url` when unset.
    migration_url: SecretStr | None = None
    # Role that receives DML grants from migrations. Must match the role in `url`.
    app_role: str = Field(default="lab_app", pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    # LangGraph checkpointer: its own role, confined to schema `langgraph` (DECISIONS C-6).
    # Needed by the runner and by `scripts.migrate` (checkpointer setup); the api never uses it.
    graph_url: SecretStr | None = None
    graph_role: str = Field(default="lab_graph", pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    pool_size: int = Field(default=10, ge=1, le=200)
    pool_timeout_s: float = Field(default=10.0, gt=0)
    statement_timeout_ms: int = Field(default=30_000, ge=100)

    @field_validator("url", "migration_url", "graph_url")
    @classmethod
    def _must_be_postgres(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and not v.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("database URLs must use the 'postgresql+psycopg://' driver")
        return v

    def effective_migration_url(self) -> SecretStr:
        return self.migration_url or self.url


class AuthSettings(_Section):
    session_ttl_hours: float = Field(default=12.0, gt=0, le=24 * 30)
    cookie_name: str = Field(default="lab_session", pattern=r"^[A-Za-z0-9_\-]+$")
    cookie_secure: bool = True
    # Throttling: more than `max_failures` failed logins for one username *or* one IP
    # within `failure_window_minutes` blocks further attempts until the window passes.
    max_failures: int = Field(default=5, ge=1)
    failure_window_minutes: int = Field(default=15, ge=1)


class ApiSettings(_Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    # Header that state-changing requests must carry (custom-header CSRF defence).
    csrf_header: str = "X-Lab-CSRF"
    # SSE: idle keep-alive interval; also how often a long-lived stream re-checks its session.
    sse_keepalive_s: float = Field(default=15.0, ge=0.1, le=300)
    # Proxies (CIDRs) whose X-Forwarded-For header is trusted, e.g. the dashboard's nginx pods.
    # Empty = the header is ignored, so clients cannot spoof their address.
    trusted_proxies: list[str] = Field(default_factory=list)

    @field_validator("trusted_proxies")
    @classmethod
    def _valid_networks(cls, v: list[str]) -> list[str]:
        for cidr in v:
            ipaddress.ip_network(cidr, strict=False)  # raises ValueError with a clear message
        return v


class LoggingSettings(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "console"] = "json"


class ResourceSettings(_Section):
    sample_interval_s: float = Field(default=5.0, ge=0.5)
    # Filesystems to report; the data directory is always included.
    disk_paths: list[str] = Field(default_factory=lambda: ["/"])
    memory_warn_percent: float = Field(default=90.0, gt=0, le=100)
    disk_warn_percent: float = Field(default=90.0, gt=0, le=100)
    # Hardware expectation of this host: a missing GPU is only a warning if one is expected.
    expect_gpu: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LAB_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="forbid",
        frozen=True,
    )

    env: Environment = "dev"
    data_dir: Path = REPO_ROOT / "var"
    database: DatabaseSettings = DatabaseSettings()
    auth: AuthSettings = AuthSettings()
    api: ApiSettings = ApiSettings()
    logging: LoggingSettings = LoggingSettings()
    resources: ResourceSettings = ResourceSettings()

    @model_validator(mode="after")
    def _server_guards(self) -> Settings:
        if self.env in _SERVER_ENVIRONMENTS:
            if not self.auth.cookie_secure:
                raise ValueError(f"auth.cookie_secure must be true in {self.env}")
            if self.logging.format != "json":
                raise ValueError(f"logging.format must be 'json' in {self.env}")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Read at construction time (not import time) so tooling and tests can point elsewhere.
        config_dir = Path(os.environ.get("LAB_CONFIG_DIR", DEFAULT_CONFIG_DIR))
        env_name = os.environ.get("LAB_ENV", "dev")
        # Later files override earlier ones: environment file over base file.
        candidates = (config_dir / "base.toml", config_dir / "environments" / f"{env_name}.toml")
        toml_files = [path for path in candidates if path.is_file()]
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings, dotenv_settings]
        # Kubernetes Secret mounted as a directory: one file per value, named like the env var
        # without the prefix, e.g. <dir>/database__url. A configured but missing dir is an error.
        secrets_dir = os.environ.get("LAB_SECRETS_DIR")
        if secrets_dir:
            sources.append(
                NestedSecretsSettingsSource(
                    file_secret_settings,
                    secrets_dir=secrets_dir,
                    secrets_dir_missing="error",
                    secrets_prefix="",
                    secrets_nested_delimiter="__",
                )
            )
        if toml_files:
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=toml_files))
        return tuple(sources)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
