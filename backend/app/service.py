"""Application service: all business operations, independent of the web framework (easy to test)."""
from __future__ import annotations

import base64
import binascii
import logging
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

from .config import AppConfig, Limits
from .core import prompts
from .core.budget import Budget
from .core.caller import ProviderCaller
from .core.capabilities import KINDS
from .core.executor import Executor, RunControl
from .core.planner import Planner, PlannerError
from .core.pricing import PricingTable
from .core.router import Router
from .core.schemas import Artifact, Project, TaskStatus as S, new_id
from .core.workspace import Workspace, WorkspaceError
from .logging_utils import redact
from .providers.base import CredentialStore, ProviderError
from .providers.registry import ProviderRegistry
from .storage import ProjectStore, SqliteProjectStore

log = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "models": {},                      # {provider: {"model": str, "image_model": str}}
    "provider_order": [],              # preferred order of provider ids
    "provider_preferences": {},        # {task_kind: provider_id}
    "planner_provider": None,
    "llm_evaluation": True,            # extra provider call per task to judge acceptance criteria
    "default_limits": Limits().to_dict(),
    "privacy": {"send_attachments_to_providers": True, "include_prompts_in_export": True},
    "default_mode": "demo",
}
UPLOAD_EXT = {".txt", ".md", ".csv", ".json", ".html", ".css", ".js", ".ts", ".tsx", ".py", ".yml", ".yaml"}
EDITABLE_TASK_FIELDS = {"title", "description", "prompt_system", "prompt_user", "acceptance_criteria", "preferred_provider", "requires_approval"}


class NotFound(Exception): ...
class Conflict(Exception): ...
class Invalid(ValueError): ...


