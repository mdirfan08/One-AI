"""Optional, user-supplied pricing. NO prices are bundled: provider pricing changes and
this app cannot verify it offline. Without an entry, cost is reported as unknown."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Very rough heuristic (~4 chars/token). Used only for pre-call reservation/estimates."""
    return max(1, len(text) // 4)


class PricingTable:
    """JSON: {"openai": {"<model>": {"input_per_mtok": 1.0, "output_per_mtok": 2.0, "per_image": 0.04}}}
    Values are USD. null / missing = unknown."""

    def __init__(self, table: Optional[dict] = None):
        self.table = table or {}

    @classmethod
    def load(cls, path) -> "PricingTable":
        if not path:
            return cls()
        try:
            data = json.loads(Path(path).read_text())
            return cls({k: v for k, v in data.items() if isinstance(v, dict) and not k.startswith("_")})
        except Exception as exc:
            log.warning("Could not load pricing file: %s", exc)
            return cls()

    def entry(self, provider: str, model: str) -> Optional[dict]:
        e = self.table.get(provider, {}).get(model)
        return e if isinstance(e, dict) else None

    def known(self, provider: str, model: str, image: bool = False) -> bool:
        if provider == "demo":
            return True
        e = self.entry(provider, model)
        if not e:
            return False
        if image:
            return e.get("per_image") is not None
        return e.get("input_per_mtok") is not None and e.get("output_per_mtok") is not None

    def cost(self, provider: str, model: str, in_tokens: int, out_tokens: int, images: int = 0) -> Optional[float]:
        if provider == "demo":
            return 0.0
        e = self.entry(provider, model)
        if not e:
            return None
        if images:
            return None if e.get("per_image") is None else images * float(e["per_image"])
        if e.get("input_per_mtok") is None or e.get("output_per_mtok") is None:
            return None
        return (in_tokens * float(e["input_per_mtok"]) + out_tokens * float(e["output_per_mtok"])) / 1_000_000
