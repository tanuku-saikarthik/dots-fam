"""SQLite store for everything except conversation state (which LangGraph checkpoints).

One file, WAL mode, a process-wide lock. Times are epoch milliseconds.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .schedule import next_cron_run

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS flags(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spaces(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS dots(
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, title TEXT NOT NULL DEFAULT '',
  instructions TEXT NOT NULL, model TEXT, can_delegate INTEGER NOT NULL DEFAULT 0,
  approval_mode TEXT NOT NULL DEFAULT 'reversible', research_allowed INTEGER NOT NULL DEFAULT 1,
  memory_allowed INTEGER NOT NULL DEFAULT 1, computer TEXT NOT NULL DEFAULT '{}',
  local TEXT NOT NULL DEFAULT '{}',
  family TEXT NOT NULL DEFAULT 'office', created_by TEXT,
  space_id TEXT NOT NULL REFERENCES spaces(id), color TEXT NOT NULL DEFAULT 'blue',
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS families(
  name TEXT PRIMARY KEY COLLATE NOCASE, title TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'hub', flow TEXT NOT NULL DEFAULT '[]', created_by TEXT,
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS dot_spaces(
  dot_id TEXT NOT NULL REFERENCES dots(id) ON DELETE CASCADE,
  space_id TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
  PRIMARY KEY(dot_id, space_id));
CREATE TABLE IF NOT EXISTS pages(
  id TEXT PRIMARY KEY, space_id TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
  parent_id TEXT, title TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1, author TEXT NOT NULL DEFAULT 'owner',
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS page_revisions(
  page_id TEXT NOT NULL, revision INTEGER NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
  author TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(page_id, revision));
CREATE TABLE IF NOT EXISTS threads(
  id TEXT PRIMARY KEY, dot_id TEXT NOT NULL REFERENCES dots(id) ON DELETE CASCADE,
  title TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'chat', internal INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, text TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  prompt TEXT NOT NULL, status TEXT NOT NULL, cron TEXT, timezone TEXT, interval_seconds INTEGER,
  next_run_at INTEGER, trigger_id TEXT, origin TEXT NOT NULL DEFAULT 'owner',
  error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS tasks_due ON tasks(status, next_run_at);
CREATE TABLE IF NOT EXISTS runs(
  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, task_id TEXT, source TEXT NOT NULL,
  status TEXT NOT NULL, text TEXT, error TEXT, started_at INTEGER NOT NULL, finished_at INTEGER);
CREATE INDEX IF NOT EXISTS runs_thread ON runs(thread_id, started_at);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, thread_id TEXT NOT NULL,
  kind TEXT NOT NULL, text TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS events_thread ON events(thread_id, id);
CREATE TABLE IF NOT EXISTS delegations(
  id TEXT PRIMARY KEY, group_id TEXT NOT NULL, parent_thread_id TEXT, worker_thread_id TEXT NOT NULL,
  from_dot_id TEXT NOT NULL, to_dot_id TEXT NOT NULL, brief TEXT NOT NULL,
  expected_output TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, result TEXT, error TEXT,
  model TEXT, delivered INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, finished_at INTEGER);
CREATE INDEX IF NOT EXISTS delegations_group ON delegations(group_id);
CREATE TABLE IF NOT EXISTS approvals(
  id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, thread_id TEXT NOT NULL, dot_id TEXT NOT NULL,
  tool_call_id TEXT NOT NULL, tool TEXT NOT NULL, args TEXT NOT NULL, reason TEXT NOT NULL,
  status TEXT NOT NULL, note TEXT, created_at INTEGER NOT NULL, decided_at INTEGER);
CREATE INDEX IF NOT EXISTS approvals_thread ON approvals(thread_id, status);
CREATE TABLE IF NOT EXISTS triggers(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  prompt TEXT NOT NULL, secret TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
  fire_count INTEGER NOT NULL DEFAULT 0, last_fired_at INTEGER, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS trigger_fires(trigger_id TEXT NOT NULL, fired_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS workspaces(
  id TEXT PRIMARY KEY, dot_id TEXT NOT NULL, thread_id TEXT NOT NULL, project_dir TEXT NOT NULL,
  path TEXT NOT NULL, work_root TEXT NOT NULL, git_dir TEXT NOT NULL, branch TEXT NOT NULL, base TEXT NOT NULL, base_commit TEXT NOT NULL,
  pushed_at INTEGER, pr_url TEXT, created_at INTEGER NOT NULL, UNIQUE(dot_id, thread_id));
CREATE TABLE IF NOT EXISTS push_subscriptions(
  endpoint TEXT PRIMARY KEY, keys TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS slack_threads(
  channel TEXT NOT NULL, ts TEXT NOT NULL, thread_id TEXT NOT NULL, PRIMARY KEY(channel, ts));
"""

