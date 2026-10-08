from __future__ import annotations

import re
import subprocess
from pathlib import Path


class WorkspaceError(RuntimeError):
    pass


def strip_conflict_markers(text: str) -> str:
    """Resolve/clean git conflict markers (<<<<<<<, =======, >>>>>>>) left by LLMs.
    Handles both raw text markers and diff-prefixed markers ('+<<<<<<<', etc.).
    Prefers the incoming/theirs section (between ======= and >>>>>>>).
    """
    if "<<<<<<<" not in text:
        return text
    lines = text.splitlines(keepends=True)
    out = []
    in_ours = False
    in_theirs = False
    for line in lines:
        stripped = line.strip()
        # Extract marker token after optional diff prefix (+, -, ' ')
        token = stripped
        if stripped.startswith(("+", "-", " ")) and len(stripped) > 1 and stripped[1] in "<=>":
            token = stripped[1:].strip()

        if token.startswith("<<<<<<<"):
            in_ours = True
            in_theirs = False
            continue
        elif token.startswith("=======") and in_ours:
            in_ours = False
            in_theirs = True
            continue
        elif token.startswith(">>>>>>>") and (in_ours or in_theirs):
            in_ours = False
            in_theirs = False
            continue

        if in_ours:
            # Skip conflict block from 'ours'
            continue
        out.append(line)
    return "".join(out)


def sanitize_worktree_conflict_markers(worktree_path: Path):
    """Ensure no modified text files in worktree contain unresolved git conflict markers."""
    for ext in (".json", ".js", ".ts", ".tsx", ".py", ".html", ".css", ".yaml", ".yml", ".md", ".toml"):
        for f in worktree_path.glob(f"**/*{ext}"):
            if "node_modules" in f.parts or ".git" in f.parts:
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                if "<<<<<<<" in content:
                    sanitized = strip_conflict_markers(content)
                    f.write_text(sanitized, encoding="utf-8")
            except Exception:
                pass


def _ensure_diff_git_header(diff_body: str) -> str:
    if "diff --git " in diff_body:
        return diff_body
    m = re.search(r"^\+\+\+\s+(?:b/)?([^\s\n]+)", diff_body, re.M)
    if m:
        path = m.group(1).strip()
        if path != "/dev/null":
            header = f"diff --git a/{path} b/{path}\n"
            return header + diff_body
    return diff_body


def extract_diff(text: str) -> str:
    # First: find all code blocks that contain diffs and concatenate them
    diff_blocks = []
    for match in re.finditer(r"```(?:diff|patch)?\s*\n(.*?)```", text, re.S):
        block = match.group(1).strip()
        if "diff --git " in block:
            start = block.find("diff --git ")
            diff_blocks.append(block[start:].strip())
        elif ("\n--- " in block or block.startswith("--- ")) and "\n+++ " in block and "\n@@" in block:
            start = block.find("--- ")
            diff_blocks.append(_ensure_diff_git_header(block[start:].strip()))

    if diff_blocks:
        return "\n".join(diff_blocks).strip() + "\n"

    # Second: find "diff --git " anywhere in the text
    start = text.find("diff --git ")
    if start >= 0:
        candidate = text[start:]
        end_fence = candidate.find("```")
        if end_fence >= 0:
            candidate = candidate[:end_fence]
        return candidate.strip() + "\n"

    # Third: find standard diff (--- and +++ and @@) anywhere in the text
    hunk_idx = text.find("\n@@ ")
    if hunk_idx >= 0:
        pre = text[:hunk_idx]
        diff_start = pre.rfind("\n--- ")
        if diff_start >= 0:
            candidate = text[diff_start + 1:]
            end_fence = candidate.find("```")
            if end_fence >= 0:
                candidate = candidate[:end_fence]
            return _ensure_diff_git_header(candidate.strip() + "\n")

    raise WorkspaceError("worker returned no unified git diff")


def split_patch_into_files(patch: str) -> list[str]:
    """Split a multi-file unified diff into individual file chunks."""
    lines = patch.replace("\r\n", "\n").split("\n")
    chunks = []
    current: list[str] = []
    for line in lines:
        is_boundary = line.startswith("diff --git ") or (
            line.startswith("--- ") and any(l.startswith("+++ ") for l in current)
        )
        if is_boundary and current:
            chunks.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        chunks.append("\n".join(current))
    return chunks


