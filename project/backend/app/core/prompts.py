"""Prompt generation. Every task gets its own instruction built from its kind, objective,
project context, constraints, quality criteria and upstream outputs. Prompts are plain text
so they can be shown, edited by the user, or copied for manual handoff."""
from __future__ import annotations

from typing import Optional

from .capabilities import KINDS
from .schemas import Plan, Project, TaskSpec

FILE_FORMAT = """Output ONLY file blocks, exactly in this format, one block per file:
=== FILE: relative/path.ext ===
<complete file content>
=== END FILE ===
Rules: relative paths only (no leading '/', no '..'); at most 40 files; every file must be complete (no placeholders like
'rest of file'); never include API keys or secrets; frontend code must not reference server-side secrets. Prefer plain
HTML/CSS/JS or a Vite + TypeScript project (include package.json with build/test scripts if you use npm)."""

KIND_FORMAT = {
    "code_files": FILE_FORMAT,
    "presentation": 'Output ONLY a JSON object: {"title": str, "slides": [{"title": str, "bullets": [str], "notes": str}]}. 6-12 slides, <= 6 bullets each.',
    "document": "Output well-structured Markdown: one H1 title, H2 sections, short paragraphs, lists where useful.",
    "research": "Output Markdown. Separate 'Established facts' from 'Unverified / needs checking'. You have NO web access here: do not invent citations or URLs; list only sources the user supplied, and say so if there are none.",
    "analysis": "Output Markdown with findings first, then evidence, then caveats.",
    "code_spec": "Output Markdown: overview, architecture, file tree (code block), per-file responsibilities, implementation steps, acceptance tests.",
    "code_review": "Output Markdown: issues ordered by severity, each with location, explanation and suggested fix.",
    "video_script": "Output Markdown: logline, scenes with narration/dialogue and on-screen action, total duration estimate. This is a script only; do not claim a video exists.",
    "video_storyboard": "Output a Markdown table: shot #, duration, visual description, camera, audio/narration. This is a storyboard only; do not claim footage exists.",
    "video_assembly": "Output Markdown: an ordered editing/assembly plan (tools, steps, timings, export settings). You are describing the work, not performing it.",
    "social": "Output Markdown: one section per platform with ready-to-post text, hashtags and suggested posting time.",
    "writing": "Output Markdown ready to use. Match the requested tone.",
    "image": "Output nothing but the image.",
    "general": "Answer directly in Markdown.",
}

UNCERTAINTY = ("If anything is ambiguous, state your assumption explicitly at the top under 'Assumptions'. If you cannot do part "
               "of the task, say exactly what is missing instead of guessing or pretending. Never present guesses as verified facts.")

PLANNER_SCHEMA = """{
  "objective": "string",
  "requirements": ["string"],
  "assumptions": ["string"],
  "clarifying_questions": ["string"],   // ONLY if essential; otherwise []
  "deliverables": ["string"],
  "tasks": [{
    "id": "lower_snake_or_kebab, starts with a letter, unique",
    "title": "string",
    "kind": "one of the allowed kinds",
    "description": "what exactly this task must accomplish",
    "depends_on": ["task ids whose OUTPUT this task needs"],
    "inputs": ["what this task needs"],
    "expected_outputs": [{"name": "string", "type": "an artifact type allowed for the kind"}],
    "acceptance_criteria": ["concrete, checkable statements (at least one)"],
    "preferred_provider": null,
    "requires_approval": false
  }]
}"""


def planner_prompt(request: str, constraints: str, output_formats: list, max_tasks: int,
                   attachments: list, mode: str, unsupported_note: str = "") -> tuple:
    kinds = "\n".join(
        f"- {k}: {v.label}. {v.description} Allowed output types: {', '.join(sorted(v.outputs))}." for k, v in KINDS.items())
    system = (
        "You are the planning engine of an AI orchestration platform. Convert the user's request into a task graph.\n"
        "Return ONE JSON object matching this schema (no prose, no code fences):\n" + PLANNER_SCHEMA + "\n\n"
        f"Allowed task kinds:\n{kinds}\n\n"
        f"Rules:\n- At most {max_tasks} tasks. Use the fewest tasks that fully cover the request.\n"
        "- Declare depends_on ONLY where a task needs another task's output, so independent tasks can run in parallel.\n"
        "- The graph must be acyclic and reference only existing ids.\n"
        "- Only promise artifact types the kind can produce. Video: script/storyboard/assembly-plan tasks produce TEXT only; "
        "never promise a rendered video unless a video_assets task is included (which may require manual handoff).\n"
        "- Ask clarifying_questions only if the request cannot be fulfilled sensibly otherwise; prefer visible assumptions.\n"
        "- Acceptance criteria must be specific enough to check.\n"
        + (f"- Note: {unsupported_note}\n" if unsupported_note else ""))
    user = f"USER REQUEST:\n{request}\n"
    if constraints:
        user += f"\nCONSTRAINTS / PREFERENCES:\n{constraints}\n"
    if output_formats:
        user += f"\nREQUESTED OUTPUT FORMATS: {', '.join(output_formats)}\n"
    if attachments:
        user += "\nATTACHED FILES (names only): " + ", ".join(a["name"] for a in attachments) + "\n"
    return system, user


