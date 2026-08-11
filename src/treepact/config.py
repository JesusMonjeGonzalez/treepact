"""TreePact configuration loading with documented precedence.

Precedence from lowest to highest:

1. Built-in safe defaults.
2. User configuration in `~/.config/treepact/config.toml` (or an explicit
   `--config PATH`, which replaces the user location).
3. Repository `.treepact.yaml` for repository capabilities (milestone M3;
   a higher layer may reduce authority but never expand beyond the Pact).
4. Explicit safe CLI options such as mode and runtime.

CLI options can never add paths, commands, network, or publication actions.
Secrets are never stored in configuration.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from dataclasses import replace as dataclasses_replace
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir, user_data_dir

from treepact.errors import ConfigurationError

APP_DIR_NAME = "TreePact"
CONFIG_FILE_NAME = "config.toml"

DEFAULT_LOG_LEVEL = "info"
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 120
DEFAULT_RESERVED_MEMORY_MB = 4096
DEFAULT_LARGE_MODEL_MEMORY_MB = 12000
DEFAULT_LOGS_RETENTION_DAYS = 14

_ALLOWED_ROOT_KEYS = {"log_level", "provider", "resources", "retention"}
_ALLOWED_PROVIDER_KEYS = {"endpoint", "timeout_seconds", "profiles"}
_ALLOWED_RESOURCE_KEYS = {"reserved_memory_mb", "large_model_memory_mb"}
_ALLOWED_RETENTION_KEYS = {"logs_days"}


@dataclass(frozen=True)
class ProviderConfig:
    """Optional loopback OpenAI-compatible provider endpoint.

    `profiles` maps the domain profiles (classify, fast-code, deep-code) to
    provider model IDs. Model IDs never appear in the domain.
    """

    endpoint: str | None = None
    timeout_seconds: int = DEFAULT_PROVIDER_TIMEOUT_SECONDS
    profiles: dict[str, str] = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ResourceConfig:
    """Conservative scheduling defaults; enforcement arrives in milestone M6."""

    reserved_memory_mb: int = DEFAULT_RESERVED_MEMORY_MB
    large_model_memory_mb: int = DEFAULT_LARGE_MODEL_MEMORY_MB


@dataclass(frozen=True)
class RetentionConfig:
    logs_days: int = DEFAULT_LOGS_RETENTION_DAYS


@dataclass(frozen=True)
class Config:
    data_dir: Path
    config_path: Path | None
    log_level: str
    provider: ProviderConfig
    resources: ResourceConfig
    retention: RetentionConfig
    sources: tuple[str, ...] = ()

    def replace(self, **kwargs: object) -> Config:
        return dataclasses_replace(self, **kwargs)  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        """Non-secret effective configuration for `treepact config show`."""
        return {
            "data_dir": str(self.data_dir),
            "config_path": str(self.config_path) if self.config_path else None,
            "log_level": self.log_level,
            "provider": {
                "endpoint": self.provider.endpoint,
                "timeout_seconds": self.provider.timeout_seconds,
                "profiles": self.provider.profiles or {},
            },
            "resources": {
                "reserved_memory_mb": self.resources.reserved_memory_mb,
                "large_model_memory_mb": self.resources.large_model_memory_mb,
            },
            "retention": {"logs_days": self.retention.logs_days},
        }


def default_config_path() -> Path:
    return Path(user_config_dir("treepact")) / CONFIG_FILE_NAME


def default_data_dir() -> Path:
    return Path(user_data_dir(APP_DIR_NAME))


def load_config(
    explicit_config: Path | None = None,
    explicit_data_dir: Path | None = None,
) -> Config:
    sources: list[str] = ["built-in defaults"]

    cfg = Config(
        data_dir=explicit_data_dir or default_data_dir(),
        config_path=None,
        log_level=DEFAULT_LOG_LEVEL,
        provider=ProviderConfig(),
        resources=ResourceConfig(),
        retention=RetentionConfig(),
        sources=(),
    )

    config_path = explicit_config or default_config_path()
    if explicit_config is not None:
        sources.append(f"explicit --config {config_path}")

    if config_path.exists():
        data = _load_toml_strict(config_path, sources)
        if "log_level" in data:
            cfg = cfg.replace(log_level=data["log_level"])
        if "provider" in data:
            profiles = data["provider"].get("profiles")
            if profiles is not None and not isinstance(profiles, dict):
                raise ConfigurationError("configuration key `provider.profiles` must be a table", code="config_invalid")
            cfg = cfg.replace(
                provider=ProviderConfig(
                    endpoint=data["provider"].get("endpoint"),
                    timeout_seconds=data["provider"].get(
                        "timeout_seconds", DEFAULT_PROVIDER_TIMEOUT_SECONDS
                    ),
                    profiles=dict(profiles or {}),
                )
            )
        if "resources" in data:
            cfg = cfg.replace(
                resources=ResourceConfig(
                    reserved_memory_mb=data["resources"].get(
                        "reserved_memory_mb", DEFAULT_RESERVED_MEMORY_MB
                    ),
                    large_model_memory_mb=data["resources"].get(
                        "large_model_memory_mb", DEFAULT_LARGE_MODEL_MEMORY_MB
                    ),
                )
            )
        if "retention" in data:
            cfg = cfg.replace(
                retention=RetentionConfig(
                    logs_days=data["retention"].get("logs_days", DEFAULT_LOGS_RETENTION_DAYS)
                )
            )
        cfg = cfg.replace(config_path=config_path)

    if explicit_data_dir is not None:
        sources.append(f"explicit --data-dir {explicit_data_dir}")

    return cfg.replace(sources=tuple(sources))


def _load_toml_strict(path: Path, sources: list[str]) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(
            f"cannot read configuration file {path}: {exc}", code="config_unreadable"
        ) from exc

    unknown = set(raw) - _ALLOWED_ROOT_KEYS
    if unknown:
        raise ConfigurationError(
            f"unknown configuration key(s) in {path}: {', '.join(sorted(unknown))}",
            code="config_unknown_key",
        )

    provider = raw.get("provider", {})
    if not isinstance(provider, dict):
        raise ConfigurationError("configuration key `provider` must be a table", code="config_invalid")
    unknown = set(provider) - _ALLOWED_PROVIDER_KEYS
    if unknown:
        raise ConfigurationError(
            f"unknown `provider` key(s): {', '.join(sorted(unknown))}", code="config_unknown_key"
        )

    resources = raw.get("resources", {})
    if not isinstance(resources, dict):
        raise ConfigurationError("configuration key `resources` must be a table", code="config_invalid")
    unknown = set(resources) - _ALLOWED_RESOURCE_KEYS
    if unknown:
        raise ConfigurationError(
            f"unknown `resources` key(s): {', '.join(sorted(unknown))}", code="config_unknown_key"
        )

    retention = raw.get("retention", {})
    if not isinstance(retention, dict):
        raise ConfigurationError("configuration key `retention` must be a table", code="config_invalid")
    unknown = set(retention) - _ALLOWED_RETENTION_KEYS
    if unknown:
        raise ConfigurationError(
            f"unknown `retention` key(s): {', '.join(sorted(unknown))}", code="config_unknown_key"
        )

    sources.append(f"user config {path}")
    return {
        "log_level": raw.get("log_level"),
        "provider": provider,
        "resources": resources,
        "retention": retention,
    }


def ensure_data_layout(cfg: Config) -> None:
    """Create the TreePact data root layout: db, artifacts, worktrees, runs,
    logs, backups. Never contains credentials."""
    for sub in ("db", "artifacts", "worktrees", "runs", "logs", "backups"):
        (cfg.data_dir / sub).mkdir(parents=True, exist_ok=True)


def database_path(cfg: Config) -> Path:
    return cfg.data_dir / "db" / "treepact.sqlite"


def run_bundle_dir(cfg: Config, run_id: str) -> Path:
    return cfg.data_dir / "runs" / run_id
