from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .approve import ApprovalError, require_human_approval
from .config import get_fallbacks_mapping, get_models_mapping, get_systems_mapping
from .defaults import DEFAULT_FALLBACKS, DEFAULT_MODELS, SYSTEMS
from .events import CancelledError, check_cancel, clear_cancel, clear_pid, emit, write_pid
from .gates import require_approve, require_command_success, require_model_content
from .locks import Budget, BudgetExceeded, FileLockManager
from .notify import notify_failure
from .ops import doctor, workers_for_wave
from .report import export_markdown_report
from .router import NineRouterClient, RouterError
from .secrets import SecretScanError, require_no_secrets
from .store import TaskStore
from .tasks import enforce_allowed_files, parse_task_dag, ready_waves
from .usage import UsageLedger
from .workspace import Workspace, extract_diff


@dataclass
class RunResult:
    project_id: str
    status: str
    next_stage: str | None


def load_9router_key() -> str:
    db_path = Path(os.path.expandvars(r"%APPDATA%\9router\db\data.sqlite"))
    db = sqlite3.connect(db_path)
    try:
        row = db.execute("SELECT key FROM apiKeys WHERE isActive IN (1,'1') LIMIT 1").fetchone()
    finally:
        db.close()
    if not row:
        raise RuntimeError("no active 9Router ingress API key")
    return row[0]


