from __future__ import annotations

import json
import re
from dataclasses import dataclass


class TaskSpecError(ValueError):
    pass


@dataclass(frozen=True)
class TaskSpec:
    id: str
    goal: str
    allowed_files: list[str]
    verify_command: str
    depends_on: list[str]

    def __getitem__(self, item: str):
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)

    def get(self, item: str, default=None):
        return getattr(self, item, default)

    def keys(self):
        return ("id", "goal", "allowed_files", "verify_command", "depends_on")

    def __iter__(self):
        return iter(self.keys())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "allowed_files": list(self.allowed_files),
            "verify_command": self.verify_command,
            "depends_on": list(self.depends_on),
        }


def _clean_and_parse_json(candidate: str):
    """Attempt parsing JSON with various LLM cleanup strategies."""
    if not candidate:
        return None

    def try_decode(s: str):
        try:
            return json.loads(s, strict=False)
        except Exception:
            return None

    # 1. Direct try
    res = try_decode(candidate)
    if res is not None:
        return res

    # 2. Fix trailing commas before ] or }
    no_trailing = re.sub(r",\s*([\]}])", r"\1", candidate)
    res = try_decode(no_trailing)
    if res is not None:
        return res

    # 3. Strip single-line comments // ... or # ...
    lines = []
    for line in candidate.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("//") or trimmed.startswith("#"):
            continue
        lines.append(line)
    without_comments = "\n".join(lines)
    res = try_decode(without_comments)
    if res is not None:
        return res

    return try_decode(re.sub(r",\s*([\]}])", r"\1", without_comments))


def _build_tasks(data: any) -> list[TaskSpec] | None:
    if isinstance(data, dict):
        for k in ("tasks", "task_dag", "dag", "plan", "items", "task_list"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
    if not isinstance(data, list) or not data:
        return None

    tasks = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            return None
        tid = str(item.get("id", "")).strip()
        goal = str(item.get("goal", "")).strip()
        allowed_raw = item.get("allowed_files")
        if isinstance(allowed_raw, str):
            allowed = [allowed_raw.strip()]
        elif isinstance(allowed_raw, list):
            allowed = [str(x).strip() for x in allowed_raw if str(x).strip()]
        else:
            allowed = []
        verify = str(item.get("verify_command", "")).strip()
        deps_raw = item.get("depends_on")
        if isinstance(deps_raw, str):
            depends = [deps_raw.strip()]
        elif isinstance(deps_raw, list):
            depends = [str(x).strip() for x in deps_raw if str(x).strip()]
        else:
            depends = []

        if not tid or not goal or not verify or not allowed:
            return None
        if tid in seen:
            return None
        seen.add(tid)
        tasks.append(TaskSpec(tid, goal, allowed, verify, depends))

    if not tasks:
        return None

    # Sanitize dependencies (drop missing or self dependencies to avoid crashing on minor LLM hallucinations)
    ids = {t.id for t in tasks}
    sanitized_tasks = []
    for t in tasks:
        valid_deps = [dep for dep in t.depends_on if dep in ids and dep != t.id]
        sanitized_tasks.append(TaskSpec(t.id, t.goal, t.allowed_files, t.verify_command, valid_deps))
    tasks = sanitized_tasks

    # Verify acyclicity
    visiting, done = set(), set()

    def visit(tid: str):
        if tid in done:
            return
        if tid in visiting:
            raise ValueError(f"cycle detected at {tid}")
        visiting.add(tid)
        task = next(x for x in tasks if x.id == tid)
        for dep in task.depends_on:
            visit(dep)
        visiting.remove(tid)
        done.add(tid)

    try:
        for t in tasks:
            visit(t.id)
    except Exception:
        return None

    return tasks


def parse_task_dag(text: str) -> list[TaskSpec]:
    """Extract a JSON task array from model output. Supports multi-block markdown and robust extraction."""
    candidates = []

    # 1. Blocks explicitly tagged ```json ... ``` (search backwards since task DAG is typically at the end)
    for m in reversed(list(re.finditer(r"```json\s*([\s\S]*?)```", text, re.I))):
        candidates.append(m.group(1).strip())

    # 2. Any code blocks ``` ... ```
    for m in reversed(list(re.finditer(r"```(?:\w+)?\s*([\s\S]*?)```", text))):
        c = m.group(1).strip()
        if c not in candidates:
            candidates.append(c)

    # 3. Last balanced [ ... ] in text
    last_open = text.rfind("[")
    last_close = text.rfind("]")
    if last_open >= 0 and last_close > last_open:
        bracket_span = text[last_open : last_close + 1].strip()
        if bracket_span not in candidates:
            candidates.append(bracket_span)

    # 4. Entire text between first [ and last ]
    first_open = text.find("[")
    if first_open >= 0 and last_close > first_open:
        span = text[first_open : last_close + 1].strip()
        if span not in candidates:
            candidates.append(span)

    # 5. Whole text as fallback
    if text.strip() not in candidates:
        candidates.append(text.strip())

    last_err = None
    for c in candidates:
        sub_candidates = []
        s_arr = c.find("[")
        e_arr = c.rfind("]")
        if s_arr >= 0 and e_arr > s_arr:
            sub_candidates.append(c[s_arr : e_arr + 1])
        s_obj = c.find("{")
        e_obj = c.rfind("}")
        if s_obj >= 0 and e_obj > s_obj:
            sub_candidates.append(c[s_obj : e_obj + 1])
        sub_candidates.append(c)

        for sub in sub_candidates:
            data = _clean_and_parse_json(sub)
            if data is not None:
                tasks = _build_tasks(data)
                if tasks:
                    return tasks
                last_err = "candidate parsed but failed task schema or cycle check"
            else:
                last_err = "candidate failed JSON decode"

    raise TaskSpecError(f"no valid task JSON array found in model output: {last_err}")


def changed_files_from_diff(diff: str) -> list[str]:
    files = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3][2:] if parts[3].startswith("b/") else parts[3]
                if path not in files:
                    files.append(path)
        elif line.startswith("+++ "):
            target = line[4:].strip()
            if target != "/dev/null":
                path = target[2:] if target.startswith("b/") else target
                if path not in files:
                    files.append(path)
    return files