DEFAULT_FLAGS = {"paused": False, "research_allowed": True, "memory_allowed": True}
TRIGGER_HOURLY_LIMIT = 30


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id() -> str:
    return uuid.uuid4().hex


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    pass


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._db.executescript(SCHEMA)
        # Additive migration: SCHEMA's CREATE TABLE IF NOT EXISTS won't add columns to a
        # dots table that already existed before this field was introduced.
        existing_columns = {row["name"] for row in self._db.execute("PRAGMA table_info(dots)")}
        if "local" not in existing_columns:
            self._db.execute("ALTER TABLE dots ADD COLUMN local TEXT NOT NULL DEFAULT '{}'")
        if "family" not in existing_columns:
            self._db.execute("ALTER TABLE dots ADD COLUMN family TEXT NOT NULL DEFAULT 'office'")
        if "created_by" not in existing_columns:
            self._db.execute("ALTER TABLE dots ADD COLUMN created_by TEXT")
        delegation_columns = {
            row["name"] for row in self._db.execute("PRAGMA table_info(delegations)")
        }
        if "kind" not in delegation_columns:
            self._db.execute("ALTER TABLE delegations ADD COLUMN kind TEXT NOT NULL DEFAULT 'delegate'")
        # Delegated work cannot survive a restart mid-run; record that honestly.
        self._db.execute(
            "UPDATE delegations SET status='failed', error='Server restarted before this work finished.',"
            " finished_at=? WHERE status='running'",
            (now_ms(),),
        )
        self._db.execute(
            "UPDATE runs SET status='interrupted', finished_at=? WHERE status='running'",
            (now_ms(),),
        )
        self._db.execute("UPDATE tasks SET status='queued' WHERE status='running'")

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def _all(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, args).fetchall()]

    def _one(self, sql: str, *args: Any) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
            return dict(row) if row else None

    def _run(self, sql: str, *args: Any) -> int:
        with self._lock:
            return self._db.execute(sql, args).rowcount

    # ---- flags -------------------------------------------------------------
    def flags(self) -> dict[str, bool]:
        values = dict(DEFAULT_FLAGS)
        for row in self._all("SELECT key, value FROM flags"):
            values[row["key"]] = json.loads(row["value"])
        return values

    def set_flags(self, **changes: bool) -> dict[str, bool]:
        for key, value in changes.items():
            if key not in DEFAULT_FLAGS:
                raise ValueError(f"Unknown setting {key}")
            self._run(
                "INSERT INTO flags(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                key,
                json.dumps(bool(value)),
            )
        return self.flags()

    # ---- spaces ------------------------------------------------------------
    def spaces(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM spaces ORDER BY created_at")

    def space(self, space_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM spaces WHERE id=?", space_id)
        if not row:
            raise NotFound("Space not found.")
        return row

    def space_by_name(self, name: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM spaces WHERE name=? COLLATE NOCASE", name)

    def create_space(self, name: str, description: str = "") -> dict[str, Any]:
        space_id = new_id()
        self._run(
            "INSERT INTO spaces(id, name, description, created_at) VALUES (?, ?, ?, ?)",
            space_id,
            name,
            description,
            now_ms(),
        )
        return self.space(space_id)

    # ---- dots --------------------------------------------------------------
    def _dot(self, row: dict[str, Any]) -> dict[str, Any]:
        row["can_delegate"] = bool(row["can_delegate"])
        row["research_allowed"] = bool(row["research_allowed"])
        row["memory_allowed"] = bool(row["memory_allowed"])
        row["computer"] = json.loads(row["computer"] or "{}")
        row["local"] = json.loads(row["local"] or "{}")
        row["space_ids"] = [
            item["space_id"]
            for item in self._all("SELECT space_id FROM dot_spaces WHERE dot_id=?", row["id"])
        ]
        return row

    def dots(self) -> list[dict[str, Any]]:
        return [self._dot(row) for row in self._all("SELECT * FROM dots ORDER BY created_at")]

    def dot(self, dot_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM dots WHERE id=?", dot_id)
        if not row:
            raise NotFound("Dot not found.")
        return self._dot(row)

    def dot_by_name(self, name: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM dots WHERE name=? COLLATE NOCASE", name.strip())
        return self._dot(row) if row else None

    def create_dot(
        self,
        *,
        name: str,
        instructions: str,
        space_id: str,
        title: str = "",
        model: str | None = None,
        can_delegate: bool = False,
        approval_mode: str = "reversible",
        research_allowed: bool = True,
        memory_allowed: bool = True,
        space_ids: list[str] | None = None,
        color: str = "blue",
        family: str = "office",
        created_by: str | None = None,
    ) -> dict[str, Any]:
        self.space(space_id)
        if self.dot_by_name(name):
            raise Conflict(f"A Dot named {name} already exists.")
        dot_id = new_id()
        with self.tx() as db:
            db.execute(
                "INSERT INTO dots(id, name, title, instructions, model, can_delegate, approval_mode,"
                " research_allowed, memory_allowed, space_id, color, created_at, family, created_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    dot_id,
                    name.strip(),
                    title,
                    instructions,
                    model or None,
                    int(can_delegate),
                    approval_mode,
                    int(research_allowed),
                    int(memory_allowed),
                    space_id,
                    color,
                    now_ms(),
                    family,
                    created_by,
                ),
            )
            for sid in sorted({space_id, *(space_ids or [])}):
                db.execute("INSERT INTO dot_spaces(dot_id, space_id) VALUES (?, ?)", (dot_id, sid))
        return self.dot(dot_id)

    def update_dot(self, dot_id: str, **patch: Any) -> dict[str, Any]:
        current = self.dot(dot_id)
        allowed = {
            "name",
            "title",
            "instructions",
            "model",
            "can_delegate",
            "approval_mode",
            "research_allowed",
            "memory_allowed",
            "space_id",
            "color",
            "computer",
            "local",
        }
        fields = {
            key: value for key, value in patch.items() if key in allowed and value is not None
        }
        if "model" in patch and patch["model"] in ("", None):
            fields["model"] = None
        if "name" in fields:
            other = self.dot_by_name(fields["name"])
            if other and other["id"] != dot_id:
                raise Conflict(f"A Dot named {fields['name']} already exists.")
        space_ids = patch.get("space_ids")
        default_space = fields.get("space_id", current["space_id"])
        with self.tx() as db:
            for key, value in fields.items():
                if key in ("can_delegate", "research_allowed", "memory_allowed"):
                    value = int(bool(value))
                if key in ("computer", "local"):
                    value = json.dumps(value)
                db.execute(f"UPDATE dots SET {key}=? WHERE id=?", (value, dot_id))  # noqa: S608
            if space_ids is not None:
                db.execute("DELETE FROM dot_spaces WHERE dot_id=?", (dot_id,))
                for sid in sorted({default_space, *space_ids}):
                    db.execute(
                        "INSERT INTO dot_spaces(dot_id, space_id) VALUES (?, ?)", (dot_id, sid)
                    )
            elif "space_id" in fields:
                db.execute(
                    "INSERT OR IGNORE INTO dot_spaces(dot_id, space_id) VALUES (?, ?)",
                    (dot_id, default_space),
                )
        return self.dot(dot_id)

    def delete_dot(self, dot_id: str) -> None:
        if not self._run("DELETE FROM dots WHERE id=?", dot_id):
            raise NotFound("Dot not found.")

    def can_access_space(self, dot_id: str, space_id: str) -> bool:
        return bool(
            self._one(
                "SELECT 1 AS ok FROM dot_spaces WHERE dot_id=? AND space_id=?", dot_id, space_id
            )
        )

    # ---- pages -------------------------------------------------------------
    def pages(self, space_id: str) -> list[dict[str, Any]]:
        self.space(space_id)
        return self._all(
            "SELECT id, space_id, parent_id, title, revision, author, created_at, updated_at,"
            " substr(content, 1, 240) AS preview FROM pages WHERE space_id=? ORDER BY updated_at DESC",
            space_id,
        )

    def page(self, page_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM pages WHERE id=?", page_id)
        if not row:
            raise NotFound("Page not found.")
        return row

    def page_by_title(self, space_id: str, title: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM pages WHERE space_id=? AND title=? COLLATE NOCASE", space_id, title
        )

    def create_page(
        self,
        space_id: str,
        title: str,
        content: str = "",
        parent_id: str | None = None,
        author: str = "owner",
    ) -> dict[str, Any]:
        self.space(space_id)
        if parent_id and self.page(parent_id)["space_id"] != space_id:
            raise Conflict("A parent page must be in the same Space.")
        page_id, at = new_id(), now_ms()
        with self.tx() as db:
            db.execute(
                "INSERT INTO pages(id, space_id, parent_id, title, content, revision, author, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (page_id, space_id, parent_id, title, content, author, at, at),
            )
            db.execute(
                "INSERT INTO page_revisions VALUES (?, 1, ?, ?, ?, ?)",
                (page_id, title, content, author, at),
            )
        return self.page(page_id)

    def update_page(
        self,
        page_id: str,
        *,
        expected_revision: int,
        title: str | None = None,
        content: str | None = None,
        author: str = "owner",
    ) -> dict[str, Any]:
        with self.tx() as db:
            row = db.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
            if not row:
                raise NotFound("Page not found.")
            if row["revision"] != expected_revision:
                raise Conflict(
                    f"Page changed since revision {expected_revision} (now {row['revision']}). Re-read it first."
                )
            revision, at = row["revision"] + 1, now_ms()
            new_title = title if title is not None else row["title"]
            new_content = content if content is not None else row["content"]
            db.execute(
                "UPDATE pages SET title=?, content=?, revision=?, author=?, updated_at=? WHERE id=?",
                (new_title, new_content, revision, author, at, page_id),
            )
            db.execute(
                "INSERT INTO page_revisions VALUES (?, ?, ?, ?, ?, ?)",
                (page_id, revision, new_title, new_content, author, at),
            )
        return self.page(page_id)

    def page_revisions(self, page_id: str) -> list[dict[str, Any]]:
        return self._all(
            "SELECT revision, title, author, created_at FROM page_revisions WHERE page_id=?"
            " ORDER BY revision DESC",
            page_id,
        )

    def delete_page(self, page_id: str) -> None:
        with self.tx() as db:
            db.execute("UPDATE pages SET parent_id=NULL WHERE parent_id=?", (page_id,))
            if not db.execute("DELETE FROM pages WHERE id=?", (page_id,)).rowcount:
                raise NotFound("Page not found.")

    # ---- threads -----------------------------------------------------------
    def create_thread(
        self, dot_id: str, title: str, kind: str = "chat", internal: bool = False
    ) -> dict[str, Any]:
        self.dot(dot_id)
        thread_id, at = new_id(), now_ms()
        self._run(
            "INSERT INTO threads(id, dot_id, title, kind, internal, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            thread_id,
            dot_id,
            title[:160],
            kind,
            int(internal),
            at,
            at,
        )
        return self.thread(thread_id)

    def thread(self, thread_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM threads WHERE id=?", thread_id)
        if not row:
            raise NotFound("Conversation not found.")
        row["internal"] = bool(row["internal"])
        return row

    def threads(self, dot_id: str | None = None) -> list[dict[str, Any]]:
        if dot_id:
            rows = self._all(
                "SELECT * FROM threads WHERE internal=0 AND dot_id=? ORDER BY updated_at DESC",
                dot_id,
            )
        else:
            rows = self._all("SELECT * FROM threads WHERE internal=0 ORDER BY updated_at DESC")
        for row in rows:
            row["internal"] = False
        return rows

    def touch_thread(self, thread_id: str, title: str | None = None) -> None:
        if title:
            self._run(
                "UPDATE threads SET updated_at=?, title=? WHERE id=?",
                now_ms(),
                title[:160],
                thread_id,
            )
        else:
            self._run("UPDATE threads SET updated_at=? WHERE id=?", now_ms(), thread_id)

    def delete_thread(self, thread_id: str) -> None:
        if not self._run("DELETE FROM threads WHERE id=?", thread_id):
            raise NotFound("Conversation not found.")

    # ---- memories ----------------------------------------------------------
    def memories(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM memories ORDER BY created_at DESC")

    def save_memory(self, text: str, memory_id: str | None = None) -> dict[str, Any]:
        memory_id = memory_id or new_id()
        self._run(
            "INSERT INTO memories(id, text, created_at) VALUES (?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET text=excluded.text",
            memory_id,
            text,
            now_ms(),
        )
        return self._one("SELECT * FROM memories WHERE id=?", memory_id) or {}

    def delete_memory(self, memory_id: str) -> None:
        if not self._run("DELETE FROM memories WHERE id=?", memory_id):
            raise NotFound("Memory not found.")

    # ---- tasks (routines, triggers, follow-ups) -----------------------------
    def create_task(
        self,
        thread_id: str,
        prompt: str,
        *,
        cron: str | None = None,
        timezone: str | None = None,
        interval_seconds: int | None = None,
        trigger_id: str | None = None,
        origin: str = "owner",
        at: int | None = None,
    ) -> dict[str, Any]:
        self.thread(thread_id)
        at = at or now_ms()
        tz = (timezone or "UTC") if cron else None
        next_run = next_cron_run(cron, tz, at) if cron else None
        task_id = new_id()
        self._run(
            "INSERT INTO tasks(id, thread_id, prompt, status, cron, timezone, interval_seconds, next_run_at,"
            " trigger_id, origin, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            task_id,
            thread_id,
            prompt,
            "scheduled" if cron else "queued",
            cron,
            tz,
            None if cron else interval_seconds,
            next_run,
            trigger_id,
            origin,
            at,
            at,
        )
        return self.task(task_id)

    def task(self, task_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM tasks WHERE id=?", task_id)
        if not row:
            raise NotFound("Task not found.")
        return row

    def tasks(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._all(
            "SELECT tasks.*, threads.dot_id, threads.title AS thread_title FROM tasks"
            " JOIN threads ON threads.id = tasks.thread_id ORDER BY tasks.updated_at DESC LIMIT ?",
            limit,
        )

    def due_tasks(self, at: int | None = None) -> list[dict[str, Any]]:
        at = at or now_ms()
        return self._all(
            "SELECT * FROM tasks WHERE status='queued' OR (status IN ('scheduled','completed','failed')"
            " AND next_run_at IS NOT NULL AND next_run_at<=?)"
            " ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END, COALESCE(next_run_at, created_at)",
            at,
        )

    def claim_task(self, task_id: str) -> dict[str, Any] | None:
        changed = self._run(
            "UPDATE tasks SET status='running', next_run_at=NULL, error=NULL, updated_at=?"
            " WHERE id=? AND status IN ('queued','scheduled','completed','failed')",
            now_ms(),
            task_id,
        )
        return self.task(task_id) if changed else None

    def finish_task(
        self, task_id: str, error: str | None = None, at: int | None = None
    ) -> dict[str, Any]:
        at = at or now_ms()
        task = self.task(task_id)
        if task["status"] != "running":
            return task
        if task["cron"]:
            status, next_run = (
                ("failed" if error else "scheduled"),
                next_cron_run(task["cron"], task["timezone"], at),
            )
        elif task["interval_seconds"] and not error:
            status, next_run = "completed", at + task["interval_seconds"] * 1000
        else:
            status, next_run = ("failed" if error else "completed"), None
        self._run(
            "UPDATE tasks SET status=?, next_run_at=?, error=?, updated_at=? WHERE id=?",
            status,
            next_run,
            error,
            at,
            task_id,
        )
        return self.task(task_id)

    def release_task(self, task_id: str) -> None:
        self._run("UPDATE tasks SET status='queued' WHERE id=? AND status='running'", task_id)

    def task_action(self, task_id: str, action: str) -> dict[str, Any]:
        task = self.task(task_id)
        at = now_ms()
        if action == "run":
            if task["status"] != "running":
                self._run(
                    "UPDATE tasks SET status='queued', error=NULL, updated_at=? WHERE id=?",
                    at,
                    task_id,
                )
        elif action == "pause":
            self._run(
                "UPDATE tasks SET status='paused', next_run_at=NULL, updated_at=? WHERE id=?",
                at,
                task_id,
            )
        elif action == "resume":
            if task["cron"]:
                self._run(
                    "UPDATE tasks SET status='scheduled', next_run_at=?, error=NULL, updated_at=? WHERE id=?",
                    next_cron_run(task["cron"], task["timezone"], at),
                    at,
                    task_id,
                )
            else:
                self._run("UPDATE tasks SET status='queued', updated_at=? WHERE id=?", at, task_id)
        elif action == "cancel":
            self._run(
                "UPDATE tasks SET status='cancelled', next_run_at=NULL, updated_at=? WHERE id=?",
                at,
                task_id,
            )
        else:
            raise ValueError("Unknown task action.")
        return self.task(task_id)

    def schedule_task(
        self,
        task_id: str,
        *,
        cron: str | None = None,
        timezone: str | None = None,
        interval_seconds: int | None = None,
    ) -> dict[str, Any]:
        task = self.task(task_id)
        at = now_ms()
        if cron:
            tz = timezone or task["timezone"] or "UTC"
            next_run = next_cron_run(cron, tz, at)
            status = (
                task["status"]
                if task["status"] in ("running", "paused", "cancelled")
                else "scheduled"
            )
            self._run(
                "UPDATE tasks SET cron=?, timezone=?, interval_seconds=NULL, next_run_at=?, status=?,"
                " updated_at=? WHERE id=?",
                cron,
                tz,
                next_run if status == "scheduled" else None,
                status,
                at,
                task_id,
            )
        else:
            status = "completed" if task["status"] == "scheduled" else task["status"]
            next_run = (
                at + interval_seconds * 1000 if interval_seconds and status == "completed" else None
            )
            self._run(
                "UPDATE tasks SET cron=NULL, timezone=NULL, interval_seconds=?, next_run_at=?, status=?,"
                " updated_at=? WHERE id=?",
                interval_seconds,
                next_run,
                status,
                at,
                task_id,
            )
        return self.task(task_id)

    # ---- runs & events -----------------------------------------------------
    def create_run(self, thread_id: str, source: str, task_id: str | None = None) -> dict[str, Any]:
        run_id = new_id()
        self._run(
            "INSERT INTO runs(id, thread_id, task_id, source, status, started_at) VALUES (?, ?, ?, ?, 'running', ?)",
            run_id,
            thread_id,
            task_id,
            source,
            now_ms(),
        )
        return self.run_record(run_id)

    def run_record(self, run_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM runs WHERE id=?", run_id)
        if not row:
            raise NotFound("Run not found.")
        return row

    def finish_run(
        self, run_id: str, status: str, text: str | None = None, error: str | None = None
    ) -> None:
        self._run(
            "UPDATE runs SET status=?, text=?, error=?, finished_at=? WHERE id=?",
            status,
            text,
            error,
            now_ms(),
            run_id,
        )

    def runs(self, thread_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._all(
            "SELECT * FROM runs WHERE thread_id=? ORDER BY started_at DESC LIMIT ?",
            thread_id,
            limit,
        )

    def add_event(self, thread_id: str, kind: str, text: str, run_id: str | None = None) -> None:
        self._run(
            "INSERT INTO events(run_id, thread_id, kind, text, created_at) VALUES (?, ?, ?, ?, ?)",
            run_id,
            thread_id,
            kind,
            text[:600],
            now_ms(),
        )

    def events(self, thread_ids: list[str], limit: int = 400) -> list[dict[str, Any]]:
        if not thread_ids:
            return []
        marks = ",".join("?" for _ in thread_ids)
        return self._all(
            f"SELECT * FROM events WHERE thread_id IN ({marks}) ORDER BY id DESC LIMIT ?",  # noqa: S608
            *thread_ids,
            limit,
        )[::-1]

    # ---- delegations -------------------------------------------------------
    def create_delegation(self, **values: Any) -> dict[str, Any]:
        delegation_id = new_id()
        self._run(
            "INSERT INTO delegations(id, group_id, parent_thread_id, worker_thread_id, from_dot_id, to_dot_id,"
            " brief, expected_output, status, model, created_at, kind)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?)",
            delegation_id,
            values["group_id"],
            values.get("parent_thread_id"),
            values["worker_thread_id"],
            values["from_dot_id"],
            values["to_dot_id"],
            values["brief"],
            values.get("expected_output", ""),
            values.get("model"),
            now_ms(),
            values.get("kind", "delegate"),
        )
        return self.delegation(delegation_id)

    def count_peer_asks(self, parent_thread_id: str) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM delegations WHERE parent_thread_id=? AND kind='peer'",
            parent_thread_id,
        )
        return int(row["n"]) if row else 0

    def delegation(self, delegation_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM delegations WHERE id=?", delegation_id)
        if not row:
            raise NotFound("Delegation not found.")
        return row

    def delegation_for_worker(self, worker_thread_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM delegations WHERE worker_thread_id=?", worker_thread_id)

    def delegations(self, limit: int = 80) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM delegations ORDER BY created_at DESC LIMIT ?", limit)

    def delegation_group(self, group_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM delegations WHERE group_id=? ORDER BY created_at", group_id)

    def set_delegation(
        self,
        delegation_id: str,
        status: str,
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        final = status in ("completed", "failed", "cancelled")
        self._run(
            "UPDATE delegations SET status=?, result=COALESCE(?, result), error=COALESCE(?, error),"
            " finished_at=CASE WHEN ? THEN ? ELSE finished_at END WHERE id=?",
            status,
            result,
            error,
            int(final),
            now_ms(),
            delegation_id,
        )

    def mark_delivered(self, group_id: str) -> bool:
        return bool(
            self._run(
                "UPDATE delegations SET delivered=1 WHERE group_id=? AND delivered=0", group_id
            )
        )

    # ---- approvals ---------------------------------------------------------
    def create_approvals(
        self, thread_id: str, dot_id: str, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        batch_id, at = new_id(), now_ms()
        with self.tx() as db:
            for item in items:
                db.execute(
                    "INSERT INTO approvals(id, batch_id, thread_id, dot_id, tool_call_id, tool, args, reason,"
                    " status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                    (
                        new_id(),
                        batch_id,
                        thread_id,
                        dot_id,
                        item["tool_call_id"],
                        item["tool"],
                        json.dumps(item.get("args", {}))[:8000],
                        item["reason"],
                        at,
                    ),
                )
        return self.approvals(batch_id=batch_id)

    def approvals(
        self, *, batch_id: str | None = None, thread_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if batch_id:
            rows = self._all(
                "SELECT * FROM approvals WHERE batch_id=? ORDER BY created_at", batch_id
            )
        elif thread_id:
            rows = self._all(
                "SELECT * FROM approvals WHERE thread_id=? ORDER BY created_at DESC LIMIT ?",
                thread_id,
                limit,
            )
        else:
            rows = self._all(
                "SELECT * FROM approvals ORDER BY status='pending' DESC, created_at DESC LIMIT ?",
                limit,
            )
        for row in rows:
            row["args"] = json.loads(row["args"])
        return rows

    def approval(self, approval_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM approvals WHERE id=?", approval_id)
        if not row:
            raise NotFound("Approval not found.")
        row["args"] = json.loads(row["args"])
        return row

    def decide_approval(
        self, approval_id: str, decision: str, note: str | None = None
    ) -> dict[str, Any]:
        if decision not in ("approved", "declined"):
            raise ValueError("Approve or decline.")
        changed = self._run(
            "UPDATE approvals SET status=?, note=?, decided_at=? WHERE id=? AND status='pending'",
            decision,
            note,
            now_ms(),
            approval_id,
        )
        if not changed:
            self.approval(approval_id)
            raise Conflict("This approval was already decided.")
        return self.approval(approval_id)

    def reopen_approvals(self, batch_id: str) -> None:
        """Undo decisions whose resume could not start, so the owner can decide again."""
        self._run(
            "UPDATE approvals SET status='pending', note=NULL, decided_at=NULL WHERE batch_id=?",
            batch_id,
        )

    def pending_approvals(self, thread_id: str | None = None) -> int:
        if thread_id:
            row = self._one(
                "SELECT COUNT(*) AS n FROM approvals WHERE thread_id=? AND status='pending'",
                thread_id,
            )
        else:
            row = self._one("SELECT COUNT(*) AS n FROM approvals WHERE status='pending'")
        return int(row["n"]) if row else 0

    def expire_approvals(self, thread_id: str, note: str) -> None:
        self._run(
            "UPDATE approvals SET status='expired', note=?, decided_at=? WHERE thread_id=? AND status='pending'",
            note,
            now_ms(),
            thread_id,
        )

    # ---- triggers ----------------------------------------------------------
    def _trigger(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row.pop("secret", None)
        row["enabled"] = bool(row["enabled"])
        return row

    def create_trigger(self, name: str, thread_id: str, prompt: str) -> tuple[dict[str, Any], str]:
        self.thread(thread_id)
        trigger_id, secret = new_id(), secrets.token_urlsafe(24)
        self._run(
            "INSERT INTO triggers(id, name, thread_id, prompt, secret, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            trigger_id,
            name,
            thread_id,
            prompt,
            secret,
            now_ms(),
        )
        return self.trigger(trigger_id), secret

    def trigger(self, trigger_id: str) -> dict[str, Any]:
        row = self._one("SELECT * FROM triggers WHERE id=?", trigger_id)
        if not row:
            raise NotFound("Trigger not found.")
        return self._trigger(row)

    def trigger_secret(self, trigger_id: str) -> str | None:
        row = self._one("SELECT secret FROM triggers WHERE id=?", trigger_id)
        return row["secret"] if row else None

    def triggers(self) -> list[dict[str, Any]]:
        return [
            self._trigger(row)
            for row in self._all("SELECT * FROM triggers ORDER BY created_at DESC")
        ]

    def set_trigger_enabled(self, trigger_id: str, enabled: bool) -> dict[str, Any]:
        if not self._run("UPDATE triggers SET enabled=? WHERE id=?", int(enabled), trigger_id):
            raise NotFound("Trigger not found.")
        return self.trigger(trigger_id)

    def rotate_trigger(self, trigger_id: str) -> str:
        secret = secrets.token_urlsafe(24)
        if not self._run("UPDATE triggers SET secret=? WHERE id=?", secret, trigger_id):
            raise NotFound("Trigger not found.")
        return secret

    def delete_trigger(self, trigger_id: str) -> None:
        self._run("DELETE FROM trigger_fires WHERE trigger_id=?", trigger_id)
        if not self._run("DELETE FROM triggers WHERE id=?", trigger_id):
            raise NotFound("Trigger not found.")

    def fire_trigger(self, trigger_id: str, at: int | None = None) -> None:
        at = at or now_ms()
        with self.tx() as db:
            recent = db.execute(
                "SELECT COUNT(*) FROM trigger_fires WHERE trigger_id=? AND fired_at>?",
                (trigger_id, at - 3_600_000),
            ).fetchone()[0]
            if recent >= TRIGGER_HOURLY_LIMIT:
                raise Conflict("This trigger reached its hourly limit.")
            db.execute("INSERT INTO trigger_fires VALUES (?, ?)", (trigger_id, at))
            db.execute(
                "DELETE FROM trigger_fires WHERE trigger_id=? AND fired_at<=?",
                (trigger_id, at - 3_600_000),
            )
            db.execute(
                "UPDATE triggers SET fire_count=fire_count+1, last_fired_at=? WHERE id=?",
                (at, trigger_id),
            )

    # ---- slack ---------------------------------------------------------------
    def slack_thread(self, channel: str, ts: str) -> str | None:
        row = self._one("SELECT thread_id FROM slack_threads WHERE channel=? AND ts=?", channel, ts)
        return row["thread_id"] if row else None

    def bind_slack_thread(self, channel: str, ts: str, thread_id: str) -> None:
        self._run("INSERT OR REPLACE INTO slack_threads VALUES (?, ?, ?)", channel, ts, thread_id)

    # ---- push subscriptions (phones and browsers that can ring) --------------
    def add_push_subscription(self, endpoint: str, keys: dict[str, str], label: str = "") -> None:
        self._run(
            "INSERT OR REPLACE INTO push_subscriptions VALUES (?, ?, ?, ?)",
            endpoint,
            json.dumps(keys),
            label[:80],
            now_ms(),
        )

    def push_subscriptions(self) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM push_subscriptions ORDER BY created_at")
        for row in rows:
            row["keys"] = json.loads(row["keys"])
        return rows

    def remove_push_subscription(self, endpoint: str) -> None:
        self._run("DELETE FROM push_subscriptions WHERE endpoint=?", endpoint)

    def slack_binding(self, thread_id: str) -> dict[str, str] | None:
        """The Slack thread a conversation started in (the first binding wins)."""
        return self._one(
            "SELECT channel, ts FROM slack_threads WHERE thread_id=? ORDER BY rowid LIMIT 1",
            thread_id,
        )

    # ---- build-mode workspaces ----------------------------------------------------------
    def workspace(self, dot_id: str, thread_id: str) -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM workspaces WHERE dot_id=? AND thread_id=?", dot_id, thread_id
        )

    def workspaces(self, dot_id: str | None = None) -> list[dict[str, Any]]:
        if dot_id:
            return self._all(
                "SELECT * FROM workspaces WHERE dot_id=? ORDER BY created_at DESC", dot_id
            )
        return self._all("SELECT * FROM workspaces ORDER BY created_at DESC")

    def add_workspace(self, **fields: Any) -> dict[str, Any]:
        row = {"id": new_id(), "pushed_at": None, "pr_url": None, "created_at": now_ms(), **fields}
        columns = ", ".join(row)
        self._run(
            f"INSERT INTO workspaces({columns}) VALUES ({', '.join('?' * len(row))})",
            *row.values(),
        )
        return row

    def update_workspace(self, workspace_id: str, **fields: Any) -> None:
        sets = ", ".join(f"{key}=?" for key in fields)
        self._run(f"UPDATE workspaces SET {sets} WHERE id=?", *fields.values(), workspace_id)

    def remove_workspace(self, workspace_id: str) -> None:
        self._run("DELETE FROM workspaces WHERE id=?", workspace_id)

    # ---- families (teams of Dots) -------------------------------------------------------
    def families(self) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM families ORDER BY created_at")
        for row in rows:
            row["flow"] = json.loads(row["flow"] or "[]")
        return rows

    def family(self, name: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM families WHERE name=? COLLATE NOCASE", name)
        if row:
            row["flow"] = json.loads(row["flow"] or "[]")
        return row

    def save_family(
        self,
        name: str,
        *,
        title: str = "",
        summary: str = "",
        kind: str = "hub",
        flow: list[dict[str, Any]] | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        self._run(
            "INSERT INTO families(name, title, summary, kind, flow, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET title=excluded.title, summary=excluded.summary,"
            " kind=excluded.kind, flow=excluded.flow",
            name.strip(),
            title,
            summary,
            kind,
            json.dumps(flow or []),
            created_by,
            now_ms(),
        )
        return self.family(name)  # type: ignore[return-value]

    def family_members(self, name: str) -> list[dict[str, Any]]:
        return [d for d in self.dots() if d["family"].lower() == name.lower()]

    def delete_family(self, name: str) -> None:
        self._run("DELETE FROM families WHERE name=? COLLATE NOCASE", name)
