"""Provider adapter interface, errors, credential store and exception classification."""
from __future__ import annotations

import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..logging_utils import REDACTOR, redact


# ----------------------------------------------------------------------------- errors
class ProviderError(Exception):
    """code: auth | rate_limit | quota | unsupported | timeout | unavailable | bad_request | bad_response | not_configured | error"""

    def __init__(self, message: str, code: str = "error", retryable: bool = False, provider: str = ""):
        super().__init__(redact(message))
        self.code = code
        self.retryable = retryable
        self.provider = provider


class UnsupportedCapability(ProviderError):
    def __init__(self, message: str, provider: str = ""):
        super().__init__(message, code="unsupported", retryable=False, provider=provider)


def classify_exception(exc: Exception, provider: str) -> ProviderError:
    """Map arbitrary SDK exceptions onto ProviderError using duck typing (no SDK imports needed)."""
    if isinstance(exc, ProviderError):
        return exc
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "code", None)
    if not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if not isinstance(status, int):
        status = None
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    text = redact(str(exc))[:300]

    if status in (401, 403) or "authentication" in name or "permissiondenied" in name:
        return ProviderError(f"Authentication/permission failure: {text}", "auth", False, provider)
    if status == 429 or "ratelimit" in name or "resource_exhausted" in msg:
        if any(w in msg for w in ("quota", "billing", "insufficient", "credit balance", "exceeded your current")):
            return ProviderError(f"Quota or billing limit reached: {text}", "quota", False, provider)
        return ProviderError(f"Rate limited: {text}", "rate_limit", True, provider)
    if "timeout" in name or "timed out" in msg:
        return ProviderError(f"Request timed out: {text}", "timeout", True, provider)
    if (status is not None and status >= 500) or "connection" in name or "overloaded" in msg:
        return ProviderError(f"Provider unavailable: {text}", "unavailable", True, provider)
    if status in (400, 404, 413, 422):
        return ProviderError(f"Request rejected: {text}", "bad_request", False, provider)
    return ProviderError(f"Unexpected provider error ({type(exc).__name__}): {text}", "error", False, provider)


# ------------------------------------------------------------------------- data types
@dataclass
class GenerationRequest:
    system: str
    prompt: str
    json_mode: bool = False
    max_output_tokens: int = 4096
    temperature: Optional[float] = None
    timeout_s: float = 120.0
    metadata: dict = field(default_factory=dict)   # task_kind, attempt, etc. (used by DemoProvider)


@dataclass
class GenerationResult:
    text: str
    provider: str
    model: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    simulated: bool = False
    finish_reason: Optional[str] = None


@dataclass
class ImageResult:
    data: bytes
    mime: str
    provider: str
    model: str
    simulated: bool = False
    note: str = ""


@dataclass
class ProviderStatus:
    id: str
    display_name: str
    configured: bool
    credential_source: Optional[str]      # "session" | "env" | None   (never the key itself)
    model: str
    image_model: str
    capabilities: list
    connection: str                        # "unknown" | "ok" | "error" | "not_configured" | "simulated"
    last_request_ok: Optional[bool] = None
    last_request_at: Optional[float] = None
    last_error: Optional[str] = None
    last_error_code: Optional[str] = None
    quota_info: str = "Not available through this provider's public API; check the provider dashboard."
    may_charge: bool = True
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


# ------------------------------------------------------------------ credential storage
class CredentialStore:
    """Credentials live in process memory only (session-scoped) or in environment variables.
    Nothing is written to disk, the database, exports or logs. They are NOT encrypted in memory."""

    ENV_VARS = {
        "openai": ("OPENAI_API_KEY",),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    }

    def __init__(self, env: Optional[dict] = None):
        self._session: dict = {}
        self._env = env if env is not None else os.environ
        self._lock = threading.Lock()
        for pid in self.ENV_VARS:
            REDACTOR.register(self.get(pid))

    def set_session_key(self, provider: str, key: str) -> None:
        key = key.strip()
        if not key or len(key) > 512 or any(c.isspace() for c in key):
            raise ValueError("Invalid API key format.")
        with self._lock:
            self._session[provider] = key
        REDACTOR.register(key)

    def clear_session_key(self, provider: str) -> None:
        with self._lock:
            self._session.pop(provider, None)

    def get(self, provider: str) -> Optional[str]:
        with self._lock:
            if provider in self._session:
                return self._session[provider]
        for var in self.ENV_VARS.get(provider, ()):
            val = self._env.get(var)
            if val:
                return val
        return None

    def source(self, provider: str) -> Optional[str]:
        with self._lock:
            if provider in self._session:
                return "session"
        return "env" if any(self._env.get(v) for v in self.ENV_VARS.get(provider, ())) else None


