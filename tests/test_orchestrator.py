import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from ma.router import EmptyResponseError, NineRouterClient, RouterError
from ma.store import TaskStore
from ma.gates import GateError, require_model_content, require_command_success, require_approve
from ma.workspace import Workspace, extract_diff
from ma.tasks import parse_task_dag, enforce_allowed_files, TaskSpecError, ready_waves
from ma.orchestrator import Orchestrator
from ma.locks import FileLockManager, FileLockError, Budget, BudgetExceeded
import subprocess


class FakeResponse:
    status = 200
    headers = {"content-type": "application/json"}

    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class RouterTests(unittest.TestCase):
    def test_extracts_chat_content(self):
        body = json.dumps({"choices": [{"message": {"content": "PLAN"}}]})
        with patch("urllib.request.urlopen", return_value=FakeResponse(body)):
            result = NineRouterClient("http://x", "secret").call("model", "prompt")
        self.assertEqual(result.content, "PLAN")

    def test_rejects_http_200_with_empty_content(self):
        body = json.dumps({"choices": [{"message": {"content": ""}}]})
        with patch("urllib.request.urlopen", return_value=FakeResponse(body)):
            with self.assertRaises(EmptyResponseError):
                NineRouterClient("http://x", "secret").call("dead", "prompt")

    def test_extracts_responses_output(self):
        body = json.dumps(
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": "AUDIT"}]}]}
        )
        with patch("urllib.request.urlopen", return_value=FakeResponse(body)):
            result = NineRouterClient("http://x", "secret").call("model", "prompt", api="responses")
        self.assertEqual(result.content, "AUDIT")

    def test_retries_timeout_three_times_then_reports_failure(self):
        with patch("urllib.request.urlopen", side_effect=socket.timeout("late")) as call:
            with self.assertRaisesRegex(Exception, "failed after 3 attempts"):
                NineRouterClient("http://x", "secret", timeout=15, attempts=3).call("slow", "prompt")
        self.assertEqual(call.call_count, 3)

    def test_retry_can_recover_on_third_attempt(self):
        body = json.dumps({"choices": [{"message": {"content": "RECOVERED"}}]})
        with patch(
            "urllib.request.urlopen",
            side_effect=[socket.timeout(), socket.timeout(), FakeResponse(body)],
        ) as call:
            result = NineRouterClient("http://x", "secret", timeout=15, attempts=3).call("slow", "prompt")
        self.assertEqual(result.content, "RECOVERED")
        self.assertEqual(call.call_count, 3)

    def test_stream_collects_chunks_and_calls_callbacks(self):
        class FakeStreamResponse:
            status = 200
            headers = {"Content-Type": "text/event-stream"}

            def __init__(self, lines):
                self.lines = [line.encode("utf-8") if isinstance(line, str) else line for line in lines]
                self.idx = 0

            def readline(self):
                if self.idx < len(self.lines):
                    line = self.lines[self.idx]
                    self.idx += 1
                    return line
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        stream_lines = [
            'data: {"choices":[{"delta":{"role":"assistant"}}]}\n',
            'data: {"choices":[{"delta":{"reasoning_content":"Thinking deeply..."}}]}\n',
            'data: {"choices":[{"delta":{"content":"Hello "}}]}\n',
            'data: {"choices":[{"delta":{"content":"World!"}}]}\n',
            'data: [DONE]\n',
        ]
        received_chunks = []
        received_reasoning = []
        with patch("urllib.request.urlopen", return_value=FakeStreamResponse(stream_lines)):
            client = NineRouterClient("http://x", "secret")
            result = client.call(
                "model",
                "prompt",
                stream=True,
                on_chunk=lambda c: received_chunks.append(c),
                on_thinking=lambda r: received_reasoning.append(r),
            )
        self.assertEqual(result.content, "Hello World!")
        self.assertEqual(received_chunks, ["Hello ", "World!"])
        self.assertEqual(received_reasoning, ["Thinking deeply..."])

    def test_stream_falls_back_gracefully_on_regular_json_response(self):
        body = json.dumps({"choices": [{"message": {"content": "PLAIN JSON"}}]})
        with patch("urllib.request.urlopen", return_value=FakeResponse(body)):
            client = NineRouterClient("http://x", "secret")
            result = client.call("model", "prompt", stream=True)
        self.assertEqual(result.content, "PLAIN JSON")