def enforce_allowed_files(diff: str, allowed: list[str]) -> list[str]:
    changed = changed_files_from_diff(diff)
    if not changed:
        raise TaskSpecError("diff touches no files")

    def _norm(p: str) -> str:
        s = p.replace("\\", "/").strip()
        if s.startswith("./"):
            s = s[2:]
        return s

    allowed_norms = {_norm(a) for a in allowed}
    illegal = []
    for f in changed:
        fn = _norm(f)
        matched = False
        if fn in allowed_norms:
            matched = True
        else:
            for a in allowed_norms:
                if a == "." or a == "*":
                    matched = True
                    break
                if a.endswith("/*") and fn.startswith(a[:-2] + "/"):
                    matched = True
                    break
                if a.endswith("/") and fn.startswith(a):
                    matched = True
                    break
        if not matched:
            illegal.append(f)

    if illegal:
        raise TaskSpecError(f"patch touches files outside allowed set: {illegal}")
    return changed


def files_overlap(a: list[str], b: list[str]) -> bool:
    return bool(set(a) & set(b))


def ready_waves(tasks: list[dict]) -> list[list[dict]]:
    """
    Partition tasks into execution waves.
    A task is ready when all depends_on are done in previous waves.
    Within a wave, tasks must not share allowed_files (file lock).
    """
    remaining = {t["id"]: dict(t) for t in tasks}
    done: set[str] = set()
    waves: list[list[dict]] = []
    while remaining:
        candidates = [
            t
            for t in remaining.values()
            if all(dep in done for dep in t.get("depends_on", []))
        ]
        if not candidates:
            raise TaskSpecError(f"deadlock in task DAG; remaining={list(remaining)}")
        # stable order by id
        candidates.sort(key=lambda x: x["id"])
        wave: list[dict] = []
        locked: set[str] = set()
        deferred = []
        for t in candidates:
            files = set(t.get("allowed_files") or [])
            if files & locked:
                deferred.append(t)
                continue
            wave.append(t)
            locked |= files
        if not wave:
            # all candidates conflict; run first alone
            wave = [candidates[0]]
        for t in wave:
            remaining.pop(t["id"])
            done.add(t["id"])
        waves.append(wave)
    return waves
