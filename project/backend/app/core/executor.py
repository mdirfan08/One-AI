"""Workflow executor.

Dependency-driven scheduler: a task starts as soon as all its dependencies are COMPLETED; independent
tasks run in parallel (bounded by limits.max_parallel). Runs are *resumable*: tasks that need approval or
a manual handoff stop the task (not the run); the run ends 'paused' and can be resumed after the user acts.

Hard bounds: max tasks (planner), max attempts per task, wall-clock deadline, token/call/USD budget,
cancellation between steps. There is no open-ended agent loop."""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..config import Limits
from ..providers.base import GenerationRequest, ProviderError
from . import prompts
from .budget import Budget, BudgetExceeded
from .caller import ProviderCaller
from .capabilities import KINDS
from .evaluator import Evaluator
from .exporters import build_docx, build_pptx, parse_json_loose
from .schemas import Artifact, Project, TaskSpec, TaskStatus as S, new_id
from .workspace import CommandRunner, Workspace, WorkspaceError, parse_file_blocks

log = logging.getLogger(__name__)
RESETTABLE_BLOCKS = {"dependency", "budget", "timeout", "cancelled", "no_provider"}
URL_RE = re.compile(r"https?://[^\s)>\]\"']+")


class RunControl:
    def __init__(self) -> None:
        self.cancel = threading.Event()


@dataclass
class PendingArtifact:
    name: str
    type: str
    mime: str
    data: bytes
    meta: dict = field(default_factory=dict)


@dataclass
class Post:
    text: str
    artifacts: list = field(default_factory=list)
    problems: list = field(default_factory=list)      # [(message, blocking)]
    simulated: bool = False
    meta: dict = field(default_factory=dict)


@dataclass
class Ctx:
    project: Project
    ctl: RunControl
    save: Callable
    lock: threading.RLock
    settings: dict
    limits: Limits
    caller: ProviderCaller
    ws: Workspace
    proj_dir: Path
    attachments: list
    inject_target: Optional[str]
    halt: Optional[str] = None


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80] or "artifact"


