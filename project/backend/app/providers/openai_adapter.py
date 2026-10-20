"""OpenAI adapter (official `openai` SDK, Responses API for text, Images API for images)."""
from __future__ import annotations

import base64

from . import base
from .base import GenerationRequest, GenerationResult, ImageResult, ProviderAdapter, ProviderError
from ..core.capabilities import TEXT, JSON, CODE, IMAGE_GEN


class OpenAIAdapter(ProviderAdapter):
    id = "openai"
    display_name = "OpenAI"
    base_capabilities = frozenset({TEXT, JSON, CODE})
    # Defaults are suggestions only. Verify current model names in the provider docs and
    # override with OPENAI_MODEL / OPENAI_IMAGE_MODEL or the Settings page.
    default_model = "gpt-4.1"
    default_image_model = "gpt-image-1"

    @property
    def capabilities(self):
        caps = set(self.base_capabilities)
        if self.image_model:
            caps.add(IMAGE_GEN)
        return frozenset(caps)

    def _build_client(self, api_key: str):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("The 'openai' package is not installed (pip install openai).", "unavailable", False, self.id) from exc
        return OpenAI(api_key=api_key, max_retries=0)   # retries are handled (and budgeted) by the orchestrator

    def _generate_text(self, req: GenerationRequest) -> GenerationResult:
        client = self._get_client()
        kwargs = dict(model=self.model, instructions=req.system, input=req.prompt,
                      max_output_tokens=req.max_output_tokens, timeout=req.timeout_s)
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if req.json_mode:
            kwargs["text"] = {"format": {"type": "json_object"}}
        resp = client.responses.create(**kwargs)
        text = getattr(resp, "output_text", None)
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("OpenAI returned no text output.", "bad_response", True, self.id)
        usage = getattr(resp, "usage", None)
        return GenerationResult(text=text, provider=self.id, model=self.model,
                                input_tokens=getattr(usage, "input_tokens", None),
                                output_tokens=getattr(usage, "output_tokens", None),
                                finish_reason=getattr(resp, "status", None))

    def _generate_image(self, prompt: str, size: str) -> ImageResult:
        client = self._get_client()
        resp = client.images.generate(model=self.image_model, prompt=prompt, size=size, n=1)
        item = resp.data[0] if getattr(resp, "data", None) else None
        b64 = getattr(item, "b64_json", None)
        if not b64:
            raise ProviderError("OpenAI image response contained no inline image data.", "bad_response", False, self.id)
        return ImageResult(data=base64.b64decode(b64), mime="image/png", provider=self.id, model=self.image_model)

    def _list_models(self) -> list:
        client = self._get_client()
        return [m.id for m in client.models.list()][:50]
