"""Result evaluation against explicit acceptance criteria.

Layer 1 - deterministic structural checks (always): non-empty, parseable, declared artifact types
          actually produced, verification/build results.
Layer 2 - optional LLM judge (costs a provider call, counted against the budget). A judge that
          cannot run is recorded as skipped; it never silently passes or fails a task."""
from __future__ import annotations

from typing import Callable, Optional

from .schemas import TaskSpec


class Evaluator:
    def evaluate(self, task: TaskSpec, text: str, produced_types: set, problems: list,
                 judge: Optional[Callable[[], Optional[dict]]] = None) -> dict:
        checks = []
        has_output = bool(text and text.strip()) or bool(produced_types)
        checks.append({"name": "output_not_empty", "passed": has_output, "blocking": True,
                       "detail": "" if has_output else "The provider returned no usable output."})
        for msg, blocking in problems:
            checks.append({"name": "postprocess", "passed": False, "blocking": blocking, "detail": msg})
        for out in task.expected_outputs:
            ok = out["type"] in produced_types
            # A missing optional exporter is reported by the postprocessor as a non-blocking problem.
            nonblocking = any((not b) and out["type"] in m for m, b in problems)
            checks.append({"name": f"expected_output:{out['type']}", "passed": ok,
                           "blocking": not nonblocking,
                           "detail": "" if ok else f"Declared output '{out['name']}' ({out['type']}) was not produced."})
        structural_ok = all(c["passed"] or not c["blocking"] for c in checks)
        method = "structural"
        if structural_ok and judge is not None and task.acceptance_criteria:
            verdict = judge()
            if verdict is None or verdict.get("skipped"):
                checks.append({"name": "llm_judge", "passed": True, "blocking": False,
                               "detail": f"Judge skipped: {(verdict or {}).get('skipped', 'not available')}"})
            else:
                method = "structural+simulated_judge" if verdict.get("simulated") else "structural+llm_judge"
                unmet = [c for c in verdict.get("criteria", []) if not c.get("met", True)]
                passed = bool(verdict.get("passed", not unmet)) and not unmet
                checks.append({"name": "llm_judge", "passed": passed, "blocking": True,
                               "detail": "; ".join(f"{c.get('criterion', '?')}: {c.get('reason', '')}" for c in unmet)[:800]})
        passed = all(c["passed"] or not c["blocking"] for c in checks)
        return {"passed": passed, "method": method, "checks": checks,
                "feedback": "\n".join(f"- {c['detail']}" for c in checks if not c["passed"] and c["blocking"] and c["detail"])}
