"""Anthropic adapter (official `anthropic` SDK, Messages API). No image generation."""
from __future__ import annotations

from .base import GenerationRequest, GenerationResult, ProviderAdapter, ProviderError
from ..core.capabilities import TEXT, JSON, CODE


class AnthropicAdapter(ProviderAdapter):
    id = "anthropic"
    display_name = "Anthropic (Claude)"
    # JSON is produced by instruction + validation in the planner (the API has no free-form JSON mode flag).
    base_capabilities = frozenset({TEXT, JSON, CODE})
    default_model = "claude-sonnet-5-5"   # suggestion only; override via ANTHROPIC_MODEL or Settings
    default_image_model = ""

    def _build_client(self, api_key: str):
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError("The 'anthropic' package is not installed (pip install anthropic).", "unavailable", False, self.id) from exc
        return anthropic.Anthropic(api_key=api_key, max_retries=0)

    def _generate_text(self, req: GenerationRequest) -> GenerationResult:
        client = self._get_client()
        system = req.system
        if req.json_mode:
            system += "\n\nRespond with a single valid JSON object only. No prose, no code fences."
        kwargs = dict(model=self.model, max_tokens=req.max_output_tokens, system=system,
                      messages=[{"role": "user", "content": req.prompt}], timeout=req.timeout_s)
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        resp = client.messages.create(**kwargs)
        parts = [b.text for b in getattr(resp, "content", []) if getattr(b, "type", None) == "text"]
        text = "".join(parts)
        if not text.strip():
            raise ProviderError("Anthropic returned no text content.", "bad_response", True, self.id)
        usage = getattr(resp, "usage", None)
        return GenerationResult(text=text, provider=self.id, model=self.model,
                                input_tokens=getattr(usage, "input_tokens", None),
                                output_tokens=getattr(usage, "output_tokens", None),
                                finish_reason=getattr(resp, "stop_reason", None))

    def _list_models(self) -> list:
        client = self._get_client()
        return [m.id for m in client.models.list(limit=50)]
