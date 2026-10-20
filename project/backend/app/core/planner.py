"""Task planner: asks a configured provider for a structured plan, validates it against the schema
(with one bounded repair round), then enriches it with prompts, routing previews and cost estimates."""
from __future__ import annotations

import logging

from ..config import Limits
from ..providers.base import GenerationRequest, ProviderError
from . import prompts
from .budget import BudgetExceeded
from .capabilities import KINDS, TEXT, JSON
from .exporters import parse_json_loose
from .pricing import estimate_tokens
from .schemas import Plan, PlanValidationError, Project, parse_plan

log = logging.getLogger(__name__)
MAX_PLAN_ATTEMPTS = 2


class PlannerError(Exception):
    def __init__(self, message: str, errors=None):
        super().__init__(message)
        self.errors = errors or []


class Planner:
    def __init__(self, registry, router, pricing):
        self.registry, self.router, self.pricing = registry, router, pricing

    def create_plan(self, project: Project, settings: dict, caller) -> Plan:
        limits = Limits.from_dict(project.limits)
        preferred = project.planner_provider or settings.get("planner_provider")
        decision, why = self.router.route("analysis", project.mode, preferred, settings, requires=frozenset({TEXT, JSON}))
        if decision is None:
            raise PlannerError("No provider is available to plan this project. "
                               + ("Switch to demo mode or add an API key in Providers. " + why))
        adapter = self.registry.get(decision.provider)
        unsupported = [f"{k} tasks cannot be executed automatically by any configured provider (they use manual handoff)"
                       for k, spec in KINDS.items()
                       if not self.router.eligible(k, project.mode, set())[0] and k in ("image", "video_assets", "voiceover")]
        system, user = prompts.planner_prompt(project.request, project.constraints, project.output_formats, limits.max_tasks,
                                              project.attachments, project.mode, "; ".join(unsupported))
        feedback, plan, result = "", None, None
        for attempt in range(1, MAX_PLAN_ATTEMPTS + 1):
            req = GenerationRequest(system=system, prompt=user + feedback, json_mode=True, max_output_tokens=4000,
                                    metadata={"task_kind": "planning", "user_request": project.request,
                                              "max_tasks": limits.max_tasks, "attempt": attempt})
            try:
                result = caller.text(adapter, req)
            except BudgetExceeded as exc:
                raise PlannerError(f"Budget limit reached while planning: {exc}") from exc
            except ProviderError as exc:
                raise PlannerError(f"Planner provider '{adapter.display_name}' failed ({exc.code}): {exc}") from exc
            try:
                plan = parse_plan(parse_json_loose(result.text), limits.max_tasks, self.registry.ids() - {"demo"})
                break
            except (PlanValidationError, ValueError) as exc:
                errs = exc.errors if isinstance(exc, PlanValidationError) else [f"not valid JSON ({exc})"]
                log.info("plan attempt %d invalid (%d errors)", attempt, len(errs))
                feedback = "\n\nYour previous answer was rejected for these reasons. Return a corrected JSON object:\n" + \
                           "\n".join(f"- {e}" for e in errs[:15])
                plan = None
                last_errors = errs
        if plan is None:
            raise PlannerError("The planner provider did not return a valid plan after a repair attempt.", last_errors)
        plan.planner_provider, plan.planner_model = adapter.id, adapter.model
        plan.simulated = bool(result.simulated)
        self.enrich(project, plan, settings)
        return plan

    def enrich(self, project: Project, plan: Plan, settings: dict) -> None:
        total, unknown = 0.0, 0
        for t in plan.tasks:
            if KINDS[t.kind].consequential and project.mode == "integration":
                t.requires_approval = True
            t.prompt_system, t.prompt_user = prompts.task_prompt(project, plan, t)
            decision, why = self.router.route(t.kind, project.mode, t.preferred_provider, settings)
            if decision:
                t.provider, t.model, t.routing_reason, t.handoff = decision.provider, decision.model, decision.reason, False
                adapter = self.registry.get(decision.provider)
                if t.kind == "image":
                    t.estimated_cost_usd = self.pricing.cost(t.provider, adapter.image_model or t.model, 0, 0, images=1)
                else:
                    est_in = estimate_tokens(t.prompt_system + t.prompt_user) + 1500 * len(t.depends_on)
                    t.estimated_cost_usd = self.pricing.cost(t.provider, t.model, est_in, KINDS[t.kind].default_out_tokens)
            else:
                t.provider = t.model = None
                t.handoff = True
                t.routing_reason = f"No automatic provider available ({why}). Manual handoff: copy the prompt, run it in a provider's own site/tool, paste the result back."
                t.estimated_cost_usd = None
            if t.estimated_cost_usd is None:
                if decision:
                    unknown += 1
            else:
                total += t.estimated_cost_usd
        plan.estimated_cost_usd = total if (total or not unknown) else None
        if project.mode == "demo":
            plan.cost_note = "Demo mode: no external calls, no cost."
        elif unknown:
            plan.cost_note = (f"{unknown} task(s) have unknown price (no pricing configured) so the estimate is partial. "
                              "Estimates use a rough ~4 chars/token heuristic. Real usage may differ; providers may charge.")
        else:
            plan.cost_note = "Estimates use a rough token heuristic and your pricing file; actual charges may differ."