def normalize_file_chunk(chunk: str) -> str:
    """Normalize a single file unified diff hunk:
    - On Windows, ensure 'new file mode 100644' is present before '--- /dev/null'.
    - Recount new file additions so hunk count '@@ -0,0 +1,N @@' is exact.
    - Ensure all added content lines have leading '+'.
    - Add missing 'diff --git a/path b/path' header for modifications.
    """
    lines = [l for l in chunk.split("\n") if l != ""]
    if not lines:
        return ""

    target_path = None
    for l in lines:
        if l.startswith("+++ "):
            target = l[4:].strip()
            if target != "/dev/null":
                target_path = target[2:] if target.startswith("b/") else target
                break
        elif l.startswith("diff --git "):
            parts = l.split()
            if len(parts) >= 4:
                p = parts[3]
                if p != "/dev/null":
                    target_path = p[2:] if p.startswith("b/") else p
                    break

    if not target_path or target_path == "/dev/null":
        return chunk

    is_new = any(l.startswith("--- /dev/null") or "new file mode" in l for l in lines)

    if is_new:
        content_lines = []
        for l in lines:
            if (
                l.startswith("diff --git")
                or l.startswith("new file mode")
                or l.startswith("index ")
                or l.startswith("--- ")
                or l.startswith("+++ ")
                or l.startswith("@@")
            ):
                continue
            if l.startswith("+"):
                content_lines.append(l)
            elif l.startswith(" "):
                content_lines.append("+" + l[1:])
            elif not l.startswith("-") and not l.startswith("\\"):
                content_lines.append("+" + l)

        header = [
            f"diff --git a/{target_path} b/{target_path}",
            "new file mode 100644",
            "--- /dev/null",
            f"+++ b/{target_path}",
            f"@@ -0,0 +1,{len(content_lines)} @@",
        ]
        return "\n".join(header + content_lines) + "\n"
    else:
        out = []
        has_git_header = any(l.startswith("diff --git ") for l in lines)
        if not has_git_header:
            out.append(f"diff --git a/{target_path} b/{target_path}")
        for l in lines:
            if l.startswith("--- ") and not l.startswith("--- /dev/null") and not l.startswith("--- a/"):
                out.append("--- a/" + l[4:].strip())
            elif l.startswith("+++ ") and not l.startswith("+++ /dev/null") and not l.startswith("+++ b/"):
                out.append("+++ b/" + l[4:].strip())
            else:
                out.append(l)
        return "\n".join(out) + "\n"


def normalize_unified_diff(patch: str) -> str:
    """Normalize unified diffs generated by LLMs:
    1. Unify CRLF to LF.
    2. Strip git conflict markers (<<<<<<<, =======, >>>>>>>).
    3. Split multi-file patches into per-file chunks.
    4. Ensure new files include 'new file mode 100644' and accurate hunk line counts.
    5. Ensure every addition line in new files starts with '+'.
    6. Ensure missing 'diff --git a/path b/path' headers are synthesized.
    """
    clean_patch = strip_conflict_markers(patch)
    chunks = split_patch_into_files(clean_patch)
    normalized = [normalize_file_chunk(c) for c in chunks if c.strip()]
    return "\n".join(normalized).strip() + "\n"


def extract_files_from_diff(patch: str) -> dict[str, dict]:
    """Extract individual files and contents from unified diff hunks for fallback application."""
    lines = patch.replace("\r\n", "\n").split("\n")
    files: dict[str, dict] = {}
    current_path = None
    is_new = False
    content: list[str] = []

    def commit_current():
        nonlocal current_path, is_new, content
        if current_path and (is_new or content):
            raw = "\n".join(content).strip() + "\n"
            files[current_path] = {"content": strip_conflict_markers(raw), "is_new": is_new}
        current_path = None
        is_new = False
        content = []

    for line in lines:
        if line.startswith("diff --git "):
            commit_current()
            parts = line.split()
            if len(parts) >= 4:
                p = parts[3]
                current_path = p[2:] if p.startswith("b/") else p
            continue
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target != "/dev/null":
                p = target[2:] if target.startswith("b/") else target
                if current_path != p:
                    current_path = p
            continue
        if line.startswith("--- /dev/null") or "new file mode" in line:
            is_new = True
            continue
        if line.startswith("@@"):
            continue
        if current_path:
            if line.startswith("+"):
                content.append(line[1:])
            elif line.startswith(" "):
                content.append(line[1:])
            elif is_new and not line.startswith("-") and not line.startswith("\\") and not line.startswith("diff ") and not line.startswith("index "):
                if line != "":
                    content.append(line)

    commit_current()
    return files


