from __future__ import annotations

import json
from pathlib import Path
from .defaults import SYSTEMS
from .skills import build_skills_instruction

CONFIG_PATH = Path.home() / ".ma" / "config.json"

DEFAULT_ROLES_CONFIG = {
    "version": "1.0",
    "roles": {
        "design": {
            "name": "Chief Architect",
            "department": "engineering",
            "description": "Phân tích yêu cầu, thiết kế kiến trúc kỹ thuật, rủi ro và acceptance criteria.",
            "primary": "ag/gemini-3.8-flash-high",
            "fallbacks": ["ag/gemini-3.8-flash-medium", "ag/gemini-3.7-flash-high"],
            "system": SYSTEMS["design"],
            "skills": [
                "spec-driven-development",
                "api-and-interface-design",
                "documentation-and-adrs",
                "source-driven-development"
            ],
            "icon": "compass"
        },
        "critique": {
            "name": "Security & QA Critic",
            "department": "engineering",
            "description": "Phản biện gay gắt, bóc tách lỗ hổng bảo mật, edge cases và test gap.",
            "primary": "ag/gemini-3.8-flash-high",
            "fallbacks": ["ag/gemini-3.8-flash-medium", "ag/gemini-3.7-flash-high"],
            "system": SYSTEMS["critique"],
            "skills": [
                "security-and-hardening",
                "security-review",
                "doubt-driven-development"
            ],
            "icon": "shield-alert"
        },
        "judgment": {
            "name": "Tech Lead Judge",
            "department": "engineering",
            "description": "Hòa giải Design & Critique, chốt phương án và phân rã Task DAG JSON.",
            "primary": "ag/gemini-3.8-flash-high",
            "fallbacks": ["ag/gemini-3.8-flash-medium", "ag/gemini-3.7-flash-high"],
            "system": SYSTEMS["judgment"],
            "skills": [
                "planning-and-task-breakdown",
                "git-workflow-and-versioning",
                "idea-refine"
            ],
            "icon": "scale"
        },
        "implementation": {
            "name": "Dev Engineer (Worker)",
            "department": "engineering",
            "description": "Viết mã nguồn cụ thể, sinh unified diff trong phạm vi allowed_files của task.",
            "primary": "ag/gemini-3.8-flash-high",
            "fallbacks": ["ag/gemini-3.8-flash-medium", "ag/gemini-3.7-flash-high"],
            "system": SYSTEMS["implementation"],
            "skills": [
                "incremental-implementation",
                "frontend-ui-engineering",
                "frontend-design",
                "web-design-guidelines",
                "test-driven-development",
                "code-simplification"
            ],
            "icon": "code"
        },
        "audit": {
            "name": "Release Auditor",
            "department": "engineering",
            "description": "Kiểm toán code diff & machine test log, phê duyệt APPROVE hoặc REJECT.",
            "primary": "ag/gemini-3.8-flash-high",
            "fallbacks": ["ag/gemini-3.8-flash-medium", "ag/gemini-3.7-flash-high"],
            "system": SYSTEMS["audit"],
            "skills": [
                "code-review-and-quality",
                "observability-and-instrumentation",
                "browser-testing-with-devtools"
            ],
            "icon": "stamp"
        },
        "report": {
            "name": "Documentation & Scribe",
            "department": "engineering",
            "description": "Tổng hợp báo cáo tiến độ, kết quả test và chi phí token.",
            "primary": "ag/gemini-3.8-flash-high",
            "fallbacks": ["ag/gemini-3.8-flash-medium", "ag/gemini-3.7-flash-high"],
            "system": SYSTEMS["report"],
            "skills": [
                "shipping-and-launch",
                "documentation-and-adrs"
            ],
            "icon": "file-text"
        },
        "scriptwriter": {
            "name": "Creative Copywriter",
            "department": "media",
            "description": "Viết kịch bản video bán hàng: Hook 3s, nỗi đau khách hàng, giải pháp, CTA.",
            "primary": "ag/gemini-3.8-flash-high",
            "fallbacks": ["ag/gemini-3.8-flash-medium", "ag/gemini-3.7-flash-high"],
            "system": "You are a professional video copywriter. Write engaging, high-converting video scripts structured into visual scenes, narrations, and on-screen text.",
            "skills": [],
            "icon": "pen-tool"
        },
        "prompter": {
            "name": "Visual Prompter",
            "department": "media",
            "description": "Chuyển kịch bản phân cảnh thành visual prompt chi tiết cho mô hình AI tạo ảnh/video.",
            "primary": "ag/gemini-3.8-flash-high",
            "fallbacks": ["ag/gemini-3.8-flash-medium", "ag/gemini-3.7-flash-high"],
            "system": "You are an AI prompt engineer specializing in cinematic, commercial-grade visual prompts for image and video diffusion models.",
            "skills": [],
            "icon": "image"
        },
        "voice": {
            "name": "Voice Talent (TTS)",
            "department": "media",
            "description": "Thu âm giọng đọc kịch bản qua text-to-speech tự nhiên và trích xuất phụ đề.",
            "primary": "edge-tts:vi-VN-HoaiMyNeural",
            "fallbacks": ["edge-tts:vi-VN-NamMinhNeural"],
            "system": "Voiceover synthesis engine",
            "skills": [],
            "icon": "mic"
        },
        "video_editor": {
            "name": "Video Render Editor",
            "department": "media",
            "description": "Ghép hình ảnh, clip, âm thanh và phụ đề thành video hoàn chỉnh bằng FFmpeg.",
            "primary": "ffmpeg-engine",
            "fallbacks": [],
            "system": "FFmpeg video compositor and effects pipeline",
            "skills": [],
            "icon": "film"
        }
    },
    "departments": [
        {
            "id": "engineering",
            "name": "Phòng Kỹ Thuật (Coding & Software)",
            "description": "Đội ngũ kiến trúc sư, phản biện, lập trình viên và kiểm toán mã nguồn.",
            "roles": ["design", "critique", "judgment", "implementation", "audit", "report"]
        },
        {
            "id": "media",
            "name": "Phòng Media & Truyền Thông (Video Production)",
            "description": "Đội ngũ biên kịch, kỹ sư prompt, diễn viên lồng tiếng và dựng video bán hàng.",
            "roles": ["scriptwriter", "prompter", "voice", "video_editor"]
        }
    ]
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_ROLES_CONFIG)
        return DEFAULT_ROLES_CONFIG.copy()

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure all default roles exist in loaded config
            updated = False
            if "roles" not in data:
                data["roles"] = {}
                updated = True
            for k, v in DEFAULT_ROLES_CONFIG["roles"].items():
                if k not in data["roles"]:
                    data["roles"][k] = v
                    updated = True
                else:
                    if "skills" not in data["roles"][k]:
                        data["roles"][k]["skills"] = v.get("skills", [])
                        updated = True
            if "departments" not in data:
                data["departments"] = DEFAULT_ROLES_CONFIG["departments"]
                updated = True
            if updated:
                save_config(data)
            return data
    except Exception:
        return DEFAULT_ROLES_CONFIG.copy()


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def get_role_skills(role: str) -> list[str]:
    cfg = load_config()
    return cfg.get("roles", {}).get(role, {}).get("skills", [])


def get_models_mapping() -> dict[str, str]:
    cfg = load_config()
    res = {}
    for role, item in cfg.get("roles", {}).items():
        if "primary" in item:
            res[role] = item["primary"]
    return res


def get_fallbacks_mapping() -> dict[str, list[str]]:
    cfg = load_config()
    res = {}
    for role, item in cfg.get("roles", {}).items():
        primary = item.get("primary")
        fallbacks = item.get("fallbacks", [])
        chain = []
        if primary:
            chain.append(primary)
        for fb in fallbacks:
            if fb not in chain:
                chain.append(fb)
        res[role] = chain
    return res


def get_systems_mapping(include_skills: bool = True) -> dict[str, str]:
    cfg = load_config()
    res = {}
    for role, item in cfg.get("roles", {}).items():
        base_system = item.get("system", SYSTEMS.get(role, ""))
        skills = item.get("skills", [])
        if include_skills and skills:
            skills_block = build_skills_instruction(skills)
            res[role] = f"{base_system}\n\n{skills_block}" if skills_block else base_system
        else:
            res[role] = base_system
    return res

