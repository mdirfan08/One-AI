"""Provider router. Selection is driven by required capabilities, configuration, availability,
user preferences and (when known) price. No provider is hard-wired as 'best' for a task."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .capabilities import KINDS, IMAGE_GEN
from .pricing import PricingTable


@dataclass
class RoutingDecision:
    provider: str
    model: str
    reason: str
    alternatives: list = field(default_factory=list)


class Router:
    def __init__(self, registry, pricing: PricingTable):
        self.registry = registry
        self.pricing = pricing

    def eligible(self, kind: str, mode: str, exclude: set, requires: Optional[frozenset] = None) -> tuple:
        """Return (eligible_adapters, rejection_reasons)."""
        need = requires if requires is not None else KINDS[kind].requires
        ok, why = [], []
        for a in self.registry.all():
            if mode == "demo" and a.id != "demo":
                continue
            if mode != "demo" and a.id == "demo":
                continue
            if a.id in exclude:
                why.append(f"{a.display_name}: excluded after an earlier failure")
            elif not a.is_configured():
                why.append(f"{a.display_name}: no credentials configured")
            elif not need <= a.capabilities:
                missing = ", ".join(sorted(need - a.capabilities))
                why.append(f"{a.display_name}: does not support {missing}")
            else:
                ok.append(a)
        return ok, why

    def route(self, kind: str, mode: str, preferred: Optional[str] = None, settings: Optional[dict] = None,
              exclude: Optional[set] = None, requires: Optional[frozenset] = None) -> tuple:
        """Return (RoutingDecision | None, explanation). explanation lists why providers were rejected."""
        settings = settings or {}
        exclude = exclude or set()
        ok, why = self.eligible(kind, mode, exclude, requires)
        if not ok:
            return None, "; ".join(why) or "No providers available in this mode."

        order = [p for p in [preferred, (settings.get("provider_preferences") or {}).get(kind)] if p]
        order += list(settings.get("provider_order") or [])
        rank = {pid: i for i, pid in enumerate(dict.fromkeys(order))}

        def key(a):
            cost = self.pricing.cost(a.id, a.model, 1000, 1000)
            return (rank.get(a.id, len(rank)), cost if cost is not None else float("inf"), a.id)

        ok.sort(key=key)
        chosen = ok[0]
        if preferred and chosen.id == preferred:
            reason = "Chosen manually for this task."
        elif preferred:
            detail = "; ".join(w for w in why if w.lower().startswith(preferred.lower()[:4]))
            reason = f"Requested provider '{preferred}' is not eligible" + (f" ({detail})" if detail else "") + f". Using {chosen.display_name}."
        elif chosen.id in rank:
            reason = f"Matches your configured provider preference; supports {', '.join(sorted(KINDS[kind].requires))}."
        elif chosen.id == "demo":
            reason = "Demo mode: simulated provider."
        else:
            reason = "Eligible provider with required capabilities" + (" and lowest known price." if len(ok) > 1 and
                      self.pricing.cost(chosen.id, chosen.model, 1000, 1000) is not None else ".")
        return RoutingDecision(chosen.id, chosen.model, reason, [a.id for a in ok[1:]]), ""
