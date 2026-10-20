"""Google Gemini adapter (official `google-genai` SDK)."""
from __future__ import annotations

from .base import GenerationRequest, GenerationResult, ImageResult, ProviderAdapter, ProviderError
from ..core.capabilities import TEXT, JSON, CODE, IMAGE_GEN


class GeminiAdapter(ProviderAdapter):
    id = "gemini"
    display_name = "Google Gemini"
    base_capabilities = frozenset({TEXT, JSON, CODE})
    # Suggestions only; override via GEMINI_MODEL / GEMINI_IMAGE_MODEL or Settings. Image-capable
    # model names and free-tier availability vary: check Google's docs before enabling.
    default_model = "gemini-3.8-flash"
    default_image_model = ""   # empty = image generation disabled until you choose a model

    @property
    def capabilities(self):
        caps = set(self.base_capabilities)
        if self.image_model:
            caps.add(IMAGE_GEN)
        return frozenset(caps)

    def _build_client(self, api_key: str):
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderError("The 'google-genai' package is not installed (pip install google-genai).", "unavailable", False, self.id) from exc
        return genai.Client(api_key=api_key)

    @staticmethod
    def _types():
        from google.genai import types
        return types

    def _generate_text(self, req: GenerationRequest) -> GenerationResult:
        client = self._get_client()
        try:
            types = self._types()
            cfg = dict(system_instruction=req.system, max_output_tokens=req.max_output_tokens)
            if req.temperature is not None:
                cfg["temperature"] = req.temperature
            if req.json_mode:
                cfg["response_mime_type"] = "application/json"
            config = types.GenerateContentConfig(**cfg)
        except ImportError as exc:
            raise ProviderError("The 'google-genai' package is not installed.", "unavailable", False, self.id) from exc
        resp = client.models.generate_content(model=self.model, contents=req.prompt, config=config)
        text = getattr(resp, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("Gemini returned no text (it may have been blocked or truncated).", "bad_response", True, self.id)
        um = getattr(resp, "usage_metadata", None)
        return GenerationResult(text=text, provider=self.id, model=self.model,
                                input_tokens=getattr(um, "prompt_token_count", None),
                                output_tokens=getattr(um, "candidates_token_count", None))

    def _generate_image(self, prompt: str, size: str) -> ImageResult:
        client = self._get_client()
        types = self._types()
        config = types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"])
        resp = client.models.generate_content(model=self.image_model, contents=prompt, config=config)
        for cand in getattr(resp, "candidates", None) or []:
            for part in getattr(getattr(cand, "content", None), "parts", None) or []:
                inline = getattr(part, "inline_data", None)
                if inline is not None and getattr(inline, "data", None):
                    return ImageResult(data=inline.data, mime=getattr(inline, "mime_type", None) or "image/png",
                                       provider=self.id, model=self.image_model)
        raise ProviderError("Gemini returned no image data for this model/prompt.", "bad_response", False, self.id)

    def _list_models(self) -> list:
        client = self._get_client()
        return [getattr(m, "name", str(m)) for m in client.models.list()][:50]
