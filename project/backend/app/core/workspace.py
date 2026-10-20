"""Controlled workspace for generated code: path-safe writes, change tracking, packaging and an
allow-listed command runner.

IMPORTANT: the command runner is NOT a sandbox. It blocks arbitrary commands (allow-list, no shell),
scrubs the environment (no secrets), enforces timeouts and output caps, and disables npm lifecycle
scripts - but allowed commands (e.g. `npm run build`) still execute generated project code with the
server user's privileges. It is disabled by default. Run the backend inside a container/VM if you enable it."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MAX_FILES = 200
MAX_FILE_BYTES = 400_000
MAX_TOTAL_BYTES = 5_000_000
ALLOWED_EXT = {".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt", ".svg", ".py", ".yml", ".yaml",
               ".toml", ".cfg", ".ini", ".env.example", ".gitignore", ".mjs", ".cjs", ".vue", ".scss", ".sh"}
FORBIDDEN_NAMES = {".env", ".git", "node_modules", ".ssh", "id_rsa"}
FILE_BLOCK = re.compile(r"^=== FILE: (?P<path>[^\n]+?) ===\n(?P<body>.*?)\n?=== END FILE ===", re.S | re.M)


class WorkspaceError(ValueError):
    pass


def parse_file_blocks(text: str) -> list:
    return [(m.group("path").strip(), m.group("body")) for m in FILE_BLOCK.finditer(text)]


@dataclass
class CommandResult:
    ok: bool
    returncode: Optional[int]
    output: str
    timed_out: bool = False
    skipped_reason: Optional[str] = None


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- path safety
    def safe_path(self, rel: str) -> Path:
        if not isinstance(rel, str) or not rel.strip() or "\x00" in rel or len(rel) > 200:
            raise WorkspaceError("invalid path")
        rel = rel.strip().replace("\\", "/")
        if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
            raise WorkspaceError(f"absolute paths are not allowed: {rel}")
        parts = Path(rel).parts
        if any(p in ("..", ".") for p in parts) or any(p.lower() in FORBIDDEN_NAMES for p in parts):
            raise WorkspaceError(f"forbidden path: {rel}")
        name = parts[-1].lower()
        if not (Path(name).suffix in ALLOWED_EXT or name in ALLOWED_EXT or name.endswith(".env.example")):
            raise WorkspaceError(f"file type not allowed: {rel}")
        target = (self.root / rel).resolve()
        if self.root != target and self.root not in target.parents:
            raise WorkspaceError(f"path escapes workspace: {rel}")
        # refuse to write through symlinks
        cur = self.root
        for p in parts:
            cur = cur / p
            if cur.is_symlink():
                raise WorkspaceError(f"symlinks are not allowed: {rel}")
        return target

    # ------------------------------------------------------------------- writing
    def files(self) -> dict:
        out = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and not p.is_symlink() and "node_modules" not in p.parts:
                try:
                    out[p.relative_to(self.root).as_posix()] = p.read_text(errors="replace")
                except OSError:
                    continue
        return out

    def apply_files(self, files: list) -> list:
        """Write [(path, content)]; return change records. Validates everything before writing anything."""
        if not files:
            raise WorkspaceError("no files supplied")
        if len(files) > 40:
            raise WorkspaceError("too many files in one step (max 40)")
        existing = self.files()
        targets, total = [], 0
        for rel, content in files:
            t = self.safe_path(rel)
            b = content.encode()
            if len(b) > MAX_FILE_BYTES:
                raise WorkspaceError(f"file too large: {rel}")
            total += len(b)
            targets.append((rel.strip().replace("\\", "/"), t, content))
        if total > MAX_TOTAL_BYTES or len(set(existing) | {r for r, _, _ in targets}) > MAX_FILES:
            raise WorkspaceError("workspace size limits exceeded")
        changes = []
        for rel, t, content in targets:
            old = existing.get(rel)
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(content)
            action = "created" if old is None else ("unchanged" if old == content else "modified")
            changes.append({"path": rel, "action": action, "bytes": len(content.encode()),
                            "sha256": hashlib.sha256(content.encode()).hexdigest()[:16],
                            "lines_before": 0 if old is None else old.count("\n") + 1, "lines_after": content.count("\n") + 1})
        return changes

    def tree(self) -> list:
        return [{"path": k, "bytes": len(v.encode())} for k, v in self.files().items()]

    def package_zip(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for rel, content in self.files().items():
                z.writestr(rel, content)
        return buf.getvalue()

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------- build/test verification
    def detect_commands(self) -> list:
        """Verification commands derived from the project itself (JS/TS only for now)."""
        pkg = self.root / "package.json"
        if not pkg.is_file():
            return []
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {})
        except (ValueError, OSError):
            return []
        cmds = [["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]]
        if "build" in scripts:
            cmds.append(["npm", "run", "build"])
        if "test" in scripts:
            cmds.append(["npm", "test", "--", "--run"] if "vitest" in scripts.get("test", "") else ["npm", "test"])
        return cmds


ALLOWED_COMMANDS = {
    ("npm", "install"), ("npm", "run", "build"), ("npm", "test"), ("npm", "run", "test"), ("npm", "run", "lint"),
    ("python3", "-m", "pytest"), ("python3", "-m", "compileall"),
}


class CommandRunner:
    def __init__(self, enabled: bool = False, timeout_s: int = 180, max_output: int = 20_000):
        self.enabled = enabled
        self.timeout_s = timeout_s
        self.max_output = max_output

    def allowed(self, argv: list) -> bool:
        if not argv or not all(isinstance(x, str) for x in argv):
            return False
        for n in (3, 2):
            if tuple(argv[:n]) in ALLOWED_COMMANDS:
                break
        else:
            return False
        if argv[:2] == ["npm", "install"]:   # flags only: never install a named package
            return all(a.startswith("-") for a in argv[2:])
        return True

    def run(self, ws: Workspace, argv: list) -> CommandResult:
        if not self.enabled:
            return CommandResult(False, None, "", skipped_reason="Command execution is disabled (set ENABLE_COMMAND_EXECUTION=true).")
        if not self.allowed(argv):
            return CommandResult(False, None, "", skipped_reason=f"Command not on the allow-list: {' '.join(argv)[:80]}")
        if argv[:2] == ["npm", "install"] and "--ignore-scripts" not in argv:
            argv = argv + ["--ignore-scripts"]
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(ws.root), "CI": "true", "NO_COLOR": "1"}   # no inherited secrets
        try:
            p = subprocess.run(argv, cwd=ws.root, env=env, capture_output=True, text=True, timeout=self.timeout_s, shell=False)
            out = (p.stdout + p.stderr)[-self.max_output:]
            return CommandResult(p.returncode == 0, p.returncode, out)
        except subprocess.TimeoutExpired:
            return CommandResult(False, None, "Command timed out.", timed_out=True)
        except FileNotFoundError:
            return CommandResult(False, None, f"Executable not found: {argv[0]}", skipped_reason="missing executable")
