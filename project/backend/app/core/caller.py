"""Single choke point for provider calls: budget reservation before, usage recording after."""
from __future__ import annotations

from ..providers.base import GenerationRequest, GenerationResult, ImageResult, ProviderAdapter
from .budget import Budget
from .pricing import estimate_tokens


class ProviderCaller:
    def __init__(self, budget: Budget):
        self.budget = budget

    def text(self, adapter: ProviderAdapter, req: GenerationRequest) -> GenerationResult:
        est_in = estimate_tokens(req.system + req.prompt)
        sim = adapter.id == "demo"
        self.budget.reserve(adapter.id, adapter.model, est_in, req.max_output_tokens, simulated=sim)
        res = adapter.generate_text(req)
        tin = res.input_tokens if res.input_tokens is not None else est_in
        tout = res.output_tokens if res.output_tokens is not None else estimate_tokens(res.text)
        self.budget.record(adapter.id, adapter.model, tin, tout, simulated=res.simulated or sim)
        return res

    def image(self, adapter: ProviderAdapter, prompt: str, size: str = "1024x1024") -> ImageResult:
        sim = adapter.id == "demo"
        self.budget.reserve(adapter.id, adapter.image_model or adapter.model, estimate_tokens(prompt), 0, images=1, simulated=sim)
        res = adapter.generate_image(prompt, size)
        self.budget.record(adapter.id, adapter.image_model or adapter.model, estimate_tokens(prompt), 0, images=1,
                           simulated=res.simulated or sim)
        return res