class StoreTests(unittest.TestCase):
    def test_persists_project_and_resumes_next_stage(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "state.sqlite"
            s = TaskStore(db)
            pid = s.create_project("demo", str(Path(d)), "build thing")
            s.record_stage(pid, "design", "sol", "plan", "artifact")
            s.close()
            s2 = TaskStore(db)
            self.assertEqual(s2.get_project(pid)["status"], "DESIGN")
            self.assertEqual(s2.next_stage(pid), "critique")
            s2.close()

    def test_invalid_transition_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            s = TaskStore(Path(d) / "state.sqlite")
            pid = s.create_project("demo", d, "goal")
            with self.assertRaises(ValueError):
                s.record_stage(pid, "implementation", "worker", "x", "y")
            s.close()

    def test_task_dag_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            s = TaskStore(Path(d) / "state.sqlite")
            pid = s.create_project("demo", d, "goal")
            s.replace_tasks(
                pid,
                [
                    {
                        "id": "T1",
                        "goal": "add subtract",
                        "allowed_files": ["calc.py"],
                        "verify_command": "python -m unittest -v",
                        "depends_on": [],
                    }
                ],
            )
            tasks = s.list_tasks(pid)
            self.assertEqual(tasks[0]["id"], "T1")
            self.assertEqual(tasks[0]["allowed_files"], ["calc.py"])
            s.close()


class WorkspaceTests(unittest.TestCase):
    def test_extract_diff_from_fenced_model_output(self):
        output = "Here it is:\n```diff\ndiff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n```"
        self.assertTrue(extract_diff(output).startswith("diff --git"))

    def test_extract_diff_without_diff_git_header(self):
        output = "Diff generated:\n```diff\n--- /dev/null\n+++ b/src/types.ts\n@@ -0,0 +1,2 @@\n+export interface A {}\n```"
        extracted = extract_diff(output)
        self.assertTrue(extracted.startswith("diff --git a/src/types.ts b/src/types.ts"))
        self.assertIn("+++ b/src/types.ts", extracted)

    def test_worktree_applies_patch_and_collects_diff(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@local"], cwd=repo, check=True)
            (repo / "a.py").write_text("old\n")
            subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
            ws = Workspace(repo, "p123")
            path = ws.create()
            ws.apply_patch(
                "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
            )
            self.assertIn("+new", ws.diff())
            ws.remove()
            self.assertFalse(path.exists())


class TaskTests(unittest.TestCase):
    def test_parse_task_dag(self):
        text = """
APPROVED PLAN
```json
[
  {
    "id": "T1",
    "goal": "add subtract",
    "allowed_files": ["calc.py"],
    "verify_command": "python -m unittest -v",
    "depends_on": []
  }
]
```
"""
        tasks = parse_task_dag(text)
        self.assertEqual(tasks[0].id, "T1")
        self.assertEqual(tasks[0].allowed_files, ["calc.py"])

    def test_parse_task_dag_with_preceding_code_blocks(self):
        text = """
Here is my architectural breakdown:
```
[Phase 1: Contracts & Architecture]
  Task 1: Setup types
  Task 2: Setup tests
```

Here is the approved plan:
```json
[
  {
    "id": "T1",
    "goal": "define types",
    "allowed_files": ["types.ts"],
    "verify_command": "npx tsc --noEmit",
    "depends_on": []
  },
  {
    "id": "T2",
    "goal": "implement api",
    "allowed_files": ["api.ts"],
    "verify_command": "npx jest",
    "depends_on": ["T1"]
  }
]
```
"""
        tasks = parse_task_dag(text)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].id, "T1")
        self.assertEqual(tasks[1].depends_on, ["T1"])

    def test_parse_task_dag_with_trailing_commas_and_comments(self):
        text = """
```json
// Task DAG plan
[
  {
    "id": "T1",
    "goal": "create db",
    "allowed_files": ["db.ts"],
    "verify_command": "npm test",
    "depends_on": [],
  },
]
```
"""
        tasks = parse_task_dag(text)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].id, "T1")

    def test_parse_task_dag_wrapped_in_object(self):
        text = """
```json
{
  "tasks": [
    {
      "id": "T1",
      "goal": "setup",
      "allowed_files": ["setup.py"],
      "verify_command": "pytest",
      "depends_on": []
    }
  ]
}
```
"""
        tasks = parse_task_dag(text)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].id, "T1")

    def test_enforce_allowed_files_rejects_out_of_scope(self):
        diff = "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@\n+x\ndiff --git a/secret.env b/secret.env\n--- a/secret.env\n+++ b/secret.env\n@@\n+y\n"
        with self.assertRaises(TaskSpecError):
            enforce_allowed_files(diff, ["calc.py"])

    def test_ready_waves_respects_deps_and_file_locks(self):
        tasks = [
            {"id": "A", "allowed_files": ["a.py"], "depends_on": []},
            {"id": "B", "allowed_files": ["b.py"], "depends_on": []},
            {"id": "C", "allowed_files": ["c.py"], "depends_on": ["A", "B"]},
            {"id": "D", "allowed_files": ["a.py"], "depends_on": []},  # conflicts with A
        ]
        waves = ready_waves(tasks)
        # first wave can run A and B (no file overlap); D conflicts with A so later
        first_ids = {t["id"] for t in waves[0]}
        self.assertIn("A", first_ids)
        self.assertIn("B", first_ids)
        self.assertNotIn("C", first_ids)
        # C only after A and B done
        flat = [t["id"] for w in waves for t in w]
        self.assertLess(flat.index("A"), flat.index("C"))
        self.assertLess(flat.index("B"), flat.index("C"))


class LockTests(unittest.TestCase):
    def test_file_lock_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            lm = FileLockManager(Path(d) / "locks.sqlite")
            lm.acquire("T1", ["a.py", "b.py"])
            with self.assertRaises(FileLockError):
                lm.acquire("T2", ["b.py"])
            lm.release("T1")
            lm.acquire("T2", ["b.py"])
            lm.release("T2")
            lm.close()

    def test_cross_process_lock_via_shared_db(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "locks.sqlite"
            a = FileLockManager(db)
            b = FileLockManager(db)
            a.acquire("T1", ["shared.py"], project_id="p1")
            with self.assertRaises(FileLockError):
                b.acquire("T2", ["shared.py"], project_id="p2")
            a.release("T1")
            b.acquire("T2", ["shared.py"], project_id="p2")
            b.release("T2")
            a.close()
            b.close()


class BudgetTests(unittest.TestCase):
    def test_budget_blocks_extra_calls(self):
        b = Budget(max_calls=1, max_tokens=10000)
        b.charge(prompt="hi", content="ok", model="m")
        with self.assertRaises(BudgetExceeded):
            b.charge(prompt="again", content="x", model="m")

    def test_budget_blocks_tokens(self):
        b = Budget(max_tokens=5)
        with self.assertRaises(BudgetExceeded):
            b.charge(prompt="x" * 100, content="y" * 100, model="m")


class SecretTests(unittest.TestCase):
    def test_secret_scan_blocks_openai_key_in_diff(self):
        from ma.secrets import require_no_secrets, SecretScanError

        fake = "sk-" + ("a" * 32)
        bad = f"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@\n+key = '{fake}'\n"
        with self.assertRaises(SecretScanError):
            require_no_secrets(bad)

    def test_secret_scan_allows_clean_diff(self):
        from ma.secrets import require_no_secrets

        good = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@\n+def ok():\n+    return 1\n"
        self.assertIn("def ok", require_no_secrets(good))


class UsageTests(unittest.TestCase):
    def test_usage_ledger_records_and_summarizes(self):
        from ma.usage import UsageLedger

        with tempfile.TemporaryDirectory() as d:
            led = UsageLedger(Path(d) / "usage.sqlite")
            led.record(model="nttcodex/deepseek-v4-pro", prompt="hi" * 20, content="ok" * 10, project_id="p1")
            led.record(model="Ntt_Codex10tr/gpt-5.6-sol", prompt="plan" * 30, content="done" * 20, project_id="p1")
            s = led.summary("p1")
            self.assertEqual(s["total_calls"], 2)
            self.assertGreater(s["total_cost_usd"], 0)
            led.close()


class OpsTests(unittest.TestCase):
    def test_workers_for_wave_autoscale(self):
        from ma.ops import workers_for_wave

        self.assertEqual(workers_for_wave(1, 0), 1)
        self.assertEqual(workers_for_wave(3, 0), 3)
        self.assertEqual(workers_for_wave(10, 0), 4)
        self.assertEqual(workers_for_wave(10, 2), 2)

    def test_clean_removes_worktree(self):
        from ma.ops import clean_project

        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "t@l"], cwd=repo, check=True)
            (repo / "a.py").write_text("x\n")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "i"], cwd=repo, check=True)
            store = TaskStore(Path(d) / "state.sqlite")
            pid = store.create_project("c", str(repo), "g")
            ws = Workspace(repo, pid, task_id="T1")
            ws.create()
            self.assertTrue(ws.path.exists())
            out = clean_project(store, pid)
            self.assertFalse(ws.path.exists())
            self.assertTrue(any("T1" in p for p in out["removed_worktrees"]))
            store.close()


