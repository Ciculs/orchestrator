from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Ensure utf-8 output on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from .approve import grant_approval, revoke_approval
from .config import load_config, save_config
from .events import events_path, request_cancel, tail_events
from .ops import doctor
from .orchestrator import load_9router_key
from .router import NineRouterClient, fetch_available_models
from .store import TaskStore
from .usage import UsageLedger

STATIC_DIR = Path(__file__).parent / "static"


class UIRequestHandler(BaseHTTPRequestHandler):
    store_path = Path.home() / ".ma" / "state.sqlite"

    def log_message(self, format: str, *args):
        # Keep server quiet unless error
        if args and str(args[1])[0] in ("4", "5"):
            super().log_message(format, *args)

    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_json(self, data: any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_body_json(self) -> dict:
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0:
            return {}
        raw = self.rfile.read(content_len).decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # Static assets
        if path == "/" or path == "/index.html":
            return self._serve_file(STATIC_DIR / "index.html")
        if path.startswith("/static/"):
            rel_path = path[len("/static/") :]
            file_path = STATIC_DIR / rel_path
            return self._serve_file(file_path)

        # REST APIs
        if path == "/api/status":
            try:
                doc = doctor(probe_models=False)
                key_loaded = False
                try:
                    key_loaded = bool(load_9router_key())
                except Exception:
                    pass
                return self._send_json({
                    "ok": doc.get("ok", False),
                    "router_up": any(c["check"] == "9router_up" and c["ok"] for c in doc.get("checks", [])),
                    "key_loaded": key_loaded,
                    "checks": doc.get("checks", []),
                })
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        if path == "/api/models":
            try:
                key = load_9router_key()
                refresh = query.get("refresh", ["0"])[0] in {"1", "true", "yes"}
                models = fetch_available_models(api_key=key, force_refresh=refresh)
                # Group by provider
                providers = {}
                for m in models:
                    p = m["provider"]
                    providers.setdefault(p, []).append(m)
                return self._send_json({"total": len(models), "providers": providers, "models": models})
            except Exception as e:
                return self._send_json({"error": str(e), "models": []}, 500)

        if path == "/api/roles":
            try:
                cfg = load_config()
                return self._send_json(cfg)
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

        if path == "/api/projects":
            try:
                store = TaskStore(self.store_path)
                try:
                    projects = store.list_projects()
                    return self._send_json({"projects": projects})
                finally:
                    store.close()
            except Exception as e:
                return self._send_json({"error": str(e), "projects": []}, 500)

        if path.startswith("/api/projects/"):
            project_id = path.split("/api/projects/")[1].strip()
            try:
                store = TaskStore(self.store_path)
                try:
                    project = store.get_project(project_id)
                    stages = store.list_stages(project_id)
                    tasks = store.list_tasks(project_id)

                    # Gather evidence (model calls, diff, test output)
                    with store._lock:
                        evidence_rows = store.db.execute(
                            "SELECT kind, data, created_at FROM evidence WHERE project_id=? ORDER BY id",
                            (project_id,),
                        ).fetchall()
                    evidences = []
                    diff = ""
                    for r in evidence_rows:
                        item = {"kind": r[0], "data": json.loads(r[1]), "created_at": r[2]}
                        evidences.append(item)
                        if r[0] == "git_diff" and isinstance(item["data"], dict):
                            diff = item["data"].get("diff", diff)

                    return self._send_json({
                        "project": project,
                        "stages": stages,
                        "tasks": tasks,
                        "evidences": evidences,
                        "diff": diff,
                    })
                finally:
                    store.close()
            except KeyError:
                return self._send_json({"error": "Project not found"}, 404)
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

        if path.startswith("/api/events/"):
            project_id = path.split("/api/events/")[1].strip()
            return self._stream_events(project_id)

        if path == "/api/usage":
            try:
                ledger = UsageLedger()
                with ledger.lock:
                    rows = ledger.db.execute(
                        "SELECT model, role, count(1) as calls, sum(tokens_in) as tokens_in, "
                        "sum(tokens_out) as tokens_out, sum(cost_usd) as cost "
                        "FROM usage_events GROUP BY model, role"
                    ).fetchall()
                usage_summary = [dict(r) for r in rows]
                totals = ledger.total_cost()
                return self._send_json({"totals": totals, "breakdown": usage_summary})
            except Exception as e:
                return self._send_json({"error": str(e), "totals": {"cost_usd": 0, "calls": 0}}, 500)

        if path == "/api/skills":
            try:
                from .skills import list_installed_skills
                skills = list_installed_skills()
                return self._send_json({"ok": True, "skills": skills})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        self._send_json({"error": "Not Found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_body_json()

        if path == "/api/roles":
            try:
                if "roles" in body:
                    save_config(body)
                elif "role" in body:
                    cfg = load_config()
                    r_id = body["role"]
                    if r_id in cfg.get("roles", {}):
                        if "primary" in body:
                            cfg["roles"][r_id]["primary"] = body["primary"]
                        elif "primary_model" in body:
                            cfg["roles"][r_id]["primary"] = body["primary_model"]
                        if "fallbacks" in body:
                            cfg["roles"][r_id]["fallbacks"] = body["fallbacks"]
                        if "system" in body:
                            cfg["roles"][r_id]["system"] = body["system"]
                        elif "system_prompt" in body:
                            cfg["roles"][r_id]["system"] = body["system_prompt"]
                        if "skills" in body:
                            cfg["roles"][r_id]["skills"] = body["skills"]
                        save_config(cfg)
                else:
                    save_config(body)
                return self._send_json({"ok": True, "config": load_config()})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        if path == "/api/skills/install":
            pkg = body.get("package", "").strip()
            if not pkg:
                return self._send_json({"error": "package is required"}, 400)
            try:
                from .skills import install_skill
                res = install_skill(pkg)
                return self._send_json(res)
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        if path == "/api/roles/batch":
            target_model = body.get("model", "ag/gemini-3.8-flash").strip()
            department_scope = body.get("department", "all")
            fallbacks = body.get("fallbacks", ["ag/gemini-3.7-flash-high", "ag/gemini-3-flash"])
            if not target_model:
                return self._send_json({"error": "model is required"}, 400)
            try:
                cfg = load_config()
                roles_dict = cfg.get("roles", {})
                updated_count = 0
                for r_id, r_info in roles_dict.items():
                    if department_scope and department_scope != "all":
                        if r_info.get("department") != department_scope:
                            continue
                    if r_id in {"video_editor", "voice"} and department_scope != "all":
                        continue
                    r_info["primary"] = target_model
                    r_info["fallbacks"] = [fb for fb in fallbacks if fb != target_model]
                    updated_count += 1
                save_config(cfg)
                return self._send_json({
                    "ok": True,
                    "config": load_config(),
                    "applied_model": target_model,
                    "updated_count": updated_count
                })
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        if path == "/api/ship":
            repo = body.get("repo", "").strip()
            goal = body.get("goal", "").strip()
            verify = body.get("verify")
            require_approval = body.get("require_approval", True)
            workers = int(body.get("workers", 0))

            if not repo or not goal:
                return self._send_json({"error": "repo and goal are required"}, 400)

            repo_path = Path(repo).resolve()
            if not repo_path.is_dir():
                return self._send_json({"error": f"Directory does not exist: {repo}"}, 400)

            # Generate project_id in store
            store = TaskStore(self.store_path)
            try:
                project_id = store.create_project("web-ship", str(repo_path), goal)
            finally:
                store.close()

            # Spawn ship runner in separate process for clean PID management and cancellation
            cmd = [
                sys.executable,
                "-m",
                "ma.cli",
                "ship",
                str(repo_path),
                goal,
                "--project-id",
                project_id,
                "--workers",
                str(workers),
            ]
            if verify:
                cmd.extend(["--verify", verify])
            if require_approval:
                cmd.append("--require-approval")

            def run_ship_proc():
                subprocess.run(cmd, check=False)

            threading.Thread(target=run_ship_proc, daemon=True).start()
            return self._send_json({"ok": True, "project_id": project_id})

        if path == "/api/approve":
            project_id = body.get("project_id")
            note = body.get("note", "approved via web UI")
            if not project_id:
                return self._send_json({"error": "project_id required"}, 400)
            try:
                path_appr = grant_approval(project_id, note=note)
                return self._send_json({"ok": True, "approval_file": str(path_appr)})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        if path == "/api/cancel":
            project_id = body.get("project_id")
            soft = body.get("soft", False)
            if not project_id:
                return self._send_json({"error": "project_id required"}, 400)
            try:
                res = request_cancel(project_id, hard=not soft)
                return self._send_json({"ok": True, "result": res})
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        # Media department APIs
        if path == "/api/media/script":
            topic = body.get("topic", "")
            product = body.get("product", "")
            target_audience = body.get("target_audience", "")
            if not topic and not product:
                return self._send_json({"error": "product or topic is required"}, 400)

            cfg = load_config()
            role_cfg = cfg.get("roles", {}).get("scriptwriter", {})
            primary = role_cfg.get("primary", "ntt/gpt-5.6-sol")
            fallbacks = role_cfg.get("fallbacks", ["ntt/gpt-5.6-sol", "ntt/gpt-5.6-luna"])
            models_to_try = [primary] + [m for m in fallbacks if m != primary]
            system = role_cfg.get("system", "")

            user_prompt = f"""
Hãy viết kịch bản video ngắn (Reels/TikTok/Shorts) bán hàng cho:
Sản phẩm: {product}
Chủ đề / Điểm nhấn: {topic}
Khách hàng mục tiêu: {target_audience}

Cấu trúc kịch bản phải bao gồm:
1. Hook (0-3s): Câu mở đầu gây sốc hoặc khơi gợi tò mò mạnh mẽ.
2. Nỗi đau / Vấn đề (4-15s): Vấn đề khách hàng gặp phải.
3. Giải pháp & Sản phẩm (16-35s): Giới thiệu sản phẩm giải quyết vấn đề thế nào.
4. Kêu gọi hành động CTA (36-45s): Hướng dẫn đặt hàng ngay.

Xuất định dạng JSON gồm các scenes:
[
  {{"scene": 1, "duration_s": 3, "visual": "mô tả hình ảnh", "narration": "lời đọc", "onscreen_text": "chữ hiển thị"}},
  ...
]
"""
            errors = []
            try:
                client = NineRouterClient("http://127.0.0.1:20128", load_9router_key(), timeout=30)
                for model in models_to_try:
                    try:
                        res = client.call(model, user_prompt, system=system)
                        return self._send_json({"ok": True, "model": model, "content": res.content})
                    except Exception as exc:
                        errors.append(f"{model}: {exc}")
                return self._send_json({"ok": False, "error": "All models failed: " + " | ".join(errors)}, 500)
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        if path == "/api/media/prompts":
            scene_desc = body.get("scene_description", "")
            if not scene_desc:
                return self._send_json({"error": "scene_description is required"}, 400)

            cfg = load_config()
            role_cfg = cfg.get("roles", {}).get("prompter", {})
            primary = role_cfg.get("primary", "ntt/gemini-3.8-flash")
            fallbacks = role_cfg.get("fallbacks", ["ntt/gpt-5.6-sol", "ntt/gpt-5.6-luna"])
            models_to_try = [primary] + [m for m in fallbacks if m != primary]
            system = role_cfg.get("system", "")

            user_prompt = f"""
Dựa trên mô tả phân cảnh sau: "{scene_desc}"
Hãy tạo 2 prompt tiếng Anh chuẩn:
1. Image Prompt (Midjourney/Flux style): Mô tả cực kỳ chi tiết về ánh sáng, góc máy, phong cách điện ảnh commercial 8k.
2. Video Prompt (Kling/Luma style): Mô tả chuyển động camera và hành động của nhân vật trong cảnh.
"""
            errors = []
            try:
                client = NineRouterClient("http://127.0.0.1:20128", load_9router_key(), timeout=20)
                for model in models_to_try:
                    try:
                        res = client.call(model, user_prompt, system=system)
                        return self._send_json({"ok": True, "model": model, "content": res.content})
                    except Exception as exc:
                        errors.append(f"{model}: {exc}")
                return self._send_json({"ok": False, "error": "All models failed: " + " | ".join(errors)}, 500)
            except Exception as e:
                return self._send_json({"ok": False, "error": str(e)}, 500)

        self._send_json({"error": "Not Found"}, 404)

    def _serve_file(self, file_path: Path):
        if not file_path.exists() or not file_path.is_file():
            self._send_json({"error": "File not found"}, 404)
            return

        mime_type, _ = mimetypes.guess_type(str(file_path))
        mime_type = mime_type or "application/octet-stream"

        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{mime_type}; charset=utf-8" if "text" in mime_type or "javascript" in mime_type else mime_type)
        self.send_header("Content-Length", str(len(content)))
        self._send_cors()
        self.end_headers()
        self.wfile.write(content)

    def _stream_events(self, project_id: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._send_cors()
        self.end_headers()

        # Stream existing events then follow new events
        try:
            for ev in tail_events(project_id, follow=True, sleep_s=0.5, max_idle=300.0):
                msg = f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def run_server(host: str = "127.0.0.1", port: int = 20129, open_browser: bool = True):
    server = ThreadingHTTPServer((host, port), UIRequestHandler)
    url = f"http://{host}:{port}"
    print(f"[UI] AI Company Orchestrator UI running at: {url}")
    print("Press Ctrl+C to stop.")

    if open_browser:
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping UI server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
