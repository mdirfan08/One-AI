import json
import tempfile
import threading
from pathlib import Path

from project.backend.app.config import AppConfig
from project.backend.app.core.capabilities import TEXT, JSON, CODE, IMAGE_GEN
from project.backend.app.core.workspace import CommandResult
from project.backend.app.providers.base import CredentialStore, GenerationResult, ImageResult, ProviderAdapter
from project.backend.app.providers.registry import ProviderRegistry
from project.backend.app.service import ProjectService


class Mock(ProviderAdapter):
    """Scripted provider: handler(req) -> str | Exception."""
    def __init__(self, pid, handler, caps=None):
        super().__init__(CredentialStore(env={}), model=f"{pid}-model", image_model="")
        self.id, self.display_name = pid, pid.title()
        self._handler, self._caps, self.calls = handler, frozenset(caps or {TEXT, JSON, CODE}), []
        self._lock2 = threading.Lock()

    @property
    def capabilities(self):
        return self._caps

    def is_configured(self):
        return True

    def _build_client(self, key):
        return None

    def _list_models(self):
        return ["m"]

    def _generate_text(self, req):
        with self._lock2:
            self.calls.append(req)
        out = self._handler(req)
        if isinstance(out, Exception):
            raise out
        return GenerationResult(out, self.id, self.model, input_tokens=10, output_tokens=20)

    def _generate_image(self, prompt, size):
        with self._lock2:
            self.calls.append(prompt)
        return ImageResult(b"\x89PNG\r\n", "image/png", self.id, "img-model")


def task(id, kind="writing", deps=(), **kw):
    return {"id": id, "title": id.title(), "kind": kind, "description": f"Do {id}", "depends_on": list(deps),
            "acceptance_criteria": ["is good"], **kw}


def plan_json(*tasks):
    return json.dumps({"objective": "obj", "requirements": [], "assumptions": [], "deliverables": [], "tasks": list(tasks)})


def make_service(providers, runner=None, llm_eval=False, **cfg):
    tmp = Path(tempfile.mkdtemp())
    config = AppConfig(data_dir=tmp, retry_backoff_s=0, **cfg)
    reg = ProviderRegistry(CredentialStore(env={}), extra=providers)
    svc = ProjectService(config, registry=reg)
    svc.update_settings({"llm_evaluation": llm_eval})
    if runner:
        svc.executor.runner = runner
    return svc


def new_project(svc, mode="integration", execution="auto", **kw):
    p = svc.create_project({"request": "build things", "mode": mode, "execution": execution, **kw})
    return p.id


class FakeRunner:
    """Pretends to run builds: fails while any workspace file contains 'BUG'."""
    enabled = True

    def __init__(self):
        self.runs = []

    def run(self, ws, argv):
        self.runs.append(argv)
        files = ws.files()
        if argv[:2] == ["npm", "run"] and any("BUG" in c for c in files.values()):
            return CommandResult(False, 1, "error TS2304: Cannot find name 'BUG'")
        return CommandResult(True, 0, "ok")