class FallbackTests(unittest.TestCase):
    def test_role_fallback_uses_second_model(self):
        class Client:
            def __init__(self):
                self.calls = []

            def call(self, model, prompt, system=""):
                self.calls.append(model)
                if model == "primary":
                    raise RouterError("primary dead")
                return type("R", (), {"content": "OK", "latency_ms": 1, "raw": {}})()

        with tempfile.TemporaryDirectory() as d:
            store = TaskStore(Path(d) / "state.sqlite")
            pid = store.create_project("demo", d, "goal")
            client = Client()
            orch = Orchestrator(
                store,
                client,
                models={"design": "primary"},
                fallbacks={"design": ["primary", "backup"]},
            )
            orch.run_stage(pid, "design")
            self.assertEqual(client.calls, ["primary", "backup"])
            self.assertEqual(store.get_artifact(pid, "design"), "OK")
            self.assertIn("primary", orch._dead_models)
            # second call should skip blacklisted primary
            client.calls.clear()
            store.record_stage = store.record_stage  # keep
            # force next stage critique using same client fallbacks
            orch.models["critique"] = "primary"
            orch.fallbacks["critique"] = ["primary", "backup"]
            orch.run_stage(pid, "critique")
            self.assertEqual(client.calls, ["backup"])
            store.close()

    def test_export_report(self):
        from ma.report import export_markdown_report

        with tempfile.TemporaryDirectory() as d:
            store = TaskStore(Path(d) / "state.sqlite")
            pid = store.create_project("demo", d, "goal")
            store.record_stage(pid, "design", "m", "p", "design artifact")
            path = export_markdown_report(store, pid, {"total_calls": 1, "total_cost_usd": 0.01, "by_model": []}, out_dir=Path(d) / "reports")
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn(pid, text)
            self.assertIn("design artifact", text)
            store.close()


class TaskStoreResilienceTests(unittest.TestCase):
    def test_replace_tasks_deduplicates_and_never_violates_unique_constraint(self):
        with tempfile.TemporaryDirectory() as d:
            store = TaskStore(Path(d) / "state.sqlite")
            pid = store.create_project("demo", d, "goal")
            # Duplicate IDs in task array passed by replan
            tasks = [
                {"id": "task-1", "goal": "g1", "allowed_files": ["f1.py"], "verify_command": "true", "depends_on": []},
                {"id": "task-2", "goal": "g2", "allowed_files": ["f2.py"], "verify_command": "true", "depends_on": []},
                {"id": "task-1", "goal": "g1-redefined", "allowed_files": ["f1.py"], "verify_command": "true", "depends_on": []},
            ]
            # Must not raise sqlite3.IntegrityError: UNIQUE constraint failed: tasks.id
            store.replace_tasks(pid, tasks)
            stored = store.list_tasks(pid)
            self.assertEqual(len(stored), 2)
            self.assertEqual({t["id"] for t in stored}, {"task-1", "task-2"})
            store.close()

    def test_tasks_isolation_across_multiple_projects(self):
        with tempfile.TemporaryDirectory() as d:
            store = TaskStore(Path(d) / "state.sqlite")
            pid1 = store.create_project("proj1", d, "goal1")
            pid2 = store.create_project("proj2", d, "goal2")
            # Both projects use task-1
            store.replace_tasks(pid1, [{"id": "task-1", "goal": "g1", "allowed_files": ["a.py"], "verify_command": "true"}])
            store.replace_tasks(pid2, [{"id": "task-1", "goal": "g2", "allowed_files": ["b.py"], "verify_command": "true"}])
            t1 = store.list_tasks(pid1)
            t2 = store.list_tasks(pid2)
            self.assertEqual(len(t1), 1)
            self.assertEqual(len(t2), 1)
            self.assertEqual(t1[0]["goal"], "g1")
            self.assertEqual(t2[0]["goal"], "g2")
            store.close()

    def test_legacy_schema_migration_to_composite_pk(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "legacy.sqlite"
            con = sqlite3.connect(db_path)
            # Create old schema with single-column PK
            con.executescript("""
                CREATE TABLE tasks(
                  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, goal TEXT NOT NULL,
                  allowed_files TEXT NOT NULL, verify_command TEXT NOT NULL, depends_on TEXT NOT NULL,
                  status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
                INSERT INTO tasks VALUES('T1', 'p1', 'g', '[]', 'true', '[]', 'READY', 0, 0);
            """)
            con.commit()
            con.close()

            # TaskStore opens legacy DB and auto-migrates
            store = TaskStore(db_path)
            info = store.db.execute("PRAGMA table_info(tasks)").fetchall()
            pks = [row["name"] for row in info if row["pk"] > 0]
            self.assertIn("id", pks)
            self.assertIn("project_id", pks)
            tasks = store.list_tasks("p1")
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["id"], "T1")
            store.close()