def task_prompt(project: Project, plan: Plan, task: TaskSpec) -> tuple:
    """Base (editable) prompt for a task. Upstream outputs are appended at run time."""
    if task.kind == "image":
        brief = [task.description, f"Project goal: {plan.objective}"]
        if project.constraints:
            brief.append(f"Constraints: {project.constraints}")
        brief.append("Quality criteria: " + "; ".join(task.acceptance_criteria))
        brief.append("Do not include text unless explicitly requested. No watermarks.")
        return "You generate images from the brief.", "\n".join(brief)
    system = (
        f"You are a specialist contributing to a multi-stage project. Your stage: '{task.title}' (kind: {task.kind}).\n"
        "Do exactly this stage's job; other stages are handled by other specialists.\n" + UNCERTAINTY)
    parts = [f"## Objective\n{task.description}",
             f"## Overall project goal\n{plan.objective}"]
    if plan.requirements:
        parts.append("## Project requirements\n" + "\n".join(f"- {r}" for r in plan.requirements))
    if plan.assumptions:
        parts.append("## Working assumptions\n" + "\n".join(f"- {a}" for a in plan.assumptions))
    if task.inputs:
        parts.append("## Inputs and references\n" + "\n".join(f"- {i}" for i in task.inputs))
    constraints = []
    if project.constraints:
        constraints.append(project.constraints)
    if project.output_formats:
        constraints.append("Requested formats: " + ", ".join(project.output_formats))
    if constraints:
        parts.append("## Technical and project constraints\n" + "\n".join(f"- {c}" for c in constraints))
    parts.append("## Required output format\n" + KIND_FORMAT.get(task.kind, KIND_FORMAT["general"]))
    parts.append("## Quality criteria (your output will be checked against these)\n" + "\n".join(f"- {c}" for c in task.acceptance_criteria))
    parts.append("## Do not change\n- Names, identifiers, facts and decisions already fixed in the upstream outputs below.\n"
                 "- The user's stated constraints. Do not add scope that was not requested.")
    parts.append("## Reporting uncertainty or errors\n" + UNCERTAINTY)
    return system, "\n\n".join(parts)


def with_context(base_user: str, upstream: list, attachments: list, feedback: Optional[str] = None,
                 max_chars: int = 6000) -> str:
    """Append upstream outputs, attachments and retry feedback to the (possibly user-edited) base prompt."""
    out = [base_user]
    if upstream:
        blocks = []
        for t, text in upstream:
            snippet = text if len(text) <= max_chars else text[:max_chars] + f"\n[... truncated, {len(text) - max_chars} more characters]"
            blocks.append(f"### From stage '{t.title}' ({t.id}){' [SIMULATED]' if t.simulated else ''}\n{snippet}")
        out.append("## Context from earlier stages\n" + "\n\n".join(blocks))
    if attachments:
        out.append("## User-supplied files\n" + "\n\n".join(
            f"### {name}\n{text[:max_chars]}" for name, text in attachments))
    if feedback:
        out.append("## Previous attempt failed review: fix these problems\n" + feedback)
    return "\n\n".join(out)


def evaluator_prompt(task: TaskSpec, output: str) -> tuple:
    system = ("You are a strict reviewer. Judge whether the OUTPUT satisfies every acceptance criterion. "
              'Return ONE JSON object only: {"passed": bool, "criteria": [{"criterion": str, "met": bool, "reason": str}]}. '
              "Be skeptical; mark a criterion unmet if it is not clearly satisfied.")
    user = (f"TASK: {task.title}\n{task.description}\n\nACCEPTANCE CRITERIA:\n" + "\n".join(f"- {c}" for c in task.acceptance_criteria)
            + f"\n\nOUTPUT:\n{output[:12000]}")
    return system, user


def repair_prompt(task: TaskSpec, errors: str, files: dict, max_chars: int = 30000) -> str:
    listing, used = [], 0
    for path, content in files.items():
        if used + len(content) > max_chars:
            listing.append(f"=== FILE: {path} === [omitted: size budget]")
            continue
        used += len(content)
        listing.append(f"=== FILE: {path} ===\n{content}\n=== END FILE ===")
    return ("The project below failed its build/test commands. Fix the problems. Output ONLY the files that must change, "
            "complete, in the same === FILE: path === format. Do not add unrelated changes.\n\n"
            f"## Command output (truncated)\n{errors[-6000:]}\n\n## Current files\n" + "\n".join(listing) + "\n\n" + FILE_FORMAT)


def manual_handoff(system: str, user: str) -> str:
    return f"{system}\n\n---\n\n{user}"
