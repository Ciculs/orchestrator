from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

STAGES = ["design", "critique", "judgment", "implementation", "verification", "audit", "report"]


class TaskStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
        CREATE TABLE IF NOT EXISTS projects(
          id TEXT PRIMARY KEY, name TEXT NOT NULL, repo TEXT NOT NULL, goal TEXT NOT NULL,
          status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS stages(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, stage TEXT NOT NULL,
          model TEXT NOT NULL, prompt TEXT NOT NULL, artifact TEXT NOT NULL, created_at INTEGER NOT NULL,
          UNIQUE(project_id, stage));
        CREATE TABLE IF NOT EXISTS evidence(
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, kind TEXT NOT NULL,
          data TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(
          id TEXT NOT NULL, project_id TEXT NOT NULL, goal TEXT NOT NULL,
          allowed_files TEXT NOT NULL, verify_command TEXT NOT NULL, depends_on TEXT NOT NULL,
          status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
          PRIMARY KEY(project_id, id));
        """
        )
        self._migrate_schema()
        self.db.commit()

    def _migrate_schema(self):
        try:
            info = self.db.execute("PRAGMA table_info(tasks)").fetchall()
            pks = [row["name"] for row in info if row["pk"] > 0]
            if pks == ["id"]:
                self.db.executescript(
                    """
                    CREATE TABLE tasks_v2(
                      id TEXT NOT NULL, project_id TEXT NOT NULL, goal TEXT NOT NULL,
                      allowed_files TEXT NOT NULL, verify_command TEXT NOT NULL, depends_on TEXT NOT NULL,
                      status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                      PRIMARY KEY (project_id, id));
                    INSERT OR REPLACE INTO tasks_v2 SELECT * FROM tasks;
                    DROP TABLE tasks;
                    ALTER TABLE tasks_v2 RENAME TO tasks;
                    """
                )
        except Exception:
            pass

    def close(self):
        with self._lock:
            self.db.close()

    def create_project(self, name: str, repo: str, goal: str) -> str:
        project_id = uuid.uuid4().hex[:12]
        now = int(time.time())
        with self._lock:
            self.db.execute(
                "INSERT INTO projects VALUES(?,?,?,?,?,?,?)",
                (project_id, name, repo, goal, "INTAKE", now, now),
            )
            self.db.commit()
        return project_id

    def get_project(self, project_id: str) -> dict:
        with self._lock:
            row = self.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise KeyError(project_id)
        return dict(row)

    def next_stage(self, project_id: str) -> str | None:
        with self._lock:
            completed = {r[0] for r in self.db.execute("SELECT stage FROM stages WHERE project_id=?", (project_id,))}
        return next((stage for stage in STAGES if stage not in completed), None)

    def record_stage(self, project_id: str, stage: str, model: str, prompt: str, artifact: str):
        with self._lock:
            expected = next(
                (
                    s
                    for s in STAGES
                    if s
                    not in {r[0] for r in self.db.execute("SELECT stage FROM stages WHERE project_id=?", (project_id,))}
                ),
                None,
            )
            if stage != expected:
                raise ValueError(f"invalid transition: expected {expected}, got {stage}")
            now = int(time.time())
            self.db.execute(
                "INSERT INTO stages(project_id,stage,model,prompt,artifact,created_at) VALUES(?,?,?,?,?,?)",
                (project_id, stage, model, prompt, artifact, now),
            )
            status = "DONE" if stage == STAGES[-1] else stage.upper()
            self.db.execute("UPDATE projects SET status=?,updated_at=? WHERE id=?", (status, now, project_id))
            self.db.commit()

    def get_artifact(self, project_id: str, stage: str) -> str:
        with self._lock:
            row = self.db.execute(
                "SELECT artifact FROM stages WHERE project_id=? AND stage=?",
                (project_id, stage),
            ).fetchone()
        return row[0] if row else ""

    def list_projects(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM projects ORDER BY updated_at DESC")]

    def list_stages(self, project_id: str) -> list[dict]:
        with self._lock:
            return [
                dict(row)
                for row in self.db.execute(
                    "SELECT stage,model,artifact,created_at FROM stages WHERE project_id=? ORDER BY id",
                    (project_id,),
                )
            ]

    def add_evidence(self, project_id: str, kind: str, data: dict):
        with self._lock:
            self.db.execute(
                "INSERT INTO evidence(project_id,kind,data,created_at) VALUES(?,?,?,?)",
                (project_id, kind, json.dumps(data), int(time.time())),
            )
            self.db.commit()

    def replace_tasks(self, project_id: str, tasks: list[dict]):
        with self._lock:
            self.db.execute("DELETE FROM tasks WHERE project_id=?", (project_id,))
            now = int(time.time())
            seen_ids = set()
            for t in tasks:
                tid = str(t.get("id", "")).strip()
                if not tid or tid in seen_ids:
                    continue
                seen_ids.add(tid)
                self.db.execute(
                    "INSERT OR REPLACE INTO tasks VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        tid,
                        project_id,
                        t.get("goal", ""),
                        json.dumps(t.get("allowed_files", [])),
                        t.get("verify_command", ""),
                        json.dumps(t.get("depends_on", [])),
                        t.get("status", "READY"),
                        now,
                        now,
                    ),
                )
            self.db.commit()

    def list_tasks(self, project_id: str) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM tasks WHERE project_id=? ORDER BY created_at, id",
                (project_id,),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["allowed_files"] = json.loads(item["allowed_files"])
            item["depends_on"] = json.loads(item["depends_on"])
            out.append(item)
        return out

    def set_task_status(self, task_id: str, status: str, project_id: str | None = None):
        with self._lock:
            if project_id:
                self.db.execute(
                    "UPDATE tasks SET status=?, updated_at=? WHERE id=? AND project_id=?",
                    (status, int(time.time()), task_id, project_id),
                )
            else:
                self.db.execute(
                    "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                    (status, int(time.time()), task_id),
                )
            self.db.commit()

    def update_project_status(self, project_id: str, status: str):
        with self._lock:
            now = int(time.time())
            self.db.execute("UPDATE projects SET status=?, updated_at=? WHERE id=?", (status, now, project_id))
            self.db.commit()

    def list_evidence(self, project_id: str, kind: str | None = None) -> list[dict]:
        with self._lock:
            if kind:
                rows = self.db.execute(
                    "SELECT id, project_id, kind, data, created_at FROM evidence WHERE project_id=? AND kind=? ORDER BY id",
                    (project_id, kind),
                ).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT id, project_id, kind, data, created_at FROM evidence WHERE project_id=? ORDER BY id",
                    (project_id,),
                ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["data"] = json.loads(item["data"])
            except Exception:
                pass
            out.append(item)
        return out