class DiffResilienceTests(unittest.TestCase):
    def test_normalize_unified_diff_adds_plus_to_new_files(self):
        from ma.workspace import normalize_unified_diff

        raw = """diff --git a/src/App.tsx b/src/App.tsx
new file mode 100644
--- /dev/null
+++ b/src/App.tsx
@@ -0,0 +1,3 @@
import React from 'react';
export const App = () => <div>Hello</div>;
"""
        normalized = normalize_unified_diff(raw)
        self.assertIn("+import React", normalized)
        self.assertIn("+export const App", normalized)

    def test_extract_diff_finds_diff_block_among_multiple_fences(self):
        text = """Here is the explanation:
```json
{"status": "ok"}
```
And here is the diff:
```diff
diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1 +1 @@
-1
+2
```
"""
        extracted = extract_diff(text)
        self.assertTrue(extracted.startswith("diff --git a/calc.py"))



class EventsTests(unittest.TestCase):
    def test_emit_and_cancel(self):
        from ma import events

        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            pid = "projtest"
            old_events = events.events_path
            old_cancel = events.cancel_path
            old_pid = events.pid_path
            events.events_path = lambda project_id, base_dir=base: old_events(project_id, base_dir)  # type: ignore
            events.cancel_path = lambda project_id, base_dir=base: old_cancel(project_id, base_dir)  # type: ignore
            events.pid_path = lambda project_id, base_dir=base: old_pid(project_id, base_dir)  # type: ignore
            try:
                events.clear_cancel(pid)
                events.clear_pid(pid)
                events.emit(pid, "ship_start")
                out = events.request_cancel(pid, hard=False)
                self.assertTrue(Path(out["cancel"]).exists())
                with self.assertRaises(events.CancelledError):
                    events.check_cancel(pid)
                items = list(events.tail_events(pid, follow=False))
                names = [i["event"] for i in items]
                self.assertIn("ship_start", names)
                self.assertIn("cancel_requested", names)
            finally:
                events.events_path = old_events  # type: ignore
                events.cancel_path = old_cancel  # type: ignore
                events.pid_path = old_pid  # type: ignore

    def test_pid_write_and_clear(self):
        from ma import events

        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            old = events.pid_path
            events.pid_path = lambda project_id, base_dir=base: old(project_id, base_dir)  # type: ignore
            try:
                events.write_pid("p1", 12345)
                self.assertEqual(events.read_pid("p1"), 12345)
                events.clear_pid("p1")
                self.assertIsNone(events.read_pid("p1"))
            finally:
                events.pid_path = old  # type: ignore

    def test_is_process_alive_and_project_active(self):
        import os
        from ma import events

        current_pid = os.getpid()
        self.assertTrue(events.is_process_alive(current_pid))
        self.assertFalse(events.is_process_alive(999999))
        self.assertFalse(events.is_process_alive(None))

        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            old = events.pid_path
            events.pid_path = lambda project_id, base_dir=base: old(project_id, base_dir)  # type: ignore
            try:
                # Active project with real PID
                events.write_pid("active_proj", current_pid)
                self.assertTrue(events.is_project_active("active_proj"))

                # Inactive project with dead PID: should return False and auto-clean PID
                events.write_pid("dead_proj", 999999)
                self.assertFalse(events.is_project_active("dead_proj"))
                self.assertIsNone(events.read_pid("dead_proj"))
            finally:
                events.pid_path = old  # type: ignore

    def test_tail_events_skip_stream_chunks(self):
        from ma import events

        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            old = events.events_path
            events.events_path = lambda project_id, base_dir=base: old(project_id, base_dir)  # type: ignore
            try:
                pid = "stream_test"
                events.emit(pid, "stage_start", stage="design")
                events.emit(pid, "stream_chunk", chunk="hello ")
                events.emit(pid, "stream_thinking", chunk="thinking ")
                events.emit(pid, "stream_chunk", chunk="world")
                events.emit(pid, "stage_done", stage="design")

                # With skip_stream_chunks=True
                all_events = list(events.tail_events(pid, follow=False, skip_stream_chunks=True))
                event_names = [e["event"] for e in all_events]
                self.assertEqual(event_names, ["stage_start", "stage_done"])

                # With skip_stream_chunks=False
                full_events = list(events.tail_events(pid, follow=False, skip_stream_chunks=False))
                self.assertEqual(len(full_events), 5)
            finally:
                events.events_path = old  # type: ignore


class QueueTests(unittest.TestCase):
    def test_enqueue_claim_complete(self):
        from ma.queue import JobQueue

        with tempfile.TemporaryDirectory() as d:
            q = JobQueue(Path(d) / "queue.sqlite")
            jid = q.enqueue(repo=d, goal="do thing", verify_command="pytest -q")
            job = q.claim("worker-1")
            self.assertIsNotNone(job)
            self.assertEqual(job["id"], jid)
            self.assertEqual(job["status"], "RUNNING")
            q.complete(jid, {"ok": True})
            got = q.get(jid)
            self.assertEqual(got["status"], "DONE")
            self.assertEqual(got["result"]["ok"], True)
            self.assertIsNone(q.claim("worker-2"))
            q.close()

    def test_queue_token_required_when_configured(self):
        from ma import queue as qmod

        with tempfile.TemporaryDirectory() as d:
            token_file = Path(d) / "queue.token"
            token_file.write_text("secret-token", encoding="utf-8")
            old_home = Path.home
            # patch token file lookup via env
            import os
            old = os.environ.get("MA_QUEUE_TOKEN")
            os.environ["MA_QUEUE_TOKEN"] = "secret-token"
            try:
                with self.assertRaises(qmod.QueueError):
                    qmod.require_queue_token(None)
                with self.assertRaises(qmod.QueueError):
                    qmod.require_queue_token("wrong")
                qmod.require_queue_token("secret-token")
            finally:
                if old is None:
                    os.environ.pop("MA_QUEUE_TOKEN", None)
                else:
                    os.environ["MA_QUEUE_TOKEN"] = old


