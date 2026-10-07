from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILLS_DIR = Path.home() / ".agents" / "skills"


def get_skills_directory() -> Path:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    return SKILLS_DIR


def list_installed_skills() -> list[dict]:
    """Scan and return all installed skills found in ~/.agents/skills/."""
    skills_dir = get_skills_directory()
    if not skills_dir.exists():
        return []

    results = []
    for entry in sorted(skills_dir.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        skill_file = entry / "SKILL.md"
        if not skill_file.is_file():
            continue

        size = skill_file.stat().st_size
        summary = ""
        try:
            with open(skill_file, "r", encoding="utf-8", errors="replace") as f:
                lines = [f.readline().strip() for _ in range(25)]
                # extract first substantive non-heading line
                for line in lines:
                    if line and not line.startswith("#") and not line.startswith("<!--"):
                        summary = line[:200]
                        break
        except Exception:
            summary = "Agent skill package"

        results.append({
            "name": entry.name,
            "path": str(skill_file),
            "size": size,
            "summary": summary or entry.name,
        })
    return results


def load_skill_content(name: str) -> str:
    """Read full SKILL.md content for a given skill name."""
    skill_file = get_skills_directory() / name / "SKILL.md"
    if not skill_file.is_file():
        return ""
    try:
        with open(skill_file, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return ""


def build_skills_instruction(skill_names: list[str]) -> str:
    """Concatenate specified skills into an instruction block for the agent."""
    if not skill_names:
        return ""

    blocks = []
    for name in skill_names:
        content = load_skill_content(name)
        if content:
            blocks.append(
                f"\n<!-- BEGIN SKILL: {name} -->\n"
                f"# SPECIALIZED SKILL GUIDELINES: {name}\n\n"
                f"{content}\n"
                f"<!-- END SKILL: {name} -->\n"
            )

    if not blocks:
        return ""

    return (
        "\n\n================================================================================\n"
        "ASSIGNED DEPARTMENT SKILLS & OPERATIONAL PROTOCOLS\n"
        "Follow these authoritative guidelines and checklists when executing this role:\n"
        "================================================================================\n"
        + "\n".join(blocks)
    )


def install_skill(package: str) -> dict:
    """Install a skill globally via npx skills add <package> -g -y."""
    npx_cmd = "npx.cmd" if sys.platform == "win32" else "npx"
    if not shutil.which(npx_cmd) and not shutil.which("npx"):
        return {"ok": False, "error": f"{npx_cmd} not found in system PATH"}

    cmd = [npx_cmd, "skills", "add", package, "-g", "-y"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Installation timed out after 180 seconds"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