# ------------------------------------------------------------------------------ adapter
class ProviderAdapter(ABC):
    id: str = ""
    display_name: str = ""
    base_capabilities: frozenset = frozenset()
    default_model: str = ""
    default_image_model: str = ""
    may_charge: bool = True

    def __init__(self, credentials: CredentialStore, model: Optional[str] = None, image_model: Optional[str] = None,
                 client_factory: Optional[Callable] = None):
        self.credentials = credentials
        self.model = model or os.environ.get(f"{self.id.upper()}_MODEL") or self.default_model
        self.image_model = (image_model if image_model is not None
                            else os.environ.get(f"{self.id.upper()}_IMAGE_MODEL", self.default_image_model))
        self._client_factory = client_factory
        self._client = None
        self._client_key = None
        self._lock = threading.Lock()
        self.last_ok: Optional[bool] = None
        self.last_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.last_error_code: Optional[str] = None
        self.connection = "unknown"

    # -- capabilities depend on configuration (e.g. image model must be set)
    @property
    def capabilities(self) -> frozenset:
        return self.base_capabilities

    def is_configured(self) -> bool:
        return bool(self.credentials.get(self.id))

    def _get_client(self):
        key = self.credentials.get(self.id)
        if not key:
            raise ProviderError(f"No API key configured for {self.display_name}.", "not_configured", False, self.id)
        with self._lock:
            if self._client is None or self._client_key != key:
                factory = self._client_factory or self._build_client
                self._client = factory(key)
                self._client_key = key
            return self._client

    @abstractmethod
    def _build_client(self, api_key: str):
        """Import the official SDK lazily and build a client."""

    @abstractmethod
    def _generate_text(self, req: GenerationRequest) -> GenerationResult: ...

    def _generate_image(self, prompt: str, size: str) -> ImageResult:
        raise UnsupportedCapability(f"{self.display_name} adapter does not implement image generation.", self.id)

    @abstractmethod
    def _list_models(self) -> list: ...

    # -- public wrappers: uniform error classification + status tracking
    def generate_text(self, req: GenerationRequest) -> GenerationResult:
        return self._tracked(lambda: self._generate_text(req))

    def generate_image(self, prompt: str, size: str = "1024x1024") -> ImageResult:
        if "image_generation" not in self.capabilities:
            raise UnsupportedCapability(f"{self.display_name} has no image model configured / supported.", self.id)
        return self._tracked(lambda: self._generate_image(prompt, size))

    def check_connection(self) -> ProviderStatus:
        """Calls the provider's *model list* endpoint (does not generate text; normally not billed)."""
        try:
            self._tracked(self._list_models)
            self.connection = "ok"
        except ProviderError as exc:
            self.connection = "not_configured" if exc.code == "not_configured" else "error"
        return self.status()

    def _tracked(self, fn):
        try:
            result = fn()
        except Exception as exc:
            err = classify_exception(exc, self.id)
            self.last_ok, self.last_at = False, time.time()
            self.last_error, self.last_error_code = str(err), err.code
            if err.code == "auth":
                self.connection = "error"
            raise err from None
        self.last_ok, self.last_at = True, time.time()
        self.last_error = self.last_error_code = None
        return result

    def status(self) -> ProviderStatus:
        configured = self.is_configured()
        return ProviderStatus(
            id=self.id, display_name=self.display_name, configured=configured,
            credential_source=self.credentials.source(self.id), model=self.model, image_model=self.image_model,
            capabilities=sorted(self.capabilities),
            connection=self.connection if configured else "not_configured",
            last_request_ok=self.last_ok, last_request_at=self.last_at,
            last_error=self.last_error, last_error_code=self.last_error_code, may_charge=self.may_charge,
        )
