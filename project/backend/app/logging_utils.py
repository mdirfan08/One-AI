"""Logging with secret redaction. Prompt/output text is never logged by this app."""
from __future__ import annotations

import logging
import re
import threading

_KEY_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
]
_HEADER_PATTERN = re.compile(r"(?i)(authorization|x-api-key|api[_-]?key)(\"?\s*[:=]\s*\"?)(?:bearer\s+)?([^\s\",;]+)")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}")


class Redactor:
    def __init__(self) -> None:
        self._secrets: set = set()
        self._lock = threading.Lock()

    def register(self, secret) -> None:
        if secret and len(secret) >= 8:
            with self._lock:
                self._secrets.add(secret)

    def redact(self, text: str) -> str:
        if not text:
            return text
        with self._lock:
            secrets = list(self._secrets)
        for s in secrets:
            text = text.replace(s, "[REDACTED]")
        for p in _KEY_PATTERNS:
            text = p.sub("[REDACTED]", text)
        text = _HEADER_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        return _BEARER_PATTERN.sub("Bearer [REDACTED]", text)


REDACTOR = Redactor()


def redact(text: str) -> str:
    return REDACTOR.redact(text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = REDACTOR.redact(record.getMessage())
            record.args = ()
        except Exception:  # never break logging
            pass
        return True


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not any(isinstance(f, RedactingFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
