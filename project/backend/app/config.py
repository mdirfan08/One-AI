"""Runtime configuration (environment variables) and per-project limits."""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


def _bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    access_token: Optional[str] = None
    enable_command_execution: bool = False
    cors_origins: tuple = ("http://localhost:5173", "http://127.0.0.1:5173")
    max_request_bytes: int = 2_000_000
    max_upload_bytes: int = 500_000
    pricing_file: Optional[Path] = None
    log_level: str = "INFO"
    retry_backoff_s: float = 1.5

    @classmethod
    def from_env(cls) -> "AppConfig":
        env = os.environ
        pricing = env.get("PRICING_FILE")
        origins = tuple(o.strip() for o in env.get("CORS_ORIGINS", "").split(",") if o.strip()) or cls.cors_origins
        return cls(
            data_dir=Path(env.get("DATA_DIR", "./data")).resolve(),
            access_token=env.get("APP_ACCESS_TOKEN") or None,
            enable_command_execution=_bool(env.get("ENABLE_COMMAND_EXECUTION")),
            cors_origins=origins,
            max_request_bytes=int(env.get("MAX_REQUEST_BYTES", cls.max_request_bytes)),
            max_upload_bytes=int(env.get("MAX_UPLOAD_BYTES", cls.max_upload_bytes)),
            pricing_file=Path(pricing).resolve() if pricing else None,
            log_level=env.get("LOG_LEVEL", "INFO"),
            retry_backoff_s=float(env.get("RETRY_BACKOFF_S", cls.retry_backoff_s)),
        )


# Absolute ceilings: user-configured limits are clamped to these.
HARD_CEILINGS = {"max_tasks": 30, "max_retries": 5, "max_duration_s": 3600, "max_parallel": 6}


@dataclass
class Limits:
    max_tasks: int = 12
    max_retries: int = 2            # automatic retries per task (attempts = retries + 1)
    max_duration_s: int = 900       # wall-clock per run
    max_parallel: int = 3
    max_usd: Optional[float] = None  # None = no USD cap (token/call caps still apply)
    max_total_tokens: int = 200_000  # input+output, worst-case reserved before each call
    max_calls: int = 60

    def clamp(self) -> "Limits":
        self.max_tasks = max(1, min(int(self.max_tasks), HARD_CEILINGS["max_tasks"]))
        self.max_retries = max(0, min(int(self.max_retries), HARD_CEILINGS["max_retries"]))
        self.max_duration_s = max(10, min(int(self.max_duration_s), HARD_CEILINGS["max_duration_s"]))
        self.max_parallel = max(1, min(int(self.max_parallel), HARD_CEILINGS["max_parallel"]))
        self.max_total_tokens = max(1000, int(self.max_total_tokens))
        self.max_calls = max(1, int(self.max_calls))
        if self.max_usd is not None:
            self.max_usd = max(0.0, float(self.max_usd))
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Limits":
        d = d or {}
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known).clamp()