class ProjectService:
    def __init__(self, config: AppConfig, store: Optional[ProjectStore] = None, registry: Optional[ProviderRegistry] = None,
                 pricing: Optional[PricingTable] = None, executor: Optional[Executor] = None):
        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = store or SqliteProjectStore(config.data_dir / "orchestra.db")
        self.registry = registry or ProviderRegistry(CredentialStore())
        self.pricing = pricing or PricingTable.load(config.pricing_file)
        self.router = Router(self.registry, self.pricing)
        self.planner = Planner(self.registry, self.router, self.pricing)
        self.executor = executor or Executor(self.registry, self.router, self.pricing, config)
        self._cache: dict = {}
        self._locks: dict = {}
        self._runs: dict = {}          # pid -> (RunControl, Thread)
        self._glock = threading.Lock()
        self.registry.apply_settings(self.get_settings().get("models", {}))

    # ------------------------------------------------------------------ helpers
    def _lock(self, pid: str) -> threading.RLock:
        with self._glock:
            return self._locks.setdefault(pid, threading.RLock())

    def _project(self, pid: str) -> Project:
        with self._glock:
            p = self._cache.get(pid)
        if p is None:
            p = self.store.load(pid)
            if p is None:
                raise NotFound(f"Project '{pid}' not found")
            with self._glock:
                p = self._cache.setdefault(pid, p)
        return p

    def _dir(self, pid: str) -> Path:
        if not re.fullmatch(r"p_[0-9a-f]{10}", pid):
            raise NotFound("Project not found")
        return self.config.data_dir / "projects" / pid

    def _save(self, p: Project) -> None:
        p.updated_at = time.time()
        self.store.save(p)

    def is_running(self, pid: str) -> bool:
        r = self._runs.get(pid)
        return bool(r and r[1].is_alive())

    # ----------------------------------------------------------------- settings
    def get_settings(self) -> dict:
        saved = self.store.get_settings()
        s = {**DEFAULT_SETTINGS, **saved}
        s["privacy"] = {**DEFAULT_SETTINGS["privacy"], **saved.get("privacy", {})}
        s["default_limits"] = Limits.from_dict({**DEFAULT_SETTINGS["default_limits"], **saved.get("default_limits", {})}).to_dict()
        return s

    def update_settings(self, patch: dict) -> dict:
        s = self.get_settings()
        for key, val in (patch or {}).items():
            if key not in DEFAULT_SETTINGS:
                raise Invalid(f"Unknown setting '{key}'")
            if key == "models":
                if not isinstance(val, dict) or any(k not in self.registry.ids() for k in val):
                    raise Invalid("models must map known provider ids to {model, image_model}")
                val = {k: {"model": str(v.get("model", ""))[:100], "image_model": str(v.get("image_model", ""))[:100]}
                       for k, v in val.items() if isinstance(v, dict)}
            elif key == "provider_order":
                if not isinstance(val, list) or any(x not in self.registry.ids() for x in val):
                    raise Invalid("provider_order must be a list of known provider ids")
            elif key == "provider_preferences":
                if not isinstance(val, dict) or any(k not in KINDS or v not in self.registry.ids() for k, v in val.items()):
                    raise Invalid("provider_preferences must map task kinds to provider ids")
            elif key == "planner_provider":
                if val is not None and val not in self.registry.ids():
                    raise Invalid("unknown planner_provider")
            elif key == "llm_evaluation":
                val = bool(val)
            elif key == "default_limits":
                val = Limits.from_dict(val).to_dict()
            elif key == "privacy":
                val = {**s["privacy"], **{k: bool(v) for k, v in val.items() if k in s["privacy"]}}
            elif key == "default_mode":
                if val not in ("demo", "integration"):
                    raise Invalid("default_mode must be demo or integration")
            s[key] = val
        self.store.save_settings(s)
        self.registry.apply_settings(s["models"])
        return s

    # ---------------------------------------------------------------- providers
    def provider_statuses(self) -> list:
        out = []
        for a in self.registry.all():
            d = a.status().to_dict()
            d["notes"] = list(d.get("notes") or [])
            if a.id != "demo":
                d["notes"].append("Using the API may incur charges under your own provider account. A chat subscription "
                                  "(ChatGPT/Claude/Gemini app) does NOT include API access.")
                d["pricing_known"] = self.pricing.known(a.id, a.model)
            out.append(d)
        return out

    def set_credentials(self, pid: str, key: str) -> dict:
        a = self.registry.get(pid)
        if not a or pid == "demo":
            raise NotFound("Unknown provider")
        try:
            self.registry.credentials.set_session_key(pid, key)
        except ValueError as exc:
            raise Invalid(str(exc)) from None
        a.connection = "unknown"
        return a.status().to_dict()

    def clear_credentials(self, pid: str) -> dict:
        a = self.registry.get(pid)
        if not a or pid == "demo":
            raise NotFound("Unknown provider")
        self.registry.credentials.clear_session_key(pid)
        a.connection = "unknown"
        return a.status().to_dict()

    def check_provider(self, pid: str) -> dict:
        a = self.registry.get(pid)
        if not a:
            raise NotFound("Unknown provider")
        return a.check_connection().to_dict()

    # ----------------------------------------------------------------- projects
    def list_projects(self) -> list:
        return self.store.list_projects()

    def create_project(self, data: dict) -> Project:
        name = str(data.get("name") or "").strip()[:120]
        request = str(data.get("request") or "").strip()
        if not request:
            raise Invalid("Please describe the task.")
        if len(request) > 20000:
            raise Invalid("Request too long (max 20,000 characters).")
        mode = data.get("mode", "demo")
        execution = data.get("execution", "manual")
        if mode not in ("demo", "integration") or execution not in ("auto", "manual"):
            raise Invalid("Invalid mode or execution.")
        settings = self.get_settings()
        limits = Limits.from_dict({**settings["default_limits"], **(data.get("limits") or {})})
        fmts = [str(x)[:40] for x in (data.get("output_formats") or [])][:10]
        pid = new_id("p_")
        p = Project(id=pid, name=name or request[:50], request=request, mode=mode, execution=execution,
                    constraints=str(data.get("constraints") or "")[:4000], output_formats=fmts, limits=limits.to_dict(),
                    planner_provider=data.get("planner_provider") or None,
                    demo_options={"inject_failure": bool((data.get("demo_options") or {}).get("inject_failure"))})
        if p.planner_provider and p.planner_provider not in self.registry.ids():
            raise Invalid("Unknown planner provider.")
        self._dir(pid).mkdir(parents=True, exist_ok=True)
        p.log("Project created")
        with self._glock:
            self._cache[pid] = p
        self._save(p)
        return p

    def get_project(self, pid: str) -> dict:
        p = self._project(pid)
        with self._lock(pid):
            d = p.to_dict()
        d["running"] = self.is_running(pid)
        d["workspace_files"] = Workspace(self._dir(pid) / "workspace").tree() if (self._dir(pid) / "workspace").exists() else []
        d["command_execution_enabled"] = self.executor.runner.enabled
        return d

    def delete_project(self, pid: str) -> None:
        self._project(pid)
        if self.is_running(pid):
            raise Conflict("Cancel the running workflow first.")
        shutil.rmtree(self._dir(pid), ignore_errors=True)
        self.store.delete(pid)
        with self._glock:
            self._cache.pop(pid, None)

    # ---------------------------------------------------------------- planning
    def generate_plan(self, pid: str) -> dict:
        p = self._project(pid)
        if self.is_running(pid):
            raise Conflict("A workflow is running.")
        if p.plan and any(t.attempts or t.status in (S.COMPLETED, S.RUNNING) for t in p.plan.tasks):
            raise Conflict("This project has already started executing; create a new project to re-plan.")
        settings = self.get_settings()
        limits = Limits.from_dict(p.limits)
        caller = ProviderCaller(Budget(limits, p.usage, self.pricing, self._lock(pid)))
        with self._lock(pid):
            p.status = "planning"
            p.log("Planning started")
        try:
            plan = self.planner.create_plan(p, settings, caller)
        except PlannerError as exc:
            with self._lock(pid):
                p.status = "draft"
                p.log(f"Planning failed: {redact(str(exc))}", "error")
                self._save(p)
            raise Invalid(redact(str(exc))) from None
        with self._lock(pid):
            plan.approved = p.execution == "auto"
            p.plan, p.status, p.final = plan, "planned", None
            p.log(f"Plan created with {len(plan.tasks)} tasks" + (" (simulated planner)" if plan.simulated else ""))
            self._save(p)
        return self.get_project(pid)

    def approve_plan(self, pid: str) -> dict:
        p = self._project(pid)
        if not p.plan:
            raise Conflict("No plan to approve.")
        with self._lock(pid):
            p.plan.approved = True
            p.log("Plan approved")
            self._save(p)
        return self.get_project(pid)

    def update_task(self, pid: str, tid: str, patch: dict) -> dict:
        p = self._project(pid)
        if self.is_running(pid):
            raise Conflict("Cannot edit while the workflow is running.")
        t = p.plan.task(tid) if p.plan else None
        if not t:
            raise NotFound("Task not found")
        if t.status in (S.RUNNING, S.COMPLETED):
            raise Conflict("Completed tasks cannot be edited.")
        with self._lock(pid):
            for k, v in patch.items():
                if k not in EDITABLE_TASK_FIELDS:
                    raise Invalid(f"Field '{k}' is not editable")
                if k in ("prompt_system", "prompt_user"):
                    if not isinstance(v, str) or len(v) > 60000 or not v.strip():
                        raise Invalid(f"{k} must be a non-empty string (max 60,000 chars)")
                    t.prompt_edited = True
                elif k == "preferred_provider":
                    if v is not None and v not in self.registry.ids():
                        raise Invalid("Unknown provider")
                elif k == "acceptance_criteria":
                    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() for x in v):
                        raise Invalid("acceptance_criteria must be a non-empty list of strings")
                elif k == "requires_approval":
                    v = bool(v)
                elif not isinstance(v, str) or not v.strip():
                    raise Invalid(f"{k} must be a non-empty string")
                setattr(t, k, v)
            p.log(f"Task '{t.title}' edited", task_id=tid)
            self._save(p)
        return self.get_project(pid)

    # --------------------------------------------------------------- execution
    def _attachments(self, p: Project, settings: dict) -> list:
        if not settings["privacy"]["send_attachments_to_providers"]:
            return []
        out = []
        for a in p.attachments:
            f = self._dir(p.id) / a["path"]
            if f.is_file():
                out.append((a["name"], f.read_text(errors="replace")))
        return out

    def _run_body(self, pid: str, ctl: RunControl) -> None:
        p = self._project(pid)
        settings = self.get_settings()
        try:
            self.executor.run(p, ctl, lambda: self._save(p), self._lock(pid), settings, self._dir(pid), self._attachments(p, settings))
        except Exception:   # never leave a project stuck in 'running'
            log.exception("run crashed")
            with self._lock(pid):
                p.status = "failed"
                p.log("Run crashed (see server log)", "error")
                self._save(p)

    def start_run(self, pid: str, background: bool = True) -> dict:
        p = self._project(pid)
        if not p.plan:
            raise Conflict("Generate a plan first.")
        if self.is_running(pid):
            raise Conflict("Already running.")
        if not p.plan.approved:
            if p.execution == "auto":
                p.plan.approved = True
            else:
                raise Conflict("Review and approve the plan before running.")
        ctl = RunControl()
        t = threading.Thread(target=self._run_body, args=(pid, ctl), daemon=True, name=f"run-{pid}")
        self._runs[pid] = (ctl, t)
        with self._lock(pid):
            p.status = "running"
        t.start()
        if not background:
            t.join()
        return self.get_project(pid)

    def run_sync(self, pid: str) -> dict:
        return self.start_run(pid, background=False)

    def cancel(self, pid: str) -> dict:
        r = self._runs.get(pid)
        if r and r[1].is_alive():
            r[0].cancel.set()
        return self.get_project(pid)

    def _task(self, pid: str, tid: str):
        p = self._project(pid)
        t = p.plan.task(tid) if p.plan else None
        if not t:
            raise NotFound("Task not found")
        return p, t

    def approve_task(self, pid: str, tid: str) -> dict:
        p, t = self._task(pid, tid)
        if t.status != S.NEEDS_APPROVAL:
            raise Conflict("Task is not waiting for approval.")
        with self._lock(pid):
            t.approved, t.status = True, S.PLANNED
            p.log(f"Task '{t.title}' approved", task_id=tid)
            self._save(p)
        return self.get_project(pid)

    def skip_task(self, pid: str, tid: str, reason: str = "Skipped by user") -> dict:
        p, t = self._task(pid, tid)
        if self.is_running(pid) or t.status in (S.RUNNING, S.COMPLETED):
            raise Conflict("Task cannot be skipped now.")
        with self._lock(pid):
            t.status, t.error_code, t.error, t.handoff = S.BLOCKED, "skipped", reason[:200], False
            p.log(f"Task '{t.title}' skipped", task_id=tid)
            self._save(p)
        return self.get_project(pid)

    def retry_task(self, pid: str, tid: str) -> dict:
        p, t = self._task(pid, tid)
        limits = Limits.from_dict(p.limits)
        if self.is_running(pid):
            raise Conflict("Wait for the current run to finish or cancel it.")
        if t.status not in (S.FAILED, S.BLOCKED):
            raise Conflict("Only failed or blocked tasks can be retried.")
        if t.manual_retries >= limits.max_retries:
            raise Conflict(f"Manual retry limit reached ({limits.max_retries}). Edit the prompt or settings, or raise the limit.")
        with self._lock(pid):
            t.manual_retries += 1
            t.status, t.error, t.error_code, t.handoff = S.PLANNED, None, None, False
            p.log(f"Task '{t.title}' queued for retry ({t.manual_retries}/{limits.max_retries})", task_id=tid)
            self._save(p)
        return self.start_run(pid)

    def handoff_prompt(self, pid: str, tid: str) -> dict:
        p, t = self._task(pid, tid)
        settings = self.get_settings()
        upstream = [(p.plan.task(d), p.plan.task(d).result_text) for d in t.depends_on if p.plan.task(d).result_text]
        user = prompts.with_context(t.prompt_user, upstream, self._attachments(p, settings), None,
                                    800 if t.kind == "image" else 6000)
        return {"task_id": tid, "text": prompts.manual_handoff(t.prompt_system, user),
                "providers": {"openai": "https://chatgpt.com", "anthropic": "https://claude.ai", "gemini": "https://gemini.google.com"},
                "note": "Run this in a provider's own product, then paste the result back. Nothing is sent automatically."}

    def submit_manual_result(self, pid: str, tid: str, text: str, filename: str = "") -> dict:
        p, t = self._task(pid, tid)
        if self.is_running(pid) or t.status in (S.RUNNING,):
            raise Conflict("Wait for the current run to finish.")
        text = (text or "").strip()
        if not text or len(text) > 200_000:
            raise Invalid("Provide result text (max 200,000 characters).")
        name = re.sub(r"[^A-Za-z0-9._-]", "_", filename or f"{t.id}_manual.md")[:80]
        data = text.encode()
        aid = new_id("a_")
        rel = f"artifacts/{aid}_{name}"
        (self._dir(pid) / rel).parent.mkdir(parents=True, exist_ok=True)
        (self._dir(pid) / rel).write_bytes(data)
        import hashlib
        art = Artifact(id=aid, name=name, type="manual_note" if t.kind in ("video_assets", "voiceover") else "markdown", task_id=tid,
                       path=rel, mime="text/markdown", size=len(data), sha256=hashlib.sha256(data).hexdigest(), source="manual",
                       provider=None, meta={"note": "Pasted by the user; not produced or verified by this platform."})
        with self._lock(pid):
            p.artifacts.append(art)
            t.artifact_ids, t.result_text, t.simulated = [aid], text, False
            t.status, t.error, t.error_code, t.handoff = S.COMPLETED, None, None, False
            t.provider, t.evaluation = "manual", {"passed": None, "method": "manual", "checks": [], "feedback": ""}
            t.attempts.append({"n": len(t.attempts) + 1, "provider": "manual", "model": None, "ok": True, "simulated": False,
                               "started": time.time(), "finished": time.time(), "error": None, "error_code": None})
            p.log(f"Manual result recorded for '{t.title}'", task_id=tid)
            self._save(p)
        return self.get_project(pid)

    # ------------------------------------------------------ files / export
    def add_attachment(self, pid: str, filename: str, content_b64: str) -> dict:
        p = self._project(pid)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename or "").name)[:80]
        if not name or Path(name).suffix.lower() not in UPLOAD_EXT:
            raise Invalid(f"Unsupported file type. Allowed: {', '.join(sorted(UPLOAD_EXT))}")
        if len(p.attachments) >= 10:
            raise Invalid("Maximum 10 attachments per project.")
        try:
            raw = base64.b64decode(content_b64, validate=True)
        except (binascii.Error, ValueError):
            raise Invalid("Invalid base64 content.") from None
        if len(raw) > self.config.max_upload_bytes:
            raise Invalid(f"File too large (max {self.config.max_upload_bytes} bytes).")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            raise Invalid("Only UTF-8 text files are supported.") from None
        rel = f"uploads/{name}"
        (self._dir(pid) / "uploads").mkdir(parents=True, exist_ok=True)
        (self._dir(pid) / rel).write_bytes(raw)
        with self._lock(pid):
            p.attachments = [a for a in p.attachments if a["name"] != name] + [{"name": name, "size": len(raw), "path": rel}]
            self._save(p)
        return self.get_project(pid)

    def artifact_content(self, pid: str, aid: str) -> tuple:
        """Return (bytes, mime, filename)."""
        p = self._project(pid)
        a = p.artifact(aid)
        if not a:
            raise NotFound("Artifact not found")
        if a.type == "code_bundle":
            return Workspace(self._dir(pid) / "workspace").package_zip(), "application/zip", f"{pid}_project.zip"
        f = (self._dir(pid) / a.path).resolve()
        if not str(f).startswith(str(self._dir(pid).resolve())) or not f.is_file():
            raise NotFound("Artifact file missing")
        return f.read_bytes(), a.mime, a.name

    def package_zip(self, pid: str) -> bytes:
        self._project(pid)
        return Workspace(self._dir(pid) / "workspace").package_zip()

    def preview_file(self, pid: str, rel: str) -> tuple:
        self._project(pid)
        ws = Workspace(self._dir(pid) / "workspace")
        try:
            f = ws.safe_path(rel or "index.html")
        except WorkspaceError:
            raise NotFound("File not found") from None
        if not f.is_file():
            raise NotFound("File not found")
        mime = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".json": "application/json",
                ".svg": "image/svg+xml", ".txt": "text/plain", ".md": "text/plain"}.get(f.suffix.lower(), "text/plain")
        return f.read_bytes(), mime

    def export_json(self, pid: str) -> dict:
        p = self._project(pid)
        settings = self.get_settings()
        with self._lock(pid):
            d = p.to_dict()
        contents = {}
        for a in p.artifacts:
            if a.path and a.mime.startswith(("text/", "application/json")) and a.size <= 200_000:
                f = self._dir(pid) / a.path
                if f.is_file():
                    contents[a.id] = f.read_text(errors="replace")
        d["artifact_contents"] = contents
        d["workspace_files"] = Workspace(self._dir(pid) / "workspace").files() if (self._dir(pid) / "workspace").exists() else {}
        if not settings["privacy"]["include_prompts_in_export"] and d.get("plan"):
            for t in d["plan"]["tasks"]:
                t["prompt_system"] = t["prompt_user"] = "[omitted by privacy setting]"
        d["export_notice"] = ("Contains your requests and AI-generated content. API keys are never stored or exported. "
                              "Items marked simulated came from the demo provider, not a real AI model.")
        return d
