"""Task, plan, artifact and project schemas with explicit validation.

The orchestration core deliberately uses dataclasses + hand-written validation so it has
zero third-party dependencies and can be tested anywhere. The HTTP layer (app/api.py)
uses Pydantic for request bodies.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .capabilities import KINDS, ARTIFACT_TYPES
from .graph import find_cycle


class TaskStatus:
    PLANNED = "planned"
    WAITING = "waiting"
    RUNNING = "running"
    NEEDS_APPROVAL = "needs_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    ALL = (PLANNED, WAITING, RUNNING, NEEDS_APPROVAL, COMPLETED, FAILED, BLOCKED)


ID_RE = re.compile(r"^[a-z][a-z0-9_\-]{0,39}$")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


class PlanValidationError(ValueError):
    def __init__(self, errors: list):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class TaskSpec:
    id: str
    title: str
    kind: str
    description: str
    depends_on: list = field(default_factory=list)
    inputs: list = field(default_factory=list)
    expected_outputs: list = field(default_factory=list)   # [{"name":..., "type":...}]
    acceptance_criteria: list = field(default_factory=list)
    preferred_provider: Optional[str] = None
    requires_approval: bool = False
    # --- mutable run state ---
    status: str = TaskStatus.PLANNED
    prompt_system: str = ""
    prompt_user: str = ""
    prompt_edited: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None
    routing_reason: str = ""
    result_text: str = ""
    artifact_ids: list = field(default_factory=list)
    attempts: list = field(default_factory=list)
    error: Optional[str] = None
    error_code: Optional[str] = None
    handoff: bool = False
    simulated: bool = False
    approved: bool = False
    manual_retries: int = 0
    estimated_cost_usd: Optional[float] = None
    evaluation: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TaskSpec":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Plan:
    objective: str
    requirements: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    clarifying_questions: list = field(default_factory=list)
    deliverables: list = field(default_factory=list)
    tasks: list = field(default_factory=list)   # list[TaskSpec]
    approved: bool = False
    planner_provider: Optional[str] = None
    planner_model: Optional[str] = None
    simulated: bool = False
    estimated_cost_usd: Optional[float] = None
    cost_note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tasks"] = [t.to_dict() for t in self.tasks]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        d = dict(d)
        tasks = [TaskSpec.from_dict(t) for t in d.pop("tasks", [])]
        return cls(tasks=tasks, **{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def task(self, tid: str) -> Optional[TaskSpec]:
        return next((t for t in self.tasks if t.id == tid), None)


def _str_list(value: Any, name: str, errors: list, required: bool = False, max_items: int = 30) -> list:
    if value is None:
        value = []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        errors.append(f"{name} must be a list of strings")
        return []
    if required and not [x for x in value if x.strip()]:
        errors.append(f"{name} must not be empty")
    return [x.strip()[:1000] for x in value[:max_items] if x.strip()]


def parse_plan(data: Any, max_tasks: int = 12, known_providers: Optional[set] = None) -> Plan:
    """Validate an untrusted plan dict (typically LLM output) and return a Plan.

    Raises PlanValidationError listing *all* problems so they can be fed back to the planner.
    """
    errors: list = []
    if not isinstance(data, dict):
        raise PlanValidationError(["plan must be a JSON object"])

    objective = data.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        errors.append("objective must be a non-empty string")
        objective = ""
    requirements = _str_list(data.get("requirements"), "requirements", errors)
    assumptions = _str_list(data.get("assumptions"), "assumptions", errors)
    questions = _str_list(data.get("clarifying_questions"), "clarifying_questions", errors, max_items=5)
    deliverables = _str_list(data.get("deliverables"), "deliverables", errors)

    raw_tasks = data.get("tasks")
    tasks: list = []
    if not isinstance(raw_tasks, list) or not raw_tasks:
        errors.append("tasks must be a non-empty list")
        raw_tasks = []
    if len(raw_tasks) > max_tasks:
        errors.append(f"too many tasks ({len(raw_tasks)}); limit is {max_tasks}")
        raw_tasks = raw_tasks[:max_tasks]

    seen: set = set()
    for i, rt in enumerate(raw_tasks):
        where = f"tasks[{i}]"
        if not isinstance(rt, dict):
            errors.append(f"{where} must be an object")
            continue
        tid = rt.get("id")
        if not isinstance(tid, str) or not ID_RE.match(tid):
            errors.append(f"{where}.id must match {ID_RE.pattern}")
            continue
        where = f"task '{tid}'"
        if tid in seen:
            errors.append(f"duplicate task id '{tid}'")
            continue
        seen.add(tid)
        kind = rt.get("kind")
        if kind not in KINDS:
            errors.append(f"{where}: unknown kind '{kind}' (allowed: {', '.join(sorted(KINDS))})")
            continue
        title = rt.get("title")
        desc = rt.get("description")
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{where}: title required")
            title = tid
        if not isinstance(desc, str) or not desc.strip():
            errors.append(f"{where}: description required")
            desc = ""
        deps = rt.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(x, str) for x in deps):
            errors.append(f"{where}: depends_on must be a list of task ids")
            deps = []
        outs = []
        for o in rt.get("expected_outputs", []) or []:
            if not isinstance(o, dict) or "name" not in o or "type" not in o:
                errors.append(f"{where}: expected_outputs entries need name and type")
                continue
            if o["type"] not in ARTIFACT_TYPES:
                errors.append(f"{where}: unknown artifact type '{o['type']}'")
            elif o["type"] not in KINDS[kind].outputs:
                errors.append(f"{where}: kind '{kind}' cannot produce '{o['type']}' (allowed: {', '.join(sorted(KINDS[kind].outputs))})")
            else:
                outs.append({"name": str(o["name"])[:120], "type": o["type"]})
        if not outs:
            outs = [{"name": f"{tid}_output", "type": sorted(KINDS[kind].outputs)[0]}]
        criteria = _str_list(rt.get("acceptance_criteria"), f"{where}.acceptance_criteria", errors, required=True, max_items=10)
        pref = rt.get("preferred_provider")
        if pref is not None and known_providers is not None and pref not in known_providers:
            errors.append(f"{where}: unknown preferred_provider '{pref}'")
            pref = None
        tasks.append(TaskSpec(
            id=tid, title=str(title).strip()[:200], kind=kind, description=str(desc).strip()[:4000],
            depends_on=list(dict.fromkeys(deps)), inputs=_str_list(rt.get("inputs"), f"{where}.inputs", errors),
            expected_outputs=outs, acceptance_criteria=criteria, preferred_provider=pref,
            requires_approval=bool(rt.get("requires_approval", False)),
        ))

    ids = {t.id for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            if dep == t.id:
                errors.append(f"task '{t.id}' depends on itself")
            elif dep not in ids:
                errors.append(f"task '{t.id}' depends on unknown task '{dep}'")
    cycle = find_cycle({t.id: [d for d in t.depends_on if d in ids and d != t.id] for t in tasks})
    if cycle:
        errors.append("dependency cycle: " + " -> ".join(cycle))

    if errors:
        raise PlanValidationError(errors)
    return Plan(objective=objective.strip(), requirements=requirements, assumptions=assumptions,
                clarifying_questions=questions, deliverables=deliverables, tasks=tasks)


@dataclass
class Artifact:
    id: str
    name: str
    type: str
    task_id: Optional[str]
    path: str = ""                 # relative to the project directory
    mime: str = "text/plain"
    size: int = 0
    sha256: str = ""
    simulated: bool = False
    source: str = "ai"             # "ai" | "manual" | "system"
    provider: Optional[str] = None
    model: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Artifact":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _default_usage() -> dict:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0, "unpriced_calls": 0, "simulated_calls": 0}


@dataclass
class Project:
    id: str
    name: str
    request: str
    mode: str = "demo"                 # "demo" | "integration"
    execution: str = "manual"          # "manual": review plan + approve each step | "auto"
    constraints: str = ""
    output_formats: list = field(default_factory=list)
    limits: dict = field(default_factory=dict)
    planner_provider: Optional[str] = None
    demo_options: dict = field(default_factory=dict)
    plan: Optional[Plan] = None
    artifacts: list = field(default_factory=list)    # list[Artifact]
    attachments: list = field(default_factory=list)  # [{"name","size","path"}]
    usage: dict = field(default_factory=_default_usage)
    status: str = "draft"
    events: list = field(default_factory=list)
    final: Optional[dict] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def log(self, message: str, level: str = "info", task_id: Optional[str] = None) -> None:
        self.events.append({"ts": time.time(), "level": level, "task_id": task_id, "message": message[:500]})
        if len(self.events) > 500:
            del self.events[:100]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["plan"] = self.plan.to_dict() if self.plan else None
        d["artifacts"] = [a.to_dict() for a in self.artifacts]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        d = dict(d)
        plan = Plan.from_dict(d["plan"]) if d.get("plan") else None
        arts = [Artifact.from_dict(a) for a in d.get("artifacts", [])]
        d.pop("plan", None)
        d.pop("artifacts", None)
        return cls(plan=plan, artifacts=arts, **{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def artifact(self, aid: str) -> Optional[Artifact]:
        return next((a for a in self.artifacts if a.id == aid), None)
