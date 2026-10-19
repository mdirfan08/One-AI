"""HTTP layer (FastAPI + Pydantic). Thin: all logic lives in app/service.py.

Security notes: binds to localhost by default; optional APP_ACCESS_TOKEN; request-size limit; strict CORS list;
uploads are base64 JSON restricted to small UTF-8 text files; generated-site preview is served with a sandboxing CSP."""
from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .config import AppConfig
from .logging_utils import setup_logging
from .service import Conflict, Invalid, NotFound, ProjectService

log = logging.getLogger(__name__)


class ProjectCreate(BaseModel):
    name: str = Field("", max_length=120)
    request: str = Field(..., min_length=1, max_length=20000)
    mode: Literal["demo", "integration"] = "demo"
    execution: Literal["auto", "manual"] = "manual"
    constraints: str = Field("", max_length=4000)
    output_formats: list[str] = Field(default_factory=list, max_length=10)
    limits: Optional[dict[str, Any]] = None
    planner_provider: Optional[str] = None
    demo_options: Optional[dict[str, Any]] = None


class TaskPatch(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=4000)
    prompt_system: Optional[str] = Field(None, max_length=60000)
    prompt_user: Optional[str] = Field(None, max_length=60000)
    acceptance_criteria: Optional[list[str]] = Field(None, max_length=10)
    preferred_provider: Optional[str] = None
    requires_approval: Optional[bool] = None


class CredentialBody(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=512, repr=False)


class ManualResult(BaseModel):
    text: str = Field(..., min_length=1, max_length=200000)
    filename: str = Field("", max_length=80)


class UploadBody(BaseModel):
    filename: str = Field(..., max_length=120)
    content_base64: str = Field(..., max_length=900000)


def create_app(service: Optional[ProjectService] = None, config: Optional[AppConfig] = None) -> FastAPI:
    config = config or (service.config if service else AppConfig.from_env())
    setup_logging(config.log_level)
    service = service or ProjectService(config)
    app = FastAPI(title="One AI — Universal AI Orchestration Platform", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=list(config.cors_origins), allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", "X-Access-Token"])

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > config.max_request_bytes:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        return await call_next(request)

    for exc, code in ((NotFound, 404), (Conflict, 409), (Invalid, 422)):
        def make(c):
            async def handler(_: Request, e: Exception):
                return JSONResponse({"detail": str(e)}, status_code=c)
            return handler
        app.add_exception_handler(exc, make(code))

    def auth(x_access_token: Optional[str] = Header(None), token: Optional[str] = Query(None)):
        if config.access_token:
            supplied = x_access_token or token or ""
            if not hmac.compare_digest(supplied, config.access_token):
                raise HTTPException(401, "Missing or invalid access token")

    api = APIRouter(prefix="/api", dependencies=[Depends(auth)])

    @api.get("/health")
    def health():
        return {"ok": True, "command_execution_enabled": service.executor.runner.enabled,
                "auth_required": bool(config.access_token)}

    # ---- providers / settings
    @api.get("/providers")
    def providers():
        return service.provider_statuses()

    @api.put("/providers/{pid}/credentials")
    def set_credentials(pid: str, body: CredentialBody):
        return service.set_credentials(pid, body.api_key)

    @api.delete("/providers/{pid}/credentials")
    def clear_credentials(pid: str):
        return service.clear_credentials(pid)

    @api.post("/providers/{pid}/check")
    def check(pid: str):
        return service.check_provider(pid)

    @api.get("/settings")
    def get_settings():
        return service.get_settings()

    @api.put("/settings")
    def put_settings(patch: dict[str, Any]):
        return service.update_settings(patch)

    # ---- projects
    @api.get("/projects")
    def list_projects():
        return service.list_projects()

    @api.post("/projects")
    def create_project(body: ProjectCreate):
        p = service.create_project(body.model_dump())
        return service.get_project(p.id)

    @api.get("/projects/{pid}")
    def get_project(pid: str):
        return service.get_project(pid)

    @api.delete("/projects/{pid}")
    def delete_project(pid: str):
        service.delete_project(pid)
        return {"ok": True}

    @api.post("/projects/{pid}/uploads")
    def upload(pid: str, body: UploadBody):
        return service.add_attachment(pid, body.filename, body.content_base64)

    @api.post("/projects/{pid}/plan")
    def plan(pid: str):
        return service.generate_plan(pid)

    @api.post("/projects/{pid}/plan/approve")
    def approve_plan(pid: str):
        return service.approve_plan(pid)

    @api.put("/projects/{pid}/tasks/{tid}")
    def edit_task(pid: str, tid: str, body: TaskPatch):
        return service.update_task(pid, tid, body.model_dump(exclude_unset=True))

    @api.post("/projects/{pid}/run")
    def run(pid: str):
        return service.start_run(pid)

    @api.post("/projects/{pid}/cancel")
    def cancel(pid: str):
        return service.cancel(pid)

    @api.post("/projects/{pid}/tasks/{tid}/approve")
    def approve_task(pid: str, tid: str, run: bool = True):
        service.approve_task(pid, tid)
        return service.start_run(pid) if run else service.get_project(pid)

    @api.post("/projects/{pid}/tasks/{tid}/skip")
    def skip_task(pid: str, tid: str):
        return service.skip_task(pid, tid)

    @api.post("/projects/{pid}/tasks/{tid}/retry")
    def retry_task(pid: str, tid: str):
        return service.retry_task(pid, tid)

    @api.get("/projects/{pid}/tasks/{tid}/handoff")
    def handoff(pid: str, tid: str):
        return service.handoff_prompt(pid, tid)

    @api.post("/projects/{pid}/tasks/{tid}/manual-result")
    def manual_result(pid: str, tid: str, body: ManualResult, resume: bool = True):
        service.submit_manual_result(pid, tid, body.text, body.filename)
        return service.start_run(pid) if resume else service.get_project(pid)

    @api.get("/projects/{pid}/artifacts/{aid}")
    def artifact(pid: str, aid: str):
        data, mime, name = service.artifact_content(pid, aid)
        return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                        "X-Content-Type-Options": "nosniff"})

    @api.get("/projects/{pid}/package")
    def package(pid: str):
        return Response(service.package_zip(pid), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{pid}_project.zip"'})

    @api.get("/projects/{pid}/export")
    def export(pid: str):
        return JSONResponse(service.export_json(pid), headers={"Content-Disposition": f'attachment; filename="{pid}_export.json"'})

    @api.get("/projects/{pid}/preview/{path:path}")
    def preview(pid: str, path: str):
        data, mime = service.preview_file(pid, path or "index.html")
        csp = "sandbox allow-scripts; default-src 'self' 'unsafe-inline' data:; connect-src 'none'; form-action 'none'"
        return Response(data, media_type=mime, headers={"Content-Security-Policy": csp, "X-Content-Type-Options": "nosniff"})

    app.include_router(api)

    dist = Path(os.environ.get("FRONTEND_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
    if dist.is_dir():   # optional single-process deployment: serve the built SPA
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dist, html=True), name="spa")
    return app