class Executor:
    def __init__(self, registry, router, pricing, config, evaluator: Optional[Evaluator] = None,
                 runner: Optional[CommandRunner] = None):
        self.registry, self.router, self.pricing, self.config = registry, router, pricing, config
        self.evaluator = evaluator or Evaluator()
        self.runner = runner or CommandRunner(enabled=config.enable_command_execution)

    # ================================================================== public
    def run(self, project: Project, ctl: RunControl, save: Callable, lock: threading.RLock, settings: dict,
            proj_dir: Path, attachments: Optional[list] = None) -> str:
        plan = project.plan
        limits = Limits.from_dict(project.limits)
        budget = Budget(limits, project.usage, self.pricing, lock)
        inject = None
        if project.demo_options.get("inject_failure") and plan.tasks:
            inject = plan.tasks[min(1, len(plan.tasks) - 1)].id
        ctx = Ctx(project, ctl, save, lock, settings, limits, ProviderCaller(budget), Workspace(proj_dir / "workspace"),
                  proj_dir, attachments or [], inject)
        deadline = time.monotonic() + limits.max_duration_s
        with lock:
            project.status, project.final = "running", None
            for t in plan.tasks:
                if t.status == S.RUNNING:
                    t.status = S.PLANNED
                if t.status == S.BLOCKED and t.error_code in RESETTABLE_BLOCKS:
                    t.status, t.error, t.error_code, t.handoff = S.PLANNED, None, None, False
            project.log("Run started")
            save()

        inflight: dict = {}
        with ThreadPoolExecutor(max_workers=limits.max_parallel) as pool:
            while True:
                if ctl.cancel.is_set():
                    ctx.halt = ctx.halt or "cancelled"
                elif time.monotonic() > deadline:
                    ctx.halt = ctx.halt or "timeout"
                with lock:
                    self._refresh(ctx)
                    ready = [] if ctx.halt else [
                        t for t in plan.tasks if t.status == S.PLANNED and t.id not in inflight.values()
                        and all(plan.task(d).status == S.COMPLETED for d in t.depends_on)]
                    for t in ready[:max(0, limits.max_parallel - len(inflight))]:
                        t.status = S.RUNNING
                        inflight[pool.submit(self._run_task, ctx, t)] = t.id
                    if ready:
                        save()
                if not inflight:
                    break
                done, _ = wait(list(inflight), timeout=0.2, return_when=FIRST_COMPLETED)
                for fut in done:
                    tid = inflight.pop(fut)
                    try:
                        fut.result()
                    except Exception as exc:   # defensive: a bug must not wedge the run
                        log.exception("task crashed")
                        with lock:
                            t = plan.task(tid)
                            t.status, t.error_code, t.error = S.FAILED, "internal", f"Internal error: {type(exc).__name__}"
                            save()
        with lock:
            self._refresh(ctx)
            status = self._finalize(ctx)
            project.updated_at = time.time()
            save()
        return status

    # ================================================================ scheduling
    def _refresh(self, ctx: Ctx) -> None:
        """Update WAITING/PLANNED/BLOCKED based on dependencies and halt state (call with lock held)."""
        plan = ctx.project.plan
        msgs = {"cancelled": "Run was cancelled.", "timeout": "Run exceeded its maximum duration.",
                "budget": "Budget limit reached; task not started."}
        changed = True
        while changed:
            changed = False
            for t in plan.tasks:
                if t.status not in (S.PLANNED, S.WAITING):
                    continue
                if ctx.halt:
                    t.status, t.error_code, t.error = S.BLOCKED, ctx.halt, msgs.get(ctx.halt, "Run halted.")
                    changed = True
                    continue
                deps = [plan.task(d) for d in t.depends_on]
                bad = next((d for d in deps if d.status in (S.FAILED, S.BLOCKED)), None)
                if bad:
                    t.status, t.error_code = S.BLOCKED, "dependency"
                    t.error = f"Blocked: upstream task '{bad.title}' is {bad.status}."
                    changed = True
                elif all(d.status == S.COMPLETED for d in deps):
                    t.status = S.PLANNED
                else:
                    t.status = S.WAITING

    def _finalize(self, ctx: Ctx) -> str:
        p, plan = ctx.project, ctx.project.plan
        tasks = plan.tasks
        by = lambda st: [t for t in tasks if t.status == st]
        awaiting = by(S.NEEDS_APPROVAL) + [t for t in by(S.BLOCKED) if t.handoff]
        if ctx.halt == "cancelled":
            status = "cancelled"
        elif ctx.halt == "budget":
            status = "budget_stopped"
        elif ctx.halt == "timeout" or by(S.FAILED):
            status = "failed"
        elif awaiting:
            status = "paused"
        elif by(S.BLOCKED):
            status = "partial"
        else:
            status = "completed"
        done = by(S.COMPLETED)
        deliverables = []
        for t in done:
            for aid in t.artifact_ids:
                a = p.artifact(aid)
                if a:
                    deliverables.append({"task_id": t.id, "task": t.title, "artifact_id": a.id, "name": a.name, "type": a.type,
                                         "simulated": a.simulated, "source": a.source})
        not_produced = [{"task_id": t.id, "task": t.title, "status": t.status,
                         "reason": t.error or ("Awaiting approval" if t.status == S.NEEDS_APPROVAL else "Not run")}
                        for t in tasks if t.status != S.COMPLETED]
        simulated = any(a["simulated"] for a in deliverables)
        caveats = []
        if simulated:
            caveats.append("Some or all output is SIMULATED (demo provider) and is not real AI output.")
        if any(t.kind == "research" for t in done):
            caveats.append("Research output is model-generated and NOT verified against live sources; any URLs may be wrong.")
        if any(KINDS[t.kind].outputs & {"video"} or t.kind.startswith("video") for t in tasks) and \
                not any(d["type"] == "video" for d in deliverables):
            caveats.append("No video file was produced by this platform. Scripts/storyboards/plans are text only.")
        u = p.usage
        lines = [f"# Final summary: {p.name}", "", f"**Status:** {status}  |  **Tasks completed:** {len(done)}/{len(tasks)}", "",
                 f"**Objective:** {plan.objective}", "", "## Deliverables actually produced"]
        lines += [f"- {d['name']} ({d['type']}) from '{d['task']}'" + (" [SIMULATED]" if d["simulated"] else "") +
                  (" [manual]" if d["source"] == "manual" else "") for d in deliverables] or ["- None"]
        if not_produced:
            lines += ["", "## Not produced"] + [f"- {n['task']} ({n['status']}): {n['reason']}" for n in not_produced]
        if caveats:
            lines += ["", "## Caveats"] + [f"- {c}" for c in caveats]
        lines += ["", "## Usage", f"- Provider calls: {u['calls']} ({u.get('simulated_calls', 0)} simulated)",
                  f"- Tokens (real calls): {u['input_tokens']} in / {u['output_tokens']} out",
                  f"- Cost: ${u['usd']:.4f} known" + (f" (+{u['unpriced_calls']} call(s) with unknown price)" if u["unpriced_calls"] else "")]
        summary = "\n".join(lines)
        p.final = {"status": status, "summary_md": summary, "deliverables": deliverables, "not_produced": not_produced,
                   "simulated": simulated, "caveats": caveats, "usage": dict(u)}
        p.status = status
        p.log(f"Run finished: {status}")
        # replace previous system summary artifact
        for old in [a for a in p.artifacts if a.source == "system" and a.name == "final_summary.md"]:
            p.artifacts.remove(old)
        self._write_artifact(ctx, None, PendingArtifact("final_summary.md", "markdown", "text/markdown", summary.encode()),
                             source="system", simulated=simulated)
        return status

    # ================================================================= one task
    def _set(self, ctx: Ctx, task: TaskSpec, status: str, code: Optional[str] = None, error: Optional[str] = None) -> None:
        with ctx.lock:
            task.status, task.error_code, task.error = status, code, error
            ctx.project.log(f"{task.title}: {status}" + (f" ({code})" if code else ""), "warn" if status in (S.FAILED, S.BLOCKED) else "info", task.id)
            ctx.save()

    def _run_task(self, ctx: Ctx, task: TaskSpec) -> None:
        p, plan, lim = ctx.project, ctx.project.plan, ctx.limits
        exclude: set = set()
        provider_fail: dict = {}
        max_attempts = lim.max_retries + 1
        done_attempts, feedback, last_err = 0, None, None
        while done_attempts < max_attempts:
            if ctx.ctl.cancel.is_set() or ctx.halt:
                return self._set(ctx, task, S.BLOCKED, ctx.halt or "cancelled", "Run stopped before this task finished.")
            decision, why = self.router.route(task.kind, p.mode, task.preferred_provider, ctx.settings, exclude)
            if decision is None:
                if done_attempts == 0:
                    with ctx.lock:
                        task.handoff, task.provider, task.model = True, None, None
                        task.routing_reason = why
                    return self._set(ctx, task, S.BLOCKED, "no_provider",
                                     f"No eligible provider ({why}). Use manual handoff: copy the prompt, run it in a provider's own "
                                     "product, then paste the result back.")
                return self._set(ctx, task, S.FAILED, last_err.code if last_err else "error",
                                 f"Failed after {done_attempts} attempt(s); no alternative provider. Last error: {last_err}")
            adapter = self.registry.get(decision.provider)
            with ctx.lock:
                task.provider, task.model, task.routing_reason, task.handoff = decision.provider, decision.model, decision.reason, False
            if done_attempts == 0 and not task.approved and (p.execution == "manual" or task.requires_approval):
                self._set(ctx, task, S.NEEDS_APPROVAL, None, None)
                return
            rec = {"n": len(task.attempts) + 1, "provider": adapter.id, "model": adapter.model, "started": time.time(),
                   "ok": None, "error": None, "error_code": None, "simulated": adapter.id == "demo"}
            with ctx.lock:
                task.attempts.append(rec)
                task.status = S.RUNNING
                ctx.save()
            try:
                post, evaluation = self._attempt(ctx, task, adapter, feedback, done_attempts + 1, rec)
            except BudgetExceeded as exc:
                rec.update(ok=False, error=str(exc), error_code="budget", finished=time.time())
                ctx.halt = "budget"
                return self._set(ctx, task, S.FAILED, "budget", f"Budget hard stop: {exc}")
            except ProviderError as exc:
                done_attempts += 1
                last_err = exc
                rec.update(ok=False, error=str(exc), error_code=exc.code, finished=time.time())
                provider_fail[adapter.id] = provider_fail.get(adapter.id, 0) + 1
                log.info("task %s attempt failed: %s", task.id, exc.code)
                if exc.code == "bad_request":
                    return self._set(ctx, task, S.FAILED, exc.code, str(exc))
                if exc.code in ("auth", "quota", "unsupported", "not_configured") or provider_fail[adapter.id] >= 2:
                    exclude.add(adapter.id)           # try another eligible provider if any
                if done_attempts < max_attempts and exc.retryable:
                    ctx.ctl.cancel.wait(self.config.retry_backoff_s * done_attempts)
                continue
            except WorkspaceError as exc:
                done_attempts += 1
                feedback = f"- Your files were rejected: {exc}"
                rec.update(ok=False, error=feedback, error_code="invalid_output", finished=time.time())
                continue
            rec["finished"] = time.time()
            with ctx.lock:
                task.evaluation = evaluation
                task.result_text = post.text
                task.simulated = post.simulated
            if evaluation["passed"]:
                rec.update(ok=True)
                with ctx.lock:
                    task.artifact_ids = []
                    for pa in post.artifacts:
                        a = self._write_artifact(ctx, task, pa, source="ai", simulated=post.simulated,
                                                 provider=adapter.id, model=adapter.model)
                        task.artifact_ids.append(a.id)
                    task.status, task.error, task.error_code = S.COMPLETED, None, None
                    p.log(f"{task.title}: completed with {len(task.artifact_ids)} artifact(s)", task_id=task.id)
                    ctx.save()
                return
            done_attempts += 1
            feedback = evaluation["feedback"] or "Output did not meet the acceptance criteria."
            rec.update(ok=False, error="Failed evaluation: " + feedback[:300], error_code="evaluation_failed")
            last_err = None
        with ctx.lock:
            self._set(ctx, task, S.FAILED, last_err.code if last_err else "evaluation_failed",
                      f"Failed after {max_attempts} attempt(s). " + (str(last_err) if last_err else (feedback or "")[:300]))

    # ============================================================ one attempt
    def _attempt(self, ctx: Ctx, task: TaskSpec, adapter, feedback, attempt_in_run: int, rec: dict):
        p, plan = ctx.project, ctx.project.plan
        upstream = [(plan.task(d), plan.task(d).result_text) for d in task.depends_on if plan.task(d).result_text]
        spec = KINDS[task.kind]
        max_chars = 800 if task.kind == "image" else 6000
        user = prompts.with_context(task.prompt_user, upstream, ctx.attachments, feedback, max_chars)
        rec["prompt_chars"] = len(user)
        if task.kind == "image":
            img = ctx.caller.image(adapter, user)
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/svg+xml": "svg"}.get(img.mime, "bin")
            post = Post(text=f"Image generated by {img.provider}/{img.model} ({img.mime}, {len(img.data)} bytes). {img.note}",
                        artifacts=[PendingArtifact(f"{task.id}.{ext}", "image", img.mime, img.data)], simulated=img.simulated)
            rec.update(simulated=img.simulated)
            return post, self.evaluator.evaluate(task, post.text, {"image"}, [])
        md = {"task_kind": task.kind, "task_id": task.id, "task_title": task.title, "attempt": attempt_in_run,
              "user_request": p.request[:2000], "inject_failure": ctx.inject_target == task.id}
        req = GenerationRequest(system=task.prompt_system, prompt=user, json_mode=task.kind == "presentation",
                                max_output_tokens=spec.default_out_tokens or 4096, metadata=md)
        res = ctx.caller.text(adapter, req)
        rec.update(simulated=res.simulated, input_tokens=res.input_tokens, output_tokens=res.output_tokens)
        post = self._postprocess(ctx, task, res.text, res.simulated)
        if task.kind == "code_files" and not post.problems:
            self._verify(ctx, task, adapter, post, rec)
        produced = {a.type for a in post.artifacts}
        judge_post = post
        if task.kind == "code_files":
            source_text = "\n\n".join(
                f"=== FILE: {path} ===\n{content}\n=== END FILE ==="
                for path, content in ctx.ws.files().items()
            )
            if source_text:
                judge_post = Post(
                    text=source_text,
                    artifacts=post.artifacts,
                    problems=post.problems,
                    simulated=post.simulated,
                    meta=post.meta,
                )
        evaluation = self.evaluator.evaluate(
            task, post.text, produced, post.problems,
            self._judge(ctx, task, adapter, judge_post)
        )
        for key in ("verification", "repair_changes", "changes"):
            if key in post.meta:
                evaluation[key] = post.meta[key]
        for pa in post.artifacts:
            if pa.type == "code_bundle" and "verification" in post.meta:
                pa.meta["verification"] = post.meta["verification"]
        return post, evaluation

    # -------------------------------------------------------------- postprocess
    def _postprocess(self, ctx: Ctx, task: TaskSpec, text: str, simulated: bool) -> Post:
        post = Post(text=text, simulated=simulated)
        k = task.kind
        if k == "code_files":
            files = parse_file_blocks(text)
            if not files:
                post.problems.append(("No '=== FILE: path ===' blocks were found in the output.", True))
                return post
            changes = ctx.ws.apply_files(files)                     # may raise WorkspaceError -> retry with feedback
            post.meta["changes"] = changes
            post.artifacts.append(PendingArtifact(f"{task.id}_workspace", "code_bundle", "application/zip", b"",
                                                  {"changes": changes, "files": ctx.ws.tree()}))
            post.text = "Files written:\n" + "\n".join(f"- {c['path']} ({c['action']}, {c['bytes']} bytes)" for c in changes)
        elif k == "presentation":
            try:
                outline = parse_json_loose(text)
                pptx = build_pptx(outline)
            except (ValueError, KeyError, TypeError) as exc:
                post.problems.append((f"Slide outline is not valid JSON/structure: {exc}", True))
                return post
            post.artifacts.append(PendingArtifact(f"{task.id}_outline.json", "json", "application/json", text.encode()))
            if pptx is None:
                post.problems.append(("pptx: python-pptx is not installed, so no .pptx file was produced (outline only).", False))
            else:
                post.artifacts.append(PendingArtifact(f"{task.id}.pptx", "pptx",
                                                      "application/vnd.openxmlformats-officedocument.presentationml.presentation", pptx))
        elif k == "document":
            post.artifacts.append(PendingArtifact(f"{task.id}.md", "markdown", "text/markdown", text.encode()))
            docx = build_docx(text)
            if docx is None:
                post.problems.append(("docx: python-docx is not installed, so no .docx file was produced (Markdown only).", False))
            else:
                post.artifacts.append(PendingArtifact(f"{task.id}.docx", "docx",
                                                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document", docx))
        else:
            meta = {}
            if k == "research":
                urls = list(dict.fromkeys(URL_RE.findall(text)))[:30]
                meta = {"sources": [{"url": u, "verified": False, "origin": "model-generated; not fetched or checked"} for u in urls],
                        "claims_verified": False}
            post.artifacts.append(PendingArtifact(f"{task.id}.md", "markdown", "text/markdown", text.encode(), meta))
        return post

    # ------------------------------------------------------ verify (+ repair)
    def _verify(self, ctx: Ctx, task: TaskSpec, adapter, post: Post, rec: dict) -> None:
        cmds = ctx.ws.detect_commands()
        if not cmds:
            post.meta["verification"] = {"status": "skipped", "reason": "No package.json: static files, nothing to build or test."}
            return
        if not self.runner.enabled:
            post.meta["verification"] = {"status": "skipped", "reason": "Command execution is disabled (ENABLE_COMMAND_EXECUTION=false); build/tests were NOT run."}
            return
        max_repairs = min(2, ctx.limits.max_retries)
        for rnd in range(max_repairs + 1):
            failed, results = None, []
            for argv in cmds:
                r = self.runner.run(ctx.ws, argv)
                results.append({"cmd": " ".join(argv), "ok": r.ok, "returncode": r.returncode, "timed_out": r.timed_out})
                if not r.ok:
                    failed = r
                    break
            if failed is None:
                post.meta["verification"] = {"status": "passed", "commands": results, "repairs": rnd}
                return
            if rnd == max_repairs or ctx.ctl.cancel.is_set():
                post.meta["verification"] = {"status": "failed", "commands": results, "repairs": rnd, "output_tail": failed.output[-1500:]}
                post.problems.append((f"Build/test failed after {rnd} repair attempt(s): {failed.output[-300:]}", True))
                return
            prompt = prompts.repair_prompt(task, failed.output, ctx.ws.files())
            res = ctx.caller.text(adapter, GenerationRequest(system=task.prompt_system, prompt=prompt,
                                                             max_output_tokens=KINDS["code_files"].default_out_tokens,
                                                             metadata={"task_kind": "code_repair", "task_title": task.title}))
            try:
                files = parse_file_blocks(res.text)
                if not files:
                    raise WorkspaceError("repair output contained no file blocks")
                post.meta.setdefault("repair_changes", []).extend(ctx.ws.apply_files(files))
            except WorkspaceError as exc:
                post.meta["verification"] = {"status": "failed", "commands": results, "repairs": rnd, "output_tail": str(exc)}
                post.problems.append((f"Repair attempt produced invalid output: {exc}", True))
                return
        # (unreachable)

    # ------------------------------------------------------------------ judge
    def _judge(self, ctx: Ctx, task: TaskSpec, adapter, post: Post):
        if not ctx.settings.get("llm_evaluation", True) or task.kind == "image" or not post.text.strip():
            return None

        def run_judge():
            try:
                alt, _ = self.router.route("analysis", ctx.project.mode, None, ctx.settings, exclude={adapter.id})
                j = self.registry.get(alt.provider) if alt else adapter       # prefer a *different* provider as judge
                sys_, usr = prompts.evaluator_prompt(task, post.text)
                res = ctx.caller.text(j, GenerationRequest(sys_, usr, json_mode=True, max_output_tokens=800,
                                                           metadata={"task_kind": "evaluation"}))
                data = parse_json_loose(res.text)
                if not isinstance(data, dict):
                    return {"skipped": "judge returned non-object JSON"}
                data["simulated"] = res.simulated
                return data
            except (BudgetExceeded, ProviderError, ValueError) as exc:
                return {"skipped": f"{type(exc).__name__}: {str(exc)[:120]}"}
        return run_judge

    # -------------------------------------------------------------- artifacts
    def _write_artifact(self, ctx: Ctx, task: Optional[TaskSpec], pa: PendingArtifact, source: str, simulated: bool,
                        provider: Optional[str] = None, model: Optional[str] = None) -> Artifact:
        aid = new_id("a_")
        rel = ""
        if pa.data:
            rel = f"artifacts/{aid}_{_safe_name(pa.name)}"
            target = ctx.proj_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(pa.data)
        art = Artifact(id=aid, name=pa.name, type=pa.type, task_id=task.id if task else None, path=rel, mime=pa.mime,
                       size=len(pa.data), sha256=hashlib.sha256(pa.data).hexdigest() if pa.data else "", simulated=simulated,
                       source=source, provider=provider, model=model, meta=pa.meta)
        ctx.project.artifacts.append(art)
        return art
