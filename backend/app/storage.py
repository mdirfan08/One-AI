"""Persistence abstraction + SQLite implementation (stdlib only). Swap `ProjectStore` to change backends.
No credentials are ever passed to or stored by this layer."""
from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .core.schemas import Project


class ProjectStore(ABC):
    @abstractmethod
    def list_projects(self) -> list: ...
    @abstractmethod
    def load(self, pid: str) -> Optional[Project]: ...
    @abstractmethod
    def save(self, project: Project) -> None: ...
    @abstractmethod
    def delete(self, pid: str) -> None: ...
    @abstractmethod
    def get_settings(self) -> dict: ...
    @abstractmethod
    def save_settings(self, settings: dict) -> None: ...


class SqliteProjectStore(ProjectStore):
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT, status TEXT, mode TEXT, "
                      "created_at REAL, updated_at REAL, data TEXT NOT NULL)")
            c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def list_projects(self) -> list:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT id,name,status,mode,created_at,updated_at FROM projects ORDER BY updated_at DESC").fetchall()
        return [dict(id=r[0], name=r[1], status=r[2], mode=r[3], created_at=r[4], updated_at=r[5]) for r in rows]

    def load(self, pid: str) -> Optional[Project]:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT data FROM projects WHERE id=?", (pid,)).fetchone()
        return Project.from_dict(json.loads(row[0])) if row else None

    def save(self, project: Project) -> None:
        data = json.dumps(project.to_dict())
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO projects (id,name,status,mode,created_at,updated_at,data) VALUES (?,?,?,?,?,?,?)",
                      (project.id, project.name, project.status, project.mode, project.created_at, project.updated_at, data))

    def delete(self, pid: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM projects WHERE id=?", (pid,))

    def get_settings(self) -> dict:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key='app'").fetchone()
        return json.loads(row[0]) if row else {}

    def save_settings(self, settings: dict) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('app',?)", (json.dumps(settings),))