class Workspace:
    def __init__(self, repo: str | Path, project_id: str, task_id: str | None = None):
        self.repo = Path(repo).resolve()
        self.project_id = project_id
        self.task_id = task_id
        suffix = f"{project_id}-{task_id}" if task_id else project_id
        self.path = self.repo.parent / f"{self.repo.name}-ma-{suffix}"
        self.branch = f"ma/{project_id}/{task_id}" if task_id else f"ma/{project_id}"

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(args, cwd=cwd or self.repo, input=input_text, text=True, capture_output=True)
        if result.returncode:
            raise WorkspaceError(f"{' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
        return result

    def create(self, *, base: str = "HEAD") -> Path:
        if not (self.repo / ".git").exists():
            raise WorkspaceError(f"not a git repository: {self.repo}")
        if self.path.exists():
            return self.path
        branch_exists = (
            subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{self.branch}"],
                cwd=self.repo,
            ).returncode
            == 0
        )
        args = ["git", "worktree", "add"]
        if branch_exists:
            args += [str(self.path), self.branch]
        else:
            args += ["-b", self.branch, str(self.path), base]
        self._run(args)
        return self.path

    def snapshot(self, max_chars: int = 24000) -> str:
        files = self._run(["git", "ls-files"], cwd=self.path).stdout.splitlines()
        chunks, size = [], 0
        for name in files:
            path = self.path / name
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            block = f"\n### FILE: {name}\n{text}\n"
            if size + len(block) > max_chars:
                break
            chunks.append(block)
            size += len(block)
        return "".join(chunks)

    def _apply_direct_fallback(self, patch: str) -> bool:
        """Fallback when git apply fails: extract files directly and write to disk."""
        files = extract_files_from_diff(patch)
        if not files:
            return False
        written = False
        for rel_path, info in files.items():
            dest = self.path / rel_path
            # For new files or non-existent files or empty files, write safely
            if info.get("is_new") or not dest.exists() or (dest.is_file() and dest.stat().st_size == 0):
                dest.parent.mkdir(parents=True, exist_ok=True)
                clean_content = strip_conflict_markers(info["content"])
                dest.write_text(clean_content, encoding="utf-8")
                written = True
        if written:
            subprocess.run(["git", "add", "-N", "."], cwd=self.path, capture_output=True)
        return written

    def apply_patch(self, patch: str):
        clean_patch = strip_conflict_markers(patch)
        normalized = normalize_unified_diff(clean_patch)

        flag_options = [
            ["--whitespace=fix", "--inaccurate-eof"],
            ["--recount", "--whitespace=fix", "--inaccurate-eof"],
            ["--ignore-whitespace", "--inaccurate-eof"],
            ["--recount", "--ignore-whitespace", "--inaccurate-eof"],
            ["-3", "--whitespace=fix", "--inaccurate-eof"],
            [],
        ]

        last_error = None
        for flags in flag_options:
            try:
                self._run(["git", "apply", "--check", *flags, "-"], cwd=self.path, input_text=normalized)
                self._run(["git", "apply", *flags, "-"], cwd=self.path, input_text=normalized)
                sanitize_worktree_conflict_markers(self.path)
                return
            except WorkspaceError as exc:
                last_error = exc

        # Fallback with raw un-normalized patch
        for flags in flag_options:
            try:
                raw_patch = clean_patch.replace("\r\n", "\n")
                self._run(["git", "apply", "--check", *flags, "-"], cwd=self.path, input_text=raw_patch)
                self._run(["git", "apply", *flags, "-"], cwd=self.path, input_text=raw_patch)
                sanitize_worktree_conflict_markers(self.path)
                return
            except WorkspaceError:
                pass

        # Fallback: Direct file extraction for new files if git apply checks fail
        try:
            if self._apply_direct_fallback(clean_patch):
                sanitize_worktree_conflict_markers(self.path)
                return
        except Exception:
            pass

        if last_error:
            raise last_error

    def diff(self) -> str:
        # Register intent-to-add for newly added files so git diff outputs untracked new files
        subprocess.run(["git", "add", "-N", "."], cwd=self.path, capture_output=True)
        return self._run(["git", "diff", "--binary"], cwd=self.path).stdout

    def commit(self, message: str):
        status = self._run(["git", "status", "--porcelain"], cwd=self.path).stdout.strip()
        if not status:
            return
        self._run(["git", "add", "-A"], cwd=self.path)
        self._run(["git", "commit", "-m", message], cwd=self.path)

    def reset_hard(self):
        if self.path.exists():
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=self.path, capture_output=True)
            subprocess.run(["git", "clean", "-fd"], cwd=self.path, capture_output=True)

    def current_branch(self) -> str:
        return self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.repo).stdout.strip()

    def merge_branch(self, branch: str, *, cwd: Path | None = None):
        self._run(["git", "merge", "--no-ff", "--no-edit", "-m", f"ma: merge {branch}", branch], cwd=cwd or self.path)

    def merge_into_repo(self, message: str = "ma: merge approved worktree") -> dict:
        target = self.current_branch()
        self.commit(message)
        if target == self.branch:
            return {"merged_into": target, "branch": self.branch, "mode": "already-on-branch"}
        try:
            self._run(
                ["git", "merge", "--no-ff", "--no-edit", "-m", message, self.branch],
                cwd=self.repo,
            )
            mode = "no-ff"
        except WorkspaceError:
            self._run(["git", "merge", "--ff-only", self.branch], cwd=self.repo)
            mode = "ff-only"
        return {"merged_into": target, "branch": self.branch, "mode": mode}

    def push(self, remote: str = "origin", branch: str | None = None) -> dict:
        target = branch or self.current_branch()
        self._run(["git", "push", remote, target], cwd=self.repo)
        return {"remote": remote, "branch": target, "pushed": True}

    def remove(self):
        if self.path.exists():
            self._run(["git", "worktree", "remove", "--force", str(self.path)])