class Orchestrator:
    def __init__(
        self,
        store: TaskStore,
        client: NineRouterClient,
        models: dict | None = None,
        fallbacks: dict | None = None,
        max_workers: int = 2,
        budget: Budget | None = None,
        max_replans: int = 1,
        usage: UsageLedger | None = None,
    ):
        self.store = store
        self.client = client
        configured_models = get_models_mapping()
        configured_fallbacks = get_fallbacks_mapping()
        self.systems = SYSTEMS | get_systems_mapping()
        self.models = DEFAULT_MODELS | configured_models | (models or {})
        self.fallbacks = DEFAULT_FALLBACKS | configured_fallbacks | (fallbacks or {})
        self.max_workers = max_workers
        self.budget = budget
        if budget is not None and getattr(client, "budget", None) is None:
            client.budget = budget
        self.max_replans = max_replans
        self.usage = usage or UsageLedger()
        self.file_locks = FileLockManager()
        self._replan_count = 0
        self._project_for_usage: str | None = None
        self._dead_models: set[str] = set()

    def _build_stream_handlers(
        self,
        project_id: str | None,
        *,
        stage: str,
        model: str,
        task_id: str | None = None,
    ):
        if not project_id:
            return None, None, lambda: None

        buffer: list[str] = []
        last_flush = time.monotonic()
        first_token = True
        start_time = time.monotonic()
        reasoning_buffer: list[str] = []
        last_reasoning_flush = time.monotonic()

        def on_chunk(text: str):
            nonlocal first_token, last_flush
            now = time.monotonic()
            if first_token:
                first_token = False
                ttft_ms = int((now - start_time) * 1000)
                emit(
                    project_id,
                    "stream_ttft",
                    stage=stage,
                    model=model,
                    task_id=task_id,
                    ttft_ms=ttft_ms,
                )
            buffer.append(text)
            if now - last_flush >= 0.2 or sum(len(c) for c in buffer) >= 150:
                chunk_str = "".join(buffer)
                buffer.clear()
                last_flush = now
                emit(
                    project_id,
                    "stream_chunk",
                    stage=stage,
                    model=model,
                    task_id=task_id,
                    chunk=chunk_str,
                )

        def on_thinking(reasoning: str):
            nonlocal last_reasoning_flush
            now = time.monotonic()
            reasoning_buffer.append(reasoning)
            if now - last_reasoning_flush >= 0.25 or sum(len(c) for c in reasoning_buffer) >= 200:
                chunk_str = "".join(reasoning_buffer)
                reasoning_buffer.clear()
                last_reasoning_flush = now
                emit(
                    project_id,
                    "stream_thinking",
                    stage=stage,
                    model=model,
                    task_id=task_id,
                    chunk=chunk_str,
                )

        def flush():
            if reasoning_buffer:
                chunk_str = "".join(reasoning_buffer)
                reasoning_buffer.clear()
                emit(
                    project_id,
                    "stream_thinking",
                    stage=stage,
                    model=model,
                    task_id=task_id,
                    chunk=chunk_str,
                )
            if buffer:
                chunk_str = "".join(buffer)
                buffer.clear()
                emit(
                    project_id,
                    "stream_chunk",
                    stage=stage,
                    model=model,
                    task_id=task_id,
                    chunk=chunk_str,
                )

        return on_chunk, on_thinking, flush

    def _call_role(self, role: str, prompt: str, system: str, *, project_id: str | None = None):
        chain = self.fallbacks.get(role) or [self.models[role]]
        seen, models = set(), []
        for m in chain:
            if m in self._dead_models:
                continue
            if m not in seen:
                seen.add(m)
                models.append(m)
        if not models:
            raise RouterError(f"{role}: no live models left (blacklist={sorted(self._dead_models)})")
        errors = []
        for model in models:
            try:
                on_chunk, on_thinking, flush_stream = self._build_stream_handlers(
                    project_id, stage=role, model=model
                )
                try:
                    result = self.client.call(
                        model,
                        prompt,
                        system=system,
                        stream=True,
                        on_chunk=on_chunk,
                        on_thinking=on_thinking,
                    )
                except TypeError as exc:
                    if "stream" in str(exc) or "on_chunk" in str(exc):
                        result = self.client.call(model, prompt, system=system)
                    else:
                        raise
                flush_stream()
                if self.usage is not None:
                    self.usage.record(
                        model=model,
                        prompt=system + "\n" + prompt,
                        content=result.content,
                        project_id=project_id or self._project_for_usage,
                        meta={"role": role, "raw": result.raw, "latency_ms": result.latency_ms},
                    )
                return result, model, errors
            except RouterError as exc:
                errors.append(str(exc))
                # blacklist model for rest of this orchestrator instance
                self._dead_models.add(model)
        raise RouterError(f"{role}: all models failed: " + " | ".join(errors))

    def run(self, project_id: str, *, until: str | None = None) -> RunResult:
        while (stage := self.store.next_stage(project_id)) is not None:
            if stage in {"implementation", "verification"}:
                break
            self.run_stage(project_id, stage)
            if stage == until:
                break
        project = self.store.get_project(project_id)
        return RunResult(project_id, project["status"], self.store.next_stage(project_id))

    def run_stage(self, project_id: str, stage: str):
        check_cancel(project_id)
        emit(project_id, "stage_start", stage=stage)
        project = self.store.get_project(project_id)
        context = self._context(project_id, stage)
        repo_summary = self._repo_summary(project["repo"])
        prompt = (
            f"PROJECT GOAL:\n{project['goal']}\n\n"
            f"REPOSITORY:\n{project['repo']}\n\n"
            f"{repo_summary}\n\n"
            f"CONTEXT:\n{context}"
        )
        result, model, fallback_errors = self._call_role(
            stage, prompt, self.systems.get(stage, SYSTEMS.get(stage, "")), project_id=project_id
        )
        artifact = require_model_content(result.content)
        self.store.record_stage(project_id, stage, model, prompt, artifact)
        self.store.add_evidence(
            project_id,
            "model_call",
            {
                "stage": stage,
                "model": model,
                "latency_ms": result.latency_ms,
                "content_chars": len(artifact),
                "fallback_errors": fallback_errors,
            },
        )
        emit(project_id, "stage_done", stage=stage, model=model, chars=len(artifact))
        if stage == "judgment":
            try:
                tasks = parse_task_dag(artifact)
                self.store.replace_tasks(
                    project_id,
                    [
                        {
                            "id": t.id,
                            "goal": t.goal,
                            "allowed_files": t.allowed_files,
                            "verify_command": t.verify_command,
                            "depends_on": t.depends_on,
                            "status": "READY",
                        }
                        for t in tasks
                    ],
                )
                self.store.add_evidence(project_id, "task_dag", {"count": len(tasks), "ids": [t.id for t in tasks]})
                emit(project_id, "task_dag", count=len(tasks), ids=[t.id for t in tasks])
            except Exception as exc:
                self.store.add_evidence(project_id, "task_dag_parse_error", {"error": str(exc)})
                emit(project_id, "task_dag_parse_error", error=str(exc))
                raise RuntimeError(f"Tech Lead Judge failed to produce a valid Task DAG: {exc}") from exc

    def verify(self, project_id: str, command: str):
        project = self.store.get_project(project_id)
        # Prefer integration worktree if present, else first task worktree, else repo
        integration = Workspace(project["repo"], project_id, task_id="integration")
        cwd = project["repo"]
        if integration.path.exists():
            cwd = integration.path
        else:
            tasks = self.store.list_tasks(project_id)
            for t in tasks:
                ws = Workspace(project["repo"], project_id, task_id=t["id"])
                if ws.path.exists():
                    cwd = ws.path
                    break
            else:
                legacy = Workspace(project["repo"], project_id)
                if legacy.path.exists():
                    cwd = legacy.path
        proc = subprocess.run(command, cwd=cwd, shell=True, text=True, capture_output=True)
        evidence = {
            "command": command,
            "cwd": str(cwd),
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-20000:],
            "stderr": proc.stderr[-20000:],
        }
        self.store.add_evidence(project_id, "command", evidence)
        require_command_success(evidence)
        self.store.record_stage(
            project_id,
            "verification",
            "machine",
            command,
            json.dumps(evidence, ensure_ascii=False),
        )
        return evidence

    def _implement_one_task(self, project: dict, project_id: str, task: dict, base: str = "HEAD") -> dict:
        task_id = task["id"]
        allowed = task["allowed_files"]
        self.file_locks.acquire(task_id, allowed, project_id=project_id)
        try:
            self.store.set_task_status(task_id, "IMPLEMENTING", project_id=project_id)
            workspace = Workspace(project["repo"], project_id, task_id=task_id)
            workspace.create(base=base)
            snapshot = workspace.snapshot()
            allowed_clause = f"ALLOWED FILES ONLY: {allowed}\n"
            prompt = (
                f"PROJECT GOAL:\n{project['goal']}\n\nTASK ID: {task_id}\nTASK GOAL:\n{task['goal']}\n\n"
                f"APPROVED PLAN:\n{self.store.get_artifact(project_id, 'judgment')}\n\n"
                f"REPOSITORY SNAPSHOT:\n{snapshot}\n\n{allowed_clause}"
                "Return only a unified git diff. Keep changes minimal."
            )
            chain = self.fallbacks.get("implementation") or [self.models["implementation"]]
            seen, models = set(), []
            for m in chain:
                if m in self._dead_models:
                    continue
                if m not in seen:
                    seen.add(m)
                    models.append(m)
            if not models:
                raise RouterError(f"task {task_id}: no live implement models left")
            last_errors: list[str] = []
            chosen = None
            for model in models:
                for attempt_prompt, tag in (
                    (prompt, "full"),
                    (
                        (
                            f"Return ONLY a unified git diff. Output must start with 'diff --git'.\n"
                            f"Task: {task['goal']}\n{allowed_clause}"
                            f"Repo snapshot:\n{snapshot}\n"
                        ),
                        "tight",
                    ),
                ):
                    try:
                        impl_sys = self.systems.get("implementation", SYSTEMS["implementation"])
                        on_chunk, on_thinking, flush_stream = self._build_stream_handlers(
                            project_id, stage="implementation", model=model, task_id=task_id
                        )
                        try:
                            result = self.client.call(
                                model,
                                attempt_prompt,
                                system=impl_sys,
                                stream=True,
                                on_chunk=on_chunk,
                                on_thinking=on_thinking,
                            )
                        except TypeError as exc:
                            if "stream" in str(exc) or "on_chunk" in str(exc):
                                result = self.client.call(model, attempt_prompt, system=impl_sys)
                            else:
                                raise
                        flush_stream()
                        if self.usage is not None:
                            self.usage.record(
                                model=model,
                                prompt=impl_sys + "\n" + attempt_prompt,
                                content=result.content,
                                project_id=project_id,
                                meta={"role": "implementation", "task_id": task_id, "raw": result.raw},
                            )
                        patch = extract_diff(require_model_content(result.content))
                        require_no_secrets(patch)
                        workspace.apply_patch(patch)
                        diff = workspace.diff()
                        if not diff.strip():
                            raise RuntimeError("worker patch produced no repository diff")
                        require_no_secrets(diff)
                        if allowed and allowed != ["."]:
                            enforce_allowed_files(diff, allowed)
                        chosen = (model, result, diff, tag, last_errors)
                        break
                    except RouterError as exc:
                        last_errors.append(f"{model}/{tag}: {exc}")
                        self._dead_models.add(model)
                        workspace.reset_hard()
                        break  # don't tight-retry same dead model
                    except Exception as exc:
                        last_errors.append(f"{model}/{tag}: {exc}")
                        workspace.reset_hard()
                if chosen:
                    break
            if not chosen:
                self.store.set_task_status(task_id, "FAILED", project_id=project_id)
                raise RouterError(f"task {task_id}: all models failed: " + " | ".join(last_errors))
            model, result, diff, tag, fallback_errors = chosen
            workspace.commit(f"ma: {task_id}")
            # per-task verify if present
            verify_cmd = task.get("verify_command")
            verify_evidence = None
            if verify_cmd:
                proc = subprocess.run(verify_cmd, cwd=workspace.path, shell=True, text=True, capture_output=True)
                verify_evidence = {
                    "command": verify_cmd,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[-10000:],
                    "stderr": proc.stderr[-10000:],
                }
                if proc.returncode != 0:
                    self.store.set_task_status(task_id, "VERIFY_FAILED", project_id=project_id)
                    raise RuntimeError(f"task {task_id} verify failed: {verify_evidence}")
            self.store.set_task_status(task_id, "IMPLEMENTED", project_id=project_id)
            evidence = {
                "task_id": task_id,
                "model": model,
                "latency_ms": result.latency_ms,
                "worktree": str(workspace.path),
                "branch": workspace.branch,
                "diff_chars": len(diff),
                "allowed_files": allowed,
                "prompt_mode": tag,
                "fallback_errors": fallback_errors,
                "verify": verify_evidence,
            }
            self.store.add_evidence(project_id, "task_implementation", evidence)
            return evidence
        finally:
            self.file_locks.release(task_id, allowed)

    def implement(self, project_id: str, *, allowed_files: list[str] | None = None) -> dict:
        if self.store.next_stage(project_id) != "implementation":
            raise ValueError(f"implementation not ready; next stage is {self.store.next_stage(project_id)}")
        project = self.store.get_project(project_id)
        tasks = self.store.list_tasks(project_id)
        if not tasks:
            # Check if judgment stage was recorded and parse DAG from artifact
            judgment_art = self.store.get_artifact(project_id, "judgment")
            if judgment_art:
                try:
                    parsed = parse_task_dag(judgment_art)
                    tasks = [
                        {
                            "id": t.id,
                            "goal": t.goal,
                            "allowed_files": t.allowed_files,
                            "verify_command": t.verify_command,
                            "depends_on": t.depends_on,
                            "status": "READY",
                        }
                        for t in parsed
                    ]
                    self.store.replace_tasks(project_id, tasks)
                except Exception as exc:
                    raise RuntimeError(f"Judgment stage artifact failed to produce valid Task DAG: {exc}") from exc
            else:
                # fallback single synthetic task over whole repo goal (e.g. direct implement tests)
                tasks = [
                    {
                        "id": "T1",
                        "goal": project["goal"],
                        "allowed_files": allowed_files or ["."],
                        "verify_command": "python -m unittest discover -s . -v",
                        "depends_on": [],
                        "status": "READY",
                    }
                ]
                self.store.replace_tasks(project_id, tasks)

        # Resumption support: reset any failed tasks to READY
        for t in tasks:
            if t.get("status") in {"FAILED", "VERIFY_FAILED"}:
                t["status"] = "READY"
                self.store.set_task_status(t["id"], "READY", project_id=project_id)

        # Identify tasks already completed in previous runs
        already_done_tasks = [t for t in tasks if t.get("status") == "IMPLEMENTED"]
        already_done_ids = {t["id"] for t in already_done_tasks}

        all_results: list[dict] = []
        if already_done_ids:
            past_evidence = self.store.list_evidence(project_id, kind="task_implementation")
            ev_map = {}
            for ev in past_evidence:
                data = ev.get("data", {})
                tid = data.get("task_id")
                if tid in already_done_ids:
                    ev_map[tid] = data

            for t in already_done_tasks:
                tid = t["id"]
                if tid in ev_map:
                    all_results.append(ev_map[tid])
                else:
                    all_results.append({
                        "task_id": tid,
                        "branch": f"ma/{project_id}/{tid}",
                        "status": "IMPLEMENTED",
                    })

        pending = [t for t in tasks if t["id"] not in already_done_ids]
        done_ids = set(already_done_ids)
        wave_idx = 0
        while pending:
            waves = ready_waves(pending, done=done_ids)
            wave = waves[0]
            self.store.add_evidence(project_id, "wave_start", {"wave": wave_idx, "tasks": [t["id"] for t in wave]})
            wave_results = []
            failures = []
            n_workers = workers_for_wave(len(wave), self.max_workers)
            self.store.add_evidence(project_id, "wave_workers", {"wave": wave_idx, "workers": n_workers, "wave_size": len(wave)})
            if len(wave) == 1 or n_workers <= 1:
                for t in wave:
                    try:
                        wave_results.append(self._implement_one_task(project, project_id, t))
                    except Exception as exc:
                        failures.append((t, exc))
            else:
                with ThreadPoolExecutor(max_workers=n_workers) as pool:
                    futures = {pool.submit(self._implement_one_task, project, project_id, t): t for t in wave}
                    for fut in as_completed(futures):
                        t = futures[fut]
                        try:
                            wave_results.append(fut.result())
                        except Exception as exc:
                            failures.append((t, exc))
            all_results.extend(wave_results)
            self.store.add_evidence(
                project_id,
                "wave_done",
                {"wave": wave_idx, "ok": [r["task_id"] for r in wave_results], "failed": [t["id"] for t, _ in failures]},
            )
            if failures:
                if self._replan_count >= self.max_replans:
                    raise RuntimeError(
                        f"task failures and replan budget exhausted: "
                        + "; ".join(f"{t['id']}: {e}" for t, e in failures)
                    )
                self._replan_count += 1
                fail_report = "\n".join(f"- {t['id']}: {e}" for t, e in failures)
                self.store.add_evidence(project_id, "replan_start", {"wave": wave_idx, "failures": fail_report})
                replan_prompt = (
                    f"PROJECT GOAL:\n{project['goal']}\n\n"
                    f"CURRENT PLAN:\n{self.store.get_artifact(project_id, 'judgment')}\n\n"
                    f"FAILED TASKS:\n{fail_report}\n\n"
                    "Replan ONLY the failed work as a JSON task array with id/goal/allowed_files/verify_command/depends_on. "
                    "Do not redo successful tasks. Keep allowed_files minimal."
                )
                judge_sys = self.systems.get("judgment", SYSTEMS["judgment"])
                result, model, errors = self._call_role("judgment", replan_prompt, judge_sys, project_id=project_id)
                new_tasks = parse_task_dag(require_model_content(result.content))
                # keep successful tasks; replace remaining with replan + not-yet-run
                done_ids = {r["task_id"] for r in all_results}
                failed_ids = {t["id"] for t, _ in failures}
                wave_ids = {x["id"] for x in wave}
                new_ids = {t.id for t in new_tasks}

                # leftovers: tasks not yet run, excluding wave, failed, and anything redefined in new_tasks
                leftovers = [
                    t for t in pending
                    if t["id"] not in done_ids
                    and t["id"] not in failed_ids
                    and t["id"] not in wave_ids
                    and t["id"] not in new_ids
                ]

                # Remap dependencies from failed tasks to their replanned replacements
                failed_to_new = {fid: list(new_ids) for fid in failed_ids}
                for t in leftovers:
                    new_deps = []
                    for dep in t.get("depends_on", []):
                        if dep in failed_to_new:
                            new_deps.extend(failed_to_new[dep])
                        else:
                            new_deps.append(dep)
                    t["depends_on"] = list(dict.fromkeys(new_deps))

                # wave tasks that failed are dropped in favor of replan
                pending = leftovers + [
                    {
                        "id": t.id,
                        "goal": t.goal,
                        "allowed_files": t.allowed_files,
                        "verify_command": t.verify_command,
                        "depends_on": t.depends_on,
                        "status": "READY",
                    }
                    for t in new_tasks
                ]

                existing = self.store.list_tasks(project_id)
                kept = [t for t in existing if t["id"] in done_ids and t["id"] not in new_ids]
                for t in kept:
                    t["status"] = "IMPLEMENTED"

                combined = []
                seen_combined = set()
                for t in kept + pending:
                    if t["id"] not in seen_combined:
                        seen_combined.add(t["id"])
                        combined.append(t)

                self.store.replace_tasks(project_id, combined)
                self.store.add_evidence(
                    project_id,
                    "replan",
                    {"model": model, "new_tasks": [t.id for t in new_tasks], "fallback_errors": errors},
                )
                wave_idx += 1
                continue
            # remove completed wave from pending
            done_wave = {t["id"] for t in wave}
            done_ids.update(done_wave)
            pending = [t for t in pending if t["id"] not in done_wave]
            wave_idx += 1

        # Integration worktree: merge all successful task branches
        integration = Workspace(project["repo"], project_id, task_id="integration")
        if integration.path.exists():
            integration.remove()
        integration.create(base="HEAD")
        merged_branches = []
        for r in all_results:
            branch = r.get("branch") or f"ma/{project_id}/{r['task_id']}"
            integration.merge_branch(branch)
            merged_branches.append(branch)
        integration.commit(f"ma: integrate {project_id}")
        combined_diff = integration.diff()
        if not combined_diff.strip():
            combined_diff = "\n".join(f"merged {b}" for b in merged_branches) + "\n"

        artifact = json.dumps(
            {
                "integration_worktree": str(integration.path),
                "integration_branch": integration.branch,
                "task_results": all_results,
                "merged_branches": merged_branches,
                "replans": self._replan_count,
                "budget": self.budget.snapshot() if self.budget else None,
            },
            ensure_ascii=False,
            indent=2,
        )
        self.store.record_stage(project_id, "implementation", "multi-task", "per-task workers", artifact)
        self.store.add_evidence(
            project_id,
            "implementation",
            {"tasks": len(all_results), "waves": wave_idx, "integration": str(integration.path), "replans": self._replan_count},
        )
        return {"tasks": all_results, "waves": wave_idx, "integration": str(integration.path), "replans": self._replan_count}

    def audit(self, project_id: str):
        if self.store.next_stage(project_id) != "audit":
            raise ValueError(f"audit not ready; next stage is {self.store.next_stage(project_id)}")
        self.run_stage(project_id, "audit")
        artifact = self.store.get_artifact(project_id, "audit")
        return require_approve(artifact)

    def merge(self, project_id: str, *, push: bool = False, remote: str = "origin", require_approval: bool = False) -> dict:
        project = self.store.get_project(project_id)
        audit = self.store.get_artifact(project_id, "audit")
        require_approve(audit)
        if require_approval or push:
            # push always requires explicit human approval gate when enabled by caller
            require_human_approval(project_id)
        integration = Workspace(project["repo"], project_id, task_id="integration")
        if integration.path.exists():
            result = integration.merge_into_repo(f"ma: {project_id} approved")
            ws = integration
        else:
            workspace = Workspace(project["repo"], project_id)
            if not workspace.path.exists():
                raise RuntimeError("worktree missing; cannot merge")
            result = workspace.merge_into_repo(f"ma: {project_id} approved")
            ws = workspace
        if push:
            result["push"] = ws.push(remote=remote)
        self.store.add_evidence(project_id, "merge", result)
        return result

    def ship(
        self,
        project_id: str,
        *,
        verify_command: str | None = None,
        merge: bool = False,
        push: bool = False,
        remote: str = "origin",
        preflight: bool = True,
        require_approval: bool = False,
    ) -> dict:
        self._project_for_usage = project_id
        clear_cancel(project_id)
        write_pid(project_id)
        emit(project_id, "ship_start", merge=merge, push=push)
        try:
            if preflight:
                check_cancel(project_id)
                pre = doctor(probe_models=False)
                core_fail = [c for c in pre["checks"] if c["check"] in {"9router_up", "9router_key", "git"} and not c["ok"]]
                self.store.add_evidence(project_id, "preflight", pre)
                emit(project_id, "preflight", ok=not core_fail)
                if core_fail:
                    raise RuntimeError("preflight failed: " + "; ".join(f"{c['check']}:{c['detail']}" for c in core_fail))
            while True:
                check_cancel(project_id)
                stage = self.store.next_stage(project_id)
                if stage is None:
                    break
                if stage in {"design", "critique", "judgment", "report"}:
                    self.run_stage(project_id, stage)
                    continue
                if stage == "implementation":
                    emit(project_id, "implement_start")
                    self.implement(project_id)
                    emit(project_id, "implement_done")
                    continue
                if stage == "verification":
                    cmd = verify_command
                    if not cmd:
                        tasks = self.store.list_tasks(project_id)
                        cmd = tasks[0]["verify_command"] if tasks else "python -m unittest discover -s . -v"
                    emit(project_id, "verify_start", command=cmd)
                    self.verify(project_id, cmd)
                    emit(project_id, "verify_done", command=cmd)
                    continue
                if stage == "audit":
                    self.audit(project_id)
                    continue
                break
            merge_info = (
                self.merge(project_id, push=push, remote=remote, require_approval=require_approval or push)
                if merge
                else {"merged": False, "reason": "merge not requested"}
            )
            project = self.store.get_project(project_id)
            usage = self.usage.summary(project_id) if self.usage else None
            report_path = export_markdown_report(self.store, project_id, usage)
            result = {
                "project_id": project_id,
                "status": project["status"],
                "next_stage": self.store.next_stage(project_id),
                "tasks": self.store.list_tasks(project_id),
                "merge": merge_info,
                "budget": self.budget.snapshot() if self.budget else None,
                "replans": self._replan_count,
                "usage": usage,
                "dead_models": sorted(self._dead_models),
                "report": str(report_path),
            }
            emit(project_id, "ship_done", status=project["status"], report=str(report_path))
            clear_cancel(project_id)
            clear_pid(project_id)
            return result
        except CancelledError:
            self.store.update_project_status(project_id, "CANCELLED")
            self.store.add_evidence(project_id, "cancelled", {"project_id": project_id})
            clear_pid(project_id)
            raise
        except Exception as exc:
            emit(project_id, "ship_failed", error=str(exc))
            note = notify_failure(f"ma ship FAILED {project_id}: {type(exc).__name__}: {exc}")
            self.store.update_project_status(project_id, "FAILED")
            self.store.add_evidence(project_id, "failure", {"error": str(exc), "notify": note})
            clear_pid(project_id)
            raise

    def resume(
        self,
        project_id: str,
        *,
        verify_command: str | None = None,
        merge: bool = False,
        push: bool = False,
        remote: str = "origin",
        preflight: bool = True,
        require_approval: bool = False,
    ) -> dict:
        """Resume an interrupted or failed project from its last uncompleted stage or task."""
        project = self.store.get_project(project_id)
        clear_cancel(project_id)
        self._dead_models.clear()
        self._replan_count = 0

        # Reset any failed tasks back to READY and sanitize dependencies against ghost IDs
        existing_tasks = self.store.list_tasks(project_id)
        if existing_tasks:
            task_ids = {t["id"] for t in existing_tasks}
            tasks_modified = False
            for t in existing_tasks:
                if t.get("status") in {"FAILED", "VERIFY_FAILED"}:
                    t["status"] = "READY"
                    tasks_modified = True
                remapped = []
                for dep in t.get("depends_on", []):
                    if dep in task_ids:
                        remapped.append(dep)
                    else:
                        parts = dep.split("-")
                        prefix = parts[0] + "-" + parts[1] if len(parts) >= 2 else dep
                        match = next((x for x in task_ids if x.startswith(prefix)), None)
                        if match:
                            remapped.append(match)
                        tasks_modified = True
                new_deps = list(dict.fromkeys(remapped))
                if new_deps != t.get("depends_on", []):
                    t["depends_on"] = new_deps
                    tasks_modified = True
            if tasks_modified:
                self.store.replace_tasks(project_id, existing_tasks)

        next_st = self.store.next_stage(project_id)
        if next_st is None:
            return {
                "project_id": project_id,
                "status": "DONE",
                "message": "Project already completed all stages",
            }

        self.store.update_project_status(project_id, next_st.upper())
        emit(project_id, "project_resumed", stage=next_st)
        return self.ship(
            project_id,
            verify_command=verify_command,
            merge=merge,
            push=push,
            remote=remote,
            preflight=preflight,
            require_approval=require_approval,
        )

    def _repo_summary(self, repo_path: str | Path, max_chars: int = 16000) -> str:
        repo = Path(repo_path).resolve()
        if not repo.exists() or not repo.is_dir():
            return f"REPOSITORY STRUCTURE:\n[Path {repo} does not exist or is not a directory]"

        files: list[str] = []
        if (repo / ".git").exists():
            try:
                proc = subprocess.run(
                    ["git", "ls-files"],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    files = [f.strip() for f in proc.stdout.splitlines() if f.strip()]
            except Exception:
                pass

        if not files:
            ignore_dirs = {
                ".git", "__pycache__", "node_modules", ".venv", "venv",
                "env", "dist", "build", ".idea", ".vscode", ".tox", ".eggs"
            }
            try:
                for root, dirs, filenames in os.walk(repo):
                    dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
                    rel_dir = Path(root).relative_to(repo)
                    for fn in filenames:
                        if fn.startswith(".") or fn.endswith((".pyc", ".pyo")):
                            continue
                        rel_path = (rel_dir / fn).as_posix() if str(rel_dir) != "." else fn
                        files.append(rel_path)
                        if len(files) >= 300:
                            break
                    if len(files) >= 300:
                        break
            except Exception:
                pass

        total_files = len(files)
        display_files = files[:120]
        tree_lines = [f"- {f}" for f in display_files]
        if total_files > len(display_files):
            tree_lines.append(f"... and {total_files - len(display_files)} more files")

        parts = [
            f"### REPOSITORY STRUCTURE ({repo.name}):",
            f"Total files: {total_files}",
            "\n".join(tree_lines),
        ]

        key_files = [
            ("README.md", 3000),
            ("pyproject.toml", 2000),
            ("requirements.txt", 1500),
            ("package.json", 2000),
            ("Cargo.toml", 1500),
            ("go.mod", 1500),
        ]

        current_len = sum(len(p) for p in parts)
        for fname, budget in key_files:
            target = repo / fname
            if target.exists() and target.is_file():
                try:
                    content = target.read_text(encoding="utf-8", errors="replace")
                    snippet = content[:budget].strip()
                    if len(content) > budget:
                        snippet += "\n[... truncated ...]"
                    block = f"\n### KEY FILE: {fname}\n{snippet}"
                    if current_len + len(block) > max_chars:
                        break
                    parts.append(block)
                    current_len += len(block)
                except Exception:
                    pass

        return "\n\n".join(parts)

    def _context(self, project_id: str, stage: str) -> str:
        required = {
            "design": [],
            "critique": ["design"],
            "judgment": ["design", "critique"],
            "audit": ["judgment", "implementation", "verification"],
            "report": ["judgment", "implementation", "verification", "audit"],
        }.get(stage, ["judgment"])
        return "\n\n".join(f"## {name.upper()}\n{self.store.get_artifact(project_id, name)}" for name in required)