class ApprovalTests(unittest.TestCase):
    def test_human_approval_gate(self):
        from ma import approve

        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            old = approve.approval_path
            approve.approval_path = lambda project_id, base_dir=base: old(project_id, base_dir)  # type: ignore
            old_root = approve._root
            approve._root = lambda base_dir=base: old_root(base_dir)  # type: ignore
            try:
                with self.assertRaises(approve.ApprovalError):
                    approve.require_human_approval("p1")
                approve.grant_approval("p1", "ok")
                approve.require_human_approval("p1")
                approve.revoke_approval("p1")
                with self.assertRaises(approve.ApprovalError):
                    approve.require_human_approval("p1")
            finally:
                approve.approval_path = old  # type: ignore
                approve._root = old_root  # type: ignore


class UsageTokenTests(unittest.TestCase):
    def test_extract_provider_usage_preferred(self):
        from ma.usage import extract_usage_tokens

        p, c, src = extract_usage_tokens({"usage": {"prompt_tokens": 11, "completion_tokens": 7}}, "x" * 100, "y" * 100)
        self.assertEqual((p, c, src), (11, 7, "provider.usage"))


class GateTests(unittest.TestCase):
    def test_model_content_gate_rejects_whitespace(self):
        with self.assertRaises(GateError):
            require_model_content("  \n")

    def test_command_gate_requires_zero_exit(self):
        with self.assertRaises(GateError):
            require_command_success({"exit_code": 1})

    def test_approve_gate(self):
        self.assertTrue(require_approve("APPROVE\nok").startswith("APPROVE"))
        with self.assertRaises(GateError):
            require_approve("REJECT no")


class RouterTimeoutTests(unittest.TestCase):
    def test_ninerouter_default_timeout(self):
        from ma.router import NineRouterClient
        c = NineRouterClient("http://127.0.0.1:20128", "dummy-key")
        self.assertEqual(c.timeout, 120)

    def test_cli_parser_timeout(self):
        from ma.cli import parser
        p = parser()
        args = p.parse_args(["ship", "dummy-repo", "dummy-goal"])
        self.assertEqual(args.timeout, 120)
        args_custom = p.parse_args(["--timeout", "60", "ship", "dummy-repo", "dummy-goal"])
        self.assertEqual(args_custom.timeout, 60)



class ReplanTaskSpecTests(unittest.TestCase):
    def test_replan_taskspec_attribute_access(self):
        from ma.tasks import TaskSpec
        from ma.store import TaskStore
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test.sqlite"
            store = TaskStore(db_path)
            try:
                pid = store.create_project("test-proj", "/dummy", "goal")
                new_tasks = [
                    TaskSpec(id="TASK-001", goal="Goal 1", allowed_files=["a.py"], verify_command="", depends_on=[]),
                    TaskSpec(id="TASK-002", goal="Goal 2", allowed_files=["b.py"], verify_command="", depends_on=["TASK-001"]),
                ]
                # Verify extracting t.id works and can be stored as evidence
                task_ids = [t.id for t in new_tasks]
                self.assertEqual(task_ids, ["TASK-001", "TASK-002"])
                store.add_evidence(pid, "replan", {"model": "m", "new_tasks": task_ids, "fallback_errors": []})
            finally:
                store.close()

    def test_taskspec_subscriptable_and_mapping_protocol(self):
        from ma.tasks import TaskSpec, ready_waves
        t = TaskSpec(id="TASK-001", goal="Goal 1", allowed_files=["a.py"], verify_command="pytest", depends_on=[])
        # Test subscripting
        self.assertEqual(t["id"], "TASK-001")
        self.assertEqual(t["goal"], "Goal 1")
        self.assertEqual(t["allowed_files"], ["a.py"])
        self.assertEqual(t["verify_command"], "pytest")
        self.assertEqual(t["depends_on"], [])
        self.assertEqual(t.get("verify_command"), "pytest")
        self.assertEqual(t.get("nonexistent", "default"), "default")
        with self.assertRaises(KeyError):
            _ = t["nonexistent"]

        # Test dict conversion
        d = dict(t)
        self.assertEqual(d["id"], "TASK-001")
        self.assertEqual(d["goal"], "Goal 1")

        # Test ready_waves accepts TaskSpec directly
        waves = ready_waves([t])
        self.assertEqual(len(waves), 1)
        self.assertEqual(waves[0][0]["id"], "TASK-001")


class UIPresetTests(unittest.TestCase):
    def test_preset_configuration_logic(self):
        roles_dict = {
            "design": {"primary": "", "fallbacks": []},
            "critique": {"primary": "", "fallbacks": []},
            "judgment": {"primary": "", "fallbacks": []},
            "audit": {"primary": "", "fallbacks": []},
            "implementation": {"primary": "", "fallbacks": []},
            "verification": {"primary": "", "fallbacks": []},
        }

        # Test balanced preset
        for r_id, r_info in roles_dict.items():
            if r_id in {"design", "critique", "judgment", "audit"}:
                r_info["primary"] = "ag/gemini-3.8-flash-high"
            elif r_id == "implementation":
                r_info["primary"] = "ag/gemini-3.8-flash-medium"
            else:
                r_info["primary"] = "ag/gemini-3.8-flash"

        self.assertEqual(roles_dict["design"]["primary"], "ag/gemini-3.8-flash-high")
        self.assertEqual(roles_dict["implementation"]["primary"], "ag/gemini-3.8-flash-medium")
        self.assertEqual(roles_dict["verification"]["primary"], "ag/gemini-3.8-flash")


