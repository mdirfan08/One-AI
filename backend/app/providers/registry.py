"""Provider registry: holds adapter instances; new providers only need to be added here."""
from __future__ import annotations

from typing import Callable, Optional

from .anthropic_adapter import AnthropicAdapter
from .base import CredentialStore, ProviderAdapter
from .demo import DemoProvider
from .gemini_adapter import GeminiAdapter
from .openai_adapter import OpenAIAdapter

REAL_PROVIDER_CLASSES = (OpenAIAdapter, AnthropicAdapter, GeminiAdapter)


class ProviderRegistry:
    def __init__(self, credentials: Optional[CredentialStore] = None, extra: Optional[list] = None):
        self.credentials = credentials or CredentialStore()
        self._adapters: dict = {"demo": DemoProvider(self.credentials)}
        for cls in REAL_PROVIDER_CLASSES:
            self._adapters[cls.id] = cls(self.credentials)
        for adapter in extra or []:        # e.g. mock adapters in tests, or future providers
            self._adapters[adapter.id] = adapter

    def register(self, adapter: ProviderAdapter) -> None:
        self._adapters[adapter.id] = adapter

    def get(self, pid: str) -> Optional[ProviderAdapter]:
        return self._adapters.get(pid)

    def all(self) -> list:
        return list(self._adapters.values())

    def ids(self) -> set:
        return set(self._adapters)

    def apply_settings(self, models: dict) -> None:
        for pid, cfg in (models or {}).items():
            a = self._adapters.get(pid)
            if a and pid != "demo" and isinstance(cfg, dict):
                if isinstance(cfg.get("model"), str) and cfg["model"].strip():
                    a.model = cfg["model"].strip()[:100]
                if isinstance(cfg.get("image_model"), str):
                    a.image_model = cfg["image_model"].strip()[:100]
