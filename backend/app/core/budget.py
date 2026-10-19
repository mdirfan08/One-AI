"""Hard spending / usage limits. Fail-closed: if a USD cap is set and the price of a call is
unknown, the call is refused rather than assumed free."""
from __future__ import annotations

import threading
from typing import Optional

from ..config import Limits
from .pricing import PricingTable


class BudgetExceeded(Exception):
    pass


class Budget:
    def __init__(self, limits: Limits, usage: dict, pricing: PricingTable, lock=None):
        self.limits = limits
        self.usage = usage
        self.pricing = pricing
        self.lock = lock or threading.RLock()

    def reserve(self, provider: str, model: str, est_in: int, max_out: int, images: int = 0, simulated: bool = False) -> None:
        """Raise BudgetExceeded if the *worst-case* cost of this call would break a limit."""
        if simulated:
            return
        with self.lock:
            u, lim = self.usage, self.limits
            if u["calls"] - u.get("simulated_calls", 0) + 1 > lim.max_calls:
                raise BudgetExceeded(f"Call limit reached ({lim.max_calls}).")
            used = u["input_tokens"] + u["output_tokens"]
            if used + est_in + max_out > lim.max_total_tokens:
                raise BudgetExceeded(f"Token limit would be exceeded ({used} used, limit {lim.max_total_tokens}).")
            if lim.max_usd is not None:
                if not self.pricing.known(provider, model, image=bool(images)):
                    raise BudgetExceeded(
                        f"A USD limit is set but no price is configured for {provider}/{model}. "
                        "Add it to the pricing file or clear the USD limit (token/call limits still apply).")
                worst = self.pricing.cost(provider, model, est_in, max_out, images=images) or 0.0
                if u["usd"] + worst > lim.max_usd:
                    raise BudgetExceeded(
                        f"Spending limit ${lim.max_usd:.2f} would be exceeded (spent ${u['usd']:.4f}, worst-case next call ${worst:.4f}).")

    def record(self, provider: str, model: str, in_tokens: int, out_tokens: int, images: int = 0,
               simulated: bool = False) -> Optional[float]:
        with self.lock:
            u = self.usage
            u["calls"] += 1
            if simulated:
                u["simulated_calls"] = u.get("simulated_calls", 0) + 1
                return 0.0
            u["input_tokens"] += in_tokens
            u["output_tokens"] += out_tokens
            cost = self.pricing.cost(provider, model, in_tokens, out_tokens, images=images)
            if cost is None:
                u["unpriced_calls"] += 1
            else:
                u["usd"] += cost
            return cost