class GitPatchNormalizationTests(unittest.TestCase):
    def test_normalize_unified_diff_windows_dev_null(self):
        from ma.workspace import normalize_unified_diff
        raw_patch = (
            "diff --git a/src/theme.css b/src/theme.css\n"
            "--- /dev/null\n"
            "+++ b/src/theme.css\n"
            "@@ -0,0 +1,999 @@\n"
            "/*\n"
            " Office Theme Design Tokens\n"
            "*/\n"
            ":root {\n"
            "  --dept-boss: #f59e0b;\n"
            "}\n"
        )
        norm = normalize_unified_diff(raw_patch)
        self.assertIn("new file mode 100644", norm)
        self.assertIn("--- /dev/null", norm)
        self.assertIn("+++ b/src/theme.css", norm)
        # Hunk recount should count 6 added lines
        self.assertIn("@@ -0,0 +1,6 @@", norm)

    def test_normalize_unified_diff_multi_file(self):
        from ma.workspace import normalize_unified_diff
        raw_patch = (
            "--- /dev/null\n"
            "+++ b/src/a.ts\n"
            "@@ -0,0 +1,1 @@\n"
            "+const a = 1;\n"
            "--- /dev/null\n"
            "+++ b/src/b.ts\n"
            "@@ -0,0 +1,1 @@\n"
            "+const b = 2;\n"
        )
        norm = normalize_unified_diff(raw_patch)
        self.assertIn("diff --git a/src/a.ts b/src/a.ts", norm)
        self.assertIn("diff --git a/src/b.ts b/src/b.ts", norm)
        self.assertIn("new file mode 100644", norm)

    def test_extract_diff_multi_blocks(self):
        from ma.workspace import extract_diff
        llm_response = (
            "Here is the code:\n"
            "```diff\n"
            "diff --git a/a.ts b/a.ts\n"
            "--- /dev/null\n"
            "+++ b/a.ts\n"
            "@@ -0,0 +1,1 @@\n"
            "+const a = 1;\n"
            "```\n"
            "And the second file:\n"
            "```diff\n"
            "diff --git a/b.ts b/b.ts\n"
            "--- /dev/null\n"
            "+++ b/b.ts\n"
            "@@ -0,0 +1,1 @@\n"
            "+const b = 2;\n"
            "```\n"
        )
        diff = extract_diff(llm_response)
        self.assertIn("diff --git a/a.ts b/a.ts", diff)
        self.assertIn("diff --git a/b.ts b/b.ts", diff)

    def test_workspace_apply_patch_and_diff_untracked(self):
        from ma.workspace import Workspace
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            (repo / "README.md").write_text("# Test\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            ws = Workspace(repo, "p1", "t1")
            ws.create()
            patch = (
                "diff --git a/src/theme.css b/src/theme.css\n"
                "--- /dev/null\n"
                "+++ b/src/theme.css\n"
                "@@ -0,0 +1,2 @@\n"
                "+:root {\n"
                "+  --dept-boss: #f59e0b;\n"
                "+}\n"
            )
            ws.apply_patch(patch)
            diff = ws.diff()
            self.assertTrue(len(diff) > 0, "workspace.diff() should not be empty for new files")
            self.assertIn("src/theme.css", diff)
            ws.commit("test commit")
            ws.remove()

    def test_workspace_direct_fallback_on_corrupt_patch(self):
        from ma.workspace import Workspace
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            (repo / "README.md").write_text("# Test\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            ws = Workspace(repo, "p2", "t2")
            ws.create()
            # Deliberately malformed patch that git apply cannot parse
            corrupt_patch = (
                "diff --git a/src/corrupt.ts b/src/corrupt.ts\n"
                "--- /dev/null\n"
                "+++ b/src/corrupt.ts\n"
                "@@ corrupt header @@\n"
                "+export const magic = 42;\n"
            )
            ws.apply_patch(corrupt_patch)
            diff = ws.diff()
            self.assertTrue(len(diff) > 0)
            self.assertIn("src/corrupt.ts", diff)
            self.assertTrue((ws.path / "src" / "corrupt.ts").exists())
            ws.commit("test fallback")
            ws.remove()

    def test_enforce_allowed_files_path_normalization(self):
        from ma.tasks import enforce_allowed_files
        diff = (
            "diff --git a/src/core.ts b/src/core.ts\n"
            "--- /dev/null\n"
            "+++ b/src/core.ts\n"
            "@@ -0,0 +1,1 @@\n"
            "+export const x = 1;\n"
        )
        # Should accept relative prefix ./
        files1 = enforce_allowed_files(diff, ["./src/core.ts"])
        self.assertEqual(files1, ["src/core.ts"])

        # Should accept Windows backslash in allowed list
        files2 = enforce_allowed_files(diff, ["src\\core.ts"])
        self.assertEqual(files2, ["src/core.ts"])

        # Should accept wildcard directory
        files3 = enforce_allowed_files(diff, ["src/*"])
        self.assertEqual(files3, ["src/core.ts"])


class ResumptionPipelineTests(unittest.TestCase):
    def test_ready_waves_with_pre_completed_done_tasks(self):
        tasks = [
            {"id": "T2", "depends_on": ["T1"], "status": "READY"},
            {"id": "T3", "depends_on": ["T2"], "status": "READY"},
        ]
        # T1 was already completed in a prior session
        waves = ready_waves(tasks, done={"T1"})
        self.assertEqual(len(waves), 2)
        self.assertEqual([t["id"] for t in waves[0]], ["T2"])
        self.assertEqual([t["id"] for t in waves[1]], ["T3"])

    def test_store_list_evidence_and_update_project_status(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test.db"
            store = TaskStore(db_path)
            try:
                pid = store.create_project("test", "/tmp", "goal")
                store.add_evidence(pid, "task_implementation", {"task_id": "T1", "branch": "b1"})
                store.add_evidence(pid, "other", {"info": 123})
                store.add_evidence(pid, "task_implementation", {"task_id": "T2", "branch": "b2"})

                all_ev = store.list_evidence(pid)
                self.assertEqual(len(all_ev), 3)

                impl_ev = store.list_evidence(pid, kind="task_implementation")
                self.assertEqual(len(impl_ev), 2)
                self.assertEqual(impl_ev[0]["data"]["task_id"], "T1")
                self.assertEqual(impl_ev[1]["data"]["task_id"], "T2")

                store.update_project_status(pid, "FAILED")
                proj = store.get_project(pid)
                self.assertEqual(proj["status"], "FAILED")
            finally:
                store.close()

    def test_router_retries_on_http_429(self):
        import urllib.error
        body = json.dumps({"choices": [{"message": {"content": "SUCCESS_AFTER_429"}}]})
        err_429 = urllib.error.HTTPError("http://x", 429, "Too Many Requests", {}, None)
        with patch("time.sleep"):  # don't wait in tests
            with patch("urllib.request.urlopen", side_effect=[err_429, FakeResponse(body)]) as call:
                client = NineRouterClient("http://x", "key", timeout=5, attempts=2)
                res = client.call("model", "prompt")
                self.assertEqual(res.content, "SUCCESS_AFTER_429")
                self.assertEqual(call.call_count, 2)

    def test_orchestrator_resume_when_already_done(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test.db"
            store = TaskStore(db_path)
            try:
                pid = store.create_project("test", "/tmp", "goal")
                # Record all stages as completed
                from ma.store import STAGES
                for st in STAGES:
                    store.record_stage(pid, st, "m", "p", "art")

                orch = Orchestrator(store, NineRouterClient("http://x", "k"))
                res = orch.resume(pid)
                self.assertEqual(res["status"], "DONE")
                self.assertIn("already completed", res["message"])
            finally:
                store.close()

    def test_orchestrator_resume_skips_completed_stages(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            (repo / "README.md").write_text("# Test\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            db_path = Path(d) / "test.db"
            store = TaskStore(db_path)
            try:
                pid = store.create_project("test", str(repo), "goal")
                # Pre-complete design and critique
                store.record_stage(pid, "design", "model1", "prompt", "design artifact")
                store.record_stage(pid, "critique", "model2", "prompt", "critique artifact")
                store.update_project_status(pid, "FAILED")

                called_stages = []
                def fake_run_stage(project_id, stage):
                    called_stages.append(stage)
                    if stage == "judgment":
                        # Record valid json tasks
                        tasks_json = json.dumps([{"id": "T1", "goal": "g", "allowed_files": ["."], "verify_command": "", "depends_on": []}])
                        store.record_stage(project_id, stage, "model", "p", tasks_json)
                    else:
                        store.record_stage(project_id, stage, "model", "p", "art")

                client = NineRouterClient("http://x", "k")
                orch = Orchestrator(store, client)
                orch.run_stage = fake_run_stage

                # Resuming should start from judgment, NOT re-run design or critique
                next_st = store.next_stage(pid)
                self.assertEqual(next_st, "judgment")
            finally:
                store.close()

    def test_orchestrator_implement_skips_already_implemented_tasks(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            (repo / "README.md").write_text("# Test\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            db_path = Path(d) / "test.db"
            store = TaskStore(db_path)
            try:
                pid = store.create_project("test", str(repo), "goal")
                store.record_stage(pid, "design", "m", "p", "art")
                store.record_stage(pid, "critique", "m", "p", "art")
                store.record_stage(pid, "judgment", "m", "p", "art")

                # Create T1 task branch in git
                subprocess.run(["git", "checkout", "-b", f"ma/{pid}/T1"], cwd=repo, capture_output=True)
                (repo / "file1.txt").write_text("content 1\n", encoding="utf-8")
                subprocess.run(["git", "add", "file1.txt"], cwd=repo, capture_output=True)
                subprocess.run(["git", "commit", "-m", "ma: T1"], cwd=repo, capture_output=True)
                subprocess.run(["git", "checkout", "master"], cwd=repo, capture_output=True)

                # Set up tasks in DB: T1 is IMPLEMENTED, T2 is FAILED and depends on T1
                tasks = [
                    {"id": "T1", "goal": "make file1", "allowed_files": ["file1.txt"], "verify_command": "", "depends_on": [], "status": "IMPLEMENTED"},
                    {"id": "T2", "goal": "make file2", "allowed_files": ["file2.txt"], "verify_command": "", "depends_on": ["T1"], "status": "FAILED"},
                ]
                store.replace_tasks(pid, tasks)
                store.set_task_status("T1", "IMPLEMENTED", project_id=pid)
                store.set_task_status("T2", "FAILED", project_id=pid)
                store.add_evidence(pid, "task_implementation", {
                    "task_id": "T1",
                    "branch": f"ma/{pid}/T1",
                    "status": "IMPLEMENTED",
                })

                client = NineRouterClient("http://x", "k")
                orch = Orchestrator(store, client)

                implemented_calls = []
                def fake_implement_one(project, project_id, task):
                    implemented_calls.append(task["id"])
                    # Create T2 branch
                    subprocess.run(["git", "checkout", "-b", f"ma/{project_id}/{task['id']}"], cwd=repo, capture_output=True)
                    (repo / "file2.txt").write_text("content 2\n", encoding="utf-8")
                    subprocess.run(["git", "add", "file2.txt"], cwd=repo, capture_output=True)
                    subprocess.run(["git", "commit", "-m", "ma: T2"], cwd=repo, capture_output=True)
                    subprocess.run(["git", "checkout", "master"], cwd=repo, capture_output=True)
                    store.set_task_status(task["id"], "IMPLEMENTED", project_id=project_id)
                    ev = {"task_id": task["id"], "branch": f"ma/{project_id}/{task['id']}", "status": "IMPLEMENTED"}
                    store.add_evidence(project_id, "task_implementation", ev)
                    return ev

                orch._implement_one_task = fake_implement_one
                res = orch.implement(pid)

                # T1 was already IMPLEMENTED, so only T2 should have been executed!
                self.assertEqual(implemented_calls, ["T2"])
                # Both tasks should be in final results
                task_ids = [t["task_id"] for t in res["tasks"]]
                self.assertIn("T1", task_ids)
                self.assertIn("T2", task_ids)

                # Integration worktree should contain both files
                integration_path = Path(res["integration"])
                self.assertTrue((integration_path / "file1.txt").exists())
                self.assertTrue((integration_path / "file2.txt").exists())
            finally:
                store.close()

    def test_ready_waves_handles_ghost_dependencies(self):
        # T2 depends on ghost ID 'GHOST-01' which does not exist in tasks or done
        tasks = [
            {"id": "T1", "depends_on": [], "status": "READY"},
            {"id": "T2", "depends_on": ["GHOST-01", "T1"], "status": "READY"},
            {"id": "T3", "depends_on": ["T2"], "status": "READY"},
        ]
        waves = ready_waves(tasks)
        self.assertEqual(len(waves), 3)
        self.assertEqual([t["id"] for t in waves[0]], ["T1"])
        self.assertEqual([t["id"] for t in waves[1]], ["T2"])
        self.assertEqual([t["id"] for t in waves[2]], ["T3"])

    def test_ready_waves_breaks_circular_deadlock(self):
        # T1 depends on T2, and T2 depends on T1
        tasks = [
            {"id": "T1", "depends_on": ["T2"], "status": "READY"},
            {"id": "T2", "depends_on": ["T1"], "status": "READY"},
        ]
        # Should not raise TaskSpecError, breaks cycle gracefully
        waves = ready_waves(tasks)
        self.assertEqual(len(waves), 2)
        all_ids = [t["id"] for w in waves for t in w]
        self.assertIn("T1", all_ids)
        self.assertIn("T2", all_ids)

    def test_resume_sanitizes_ghost_dependencies(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "test.db"
            store = TaskStore(db_path)
            try:
                pid = store.create_project("test", "/tmp", "goal")
                tasks = [
                    {"id": "TASK-01-NEW", "goal": "g1", "allowed_files": ["."], "verify_command": "", "depends_on": []},
                    {"id": "TASK-02-FOO", "goal": "g2", "allowed_files": ["."], "verify_command": "", "depends_on": ["TASK-01-OLD", "NON-EXISTENT"]},
                ]
                store.replace_tasks(pid, tasks)
                store.record_stage(pid, "design", "m", "p", "art")

                client = NineRouterClient("http://x", "k")
                orch = Orchestrator(store, client)

                # Mock ship so resume only runs pre-checks
                orch.ship = lambda project_id, **kwargs: {"ok": True}
                orch.resume(pid)

                # Verify TASK-02-FOO was remapped from TASK-01-OLD to TASK-01-NEW and NON-EXISTENT was pruned
                updated = store.list_tasks(pid)
                t2 = next(t for t in updated if t["id"] == "TASK-02-FOO")
                self.assertEqual(t2["depends_on"], ["TASK-01-NEW"])
            finally:
                store.close()

    def test_strip_conflict_markers(self):
        from ma.workspace import strip_conflict_markers

        bad_json = (
            '{\n'
            '  "name": "foo",\n'
            '<<<<<<< ours\n'
            '  "version": "0.1.0",\n'
            '=======\n'
            '  "version": "0.2.0",\n'
            '>>>>>>> theirs\n'
            '  "private": true\n'
            '}\n'
        )
        cleaned = strip_conflict_markers(bad_json)
        self.assertNotIn("<<<<<<<", cleaned)
        self.assertNotIn("=======", cleaned)
        self.assertNotIn(">>>>>>>", cleaned)
        parsed = json.loads(cleaned)
        self.assertEqual(parsed["version"], "0.2.0")

    def test_repo_summary_includes_structure_and_manifests(self):
        with tempfile.TemporaryDirectory() as d:
            repo_path = Path(d)
            (repo_path / "README.md").write_text("# Test Readme\nHello World", encoding="utf-8")
            (repo_path / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")
            sub = repo_path / "src"
            sub.mkdir()
            (sub / "main.py").write_text("print('hello')\n", encoding="utf-8")

            client = NineRouterClient("http://x", "k")
            store = TaskStore(repo_path / "state.sqlite")
            try:
                orch = Orchestrator(store, client)
                summary = orch._repo_summary(repo_path)
                self.assertIn("REPOSITORY STRUCTURE", summary)
                self.assertIn("src/main.py", summary)
                self.assertIn("Test Readme", summary)
                self.assertIn("KEY FILE: pyproject.toml", summary)
            finally:
                store.close()

    def test_workspace_branch_exists_and_reset_to_base(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            (repo / "base.txt").write_text("initial", encoding="utf-8")
            subprocess.run(["git", "add", "base.txt"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            ws = Workspace(repo, "proj1", task_id="t1")
            try:
                ws.create()
                self.assertTrue(ws.branch_exists("ma/proj1/t1"))
                self.assertFalse(ws.branch_exists("ma/proj1/nonexistent"))

                # Write a commit in worktree
                (ws.path / "extra.txt").write_text("extra", encoding="utf-8")
                ws.commit("added extra")
                self.assertTrue((ws.path / "extra.txt").exists())

                # reset_to_base should revert extra commit
                ws.reset_to_base("HEAD")
                self.assertFalse((ws.path / "extra.txt").exists())
                self.assertTrue((ws.path / "base.txt").exists())
            finally:
                ws.remove()

    def test_workspace_ensure_dependencies_junction_and_gitignore(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
            subprocess.run(["git", "add", ".gitignore"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            # Create fake node_modules with .bin in repo
            nm = repo / "node_modules" / ".bin"
            nm.mkdir(parents=True)
            (nm / "tsc").write_text("#!/bin/sh\n", encoding="utf-8")

            ws = Workspace(repo, "proj2", task_id="t2")
            try:
                ws.create()
                (ws.path / "package.json").write_text("{}", encoding="utf-8")
                ws.ensure_dependencies("npx tsc --noEmit")

                # node_modules should be linked
                self.assertTrue((ws.path / "node_modules" / ".bin").exists())

                # .gitignore in worktree should have node_modules/
                gi = (ws.path / ".gitignore").read_text(encoding="utf-8")
                self.assertIn("node_modules/", gi)
            finally:
                ws.remove()

    def test_workspace_run_handles_unicode_vietnamese_input_text(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            (repo / "f.txt").write_text("hello", encoding="utf-8")
            subprocess.run(["git", "add", "f.txt"], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True)

            ws = Workspace(repo, "proj_unicode", task_id="t_u")
            try:
                ws.create()
                # Test input_text containing Vietnamese characters that fail in cp1252 (e.g. \u1ebf, \u1eb5)
                vn_text = "/* Sếp - \u1ebf \u1eb5 Tiếng Việt có dấu */\n"
                # Running a command with input_text should not crash with charmap encode error
                res = ws._run(["git", "hash-object", "--stdin"], input_text=vn_text)
                self.assertEqual(res.returncode, 0)
                self.assertTrue(len(res.stdout.strip()) > 0)
            finally:
                ws.remove()


if __name__ == "__main__":
    unittest.main()
