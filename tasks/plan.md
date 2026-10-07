# Implementation Plan: AI Company Orchestrator with Modern Animated UI & 9Router Integration

## Overview
Dự án nhằm nâng cấp `duylight2004/multi-agent-orchestrator` (`ma`) thành một nền tảng **AI Virtual Company Orchestrator** toàn diện:
1. Giao diện trực quan hiện đại mô phỏng **Tòa nhà / Sơ đồ tổ chức công ty ảo (Company Org Chart & Headquarters)** với hiệu ứng hoạt họa (animations) sinh động cho từng phòng ban và từng role.
2. Tích hợp trực tiếp với **9Router Local Gateway** (`http://127.0.0.1:20128`), tự động quét và load danh sách 169+ models hiện có (`ntt`, `ag`, `cld`, `cx`, `sol`,...).
3. Cho phép **tùy chỉnh AI Agent theo từng Role**: chọn Model chính, Model dự phòng (fallback), chỉnh sửa System Prompt, phân quyền kiểm duyệt.
4. Điều khiển và giám sát quy trình Coding tự động (Ship, Watch, Approve, Cancel, Diff Viewer, Machine Test Logs, Cost Ledger).
5. Tích hợp mượt mà vào **VS Code** (chạy dưới dạng Webview Panel hoặc Tab Simple Browser với 1 click).
6. Quy hoạch sẵn kiến trúc cho **Phòng Media & Video Production** (dự án phụ render video AI bán hàng).

---

## Architecture Decisions

### 1. Backend & Configuration (`ma/` core)
- **Cấu hình động (`~/.ma/config.json`)**: Thay thế các hardcode model cũ bằng module `ma/config.py`. Hệ thống sẽ ưu tiên đọc config từ file này trước khi fallback về `ma/defaults.py`.
- **API Server nhẹ (`ma/ui_server.py`)**: Sử dụng Python built-in `http.server` hoặc FastAPI/Uvicorn, không làm nặng thêm core engine.
  - Cung cấp REST endpoints:
    - `GET /api/models`: Gọi 9Router lấy danh sách model theo provider và capabilities.
    - `GET /api/roles`: Lấy danh sách role, system prompts, models gán cho từng role.
    - `POST /api/roles`: Lưu cấu hình role & model vào `~/.ma/config.json`.
    - `GET /api/projects`: Danh sách các run/project trong `~/.ma/state.sqlite`.
    - `GET /api/projects/:id`: Chi tiết stage, task DAG, machine verification, diff, audit.
    - `GET /api/events/:id`: Server-Sent Events (SSE) để truyền trực tiếp event log JSONL lên UI theo thời gian thực.
    - `POST /api/ship`: Kích hoạt pipeline `ma ship` chạy nền.
    - `POST /api/approve/:id`: Duyệt Human Gate (tương đương `ma approve`).
    - `POST /api/cancel/:id`: Huỷ tiến trình (soft / hard kill PID).
- **Lệnh CLI mới**: Bổ sung `ma ui [--port 20129] [--open]` để khởi chạy dashboard trong 1 giây.

### 2. Frontend UI/UX & Hoạt Họa (Animation)
- **Phong cách thiết kế**: Cyberpunk HQ / Sci-Fi Operations Center kết hợp Modern Glassmorphism (Tailwind dark mode, neon glow, phosphor terminal).
- **Mô phỏng Công ty AI (Virtual AI Company Campus)**:
  - **Phòng Kỹ Thuật (Software Engineering Dept - Ưu tiên 1)**:
    - 🏛️ **Chief Architect (Design)**: Glowing blue pulse khi phân tích kiến trúc.
    - 🛡️ **Security Critic (Critique)**: Radar scanner / alert amber glow khi phản biện, tìm bug.
    - ⚖️ **Tech Judge (Judgment)**: Golden balance scale animation khi tổng hợp Task DAG.
    - ⚡ **Dev Engineers (Wave Workers)**: Matrix typing / cyber code runners song song trong worktree.
    - ⚙️ **Machine QA Engine (Verification)**: Spinning mechanical gears, test checkmark/cross.
    - 📜 **Release Auditor (Audit)**: Stamp approval animation (APPROVE / REJECT).
    - 👑 **CEO / Founder (Human Gate)**: Holographic action bar (1-click Approve / Request Changes).
  - **Phòng Media & Truyền thông (Video Production Dept - Module quy hoạch)**:
    - ✍️ **Creative Copywriter**: Story script typing.
    - 🎨 **Visual Prompter**: Scene prompt generation.
    - 🎙️ **Voice Actor**: Audio waveform animation.
    - 🎬 **Video Editor**: Timeline clip rendering.
  - **Hiệu ứng luồng dữ liệu (Dataflow Stream)**:
    - Tia năng lượng (laser / particles) di chuyển nối tiếp giữa các Agent theo tiến độ thực tế từ event stream.

### 3. Tích hợp VS Code
- **Cấu hình Workspace**: Tự động sinh file `.vscode/tasks.json` và `.vscode/launch.json` để mở UI bằng phím tắt hoặc thanh trạng thái.
- **VS Code Extension / Webview Wrapper**: Cung cấp một extension đóng gói hoặc command tích hợp để mở giao diện trực tiếp bên trong cửa sổ VS Code mà không cần thoát ra trình duyệt ngoài.

---

## Task Breakdown

### Phase 1: Nền tảng Config Động & Tích hợp 9Router
- **Task 1: Xây dựng Module Quản lý Cấu hình Role & Model động (`ma/config.py`)**
- **Task 2: Module kết nối 9Router để Fetch & Phân loại Models (`ma/router.py`)**

### Phase 2: Backend UI Server & Event Streaming
- **Task 3: Xây dựng REST API & SSE Server cho UI (`ma/ui_server.py`)**
- **Task 4: Tích hợp lệnh `ma ui` vào CLI (`ma/cli.py`)**

### Phase 3: Giao diện Người dùng Hiện đại & Hoạt Họa Công ty AI
- **Task 5: Thiết kế Giao diện Công ty ảo & Sơ đồ Phòng ban với Hoạt họa Sinh động**
- **Task 6: Xây dựng Modal Tùy chỉnh AI Agent cho từng Role với Model Picker 9Router**
- **Task 7: Xây dựng Bảng Điều khiển Pipeline Coding Thời gian thực (Live Run, Diff & Human Gate)**

### Phase 4: Tích hợp VS Code & Quy hoạch Module Media
- **Task 8: Tích hợp VS Code (Tasks, Commands & Webview)**
- **Task 9: Thiết kế & Quy hoạch giao diện Phòng Media / Video Render AI**

---

## Risks and Mitigations
| Rủi ro | Mức độ | Biện pháp xử lý |
|---|---|---|
| Model trong 9Router bị die / hết quota giữa chừng | Cao | `ma` đã có sẵn cơ chế timeout 15s x 3 + tự động nhảy sang fallback model + blacklist model chết trong cùng 1 run. UI sẽ báo đỏ model lỗi để người dùng đổi model. |
| UI server xung đột port hoặc tiến trình treo | Trung bình | Tự động dò port khả dụng (20129 -> 20130...), có cờ kill PID sạch sẽ khi tắt UI. |
| Hiệu ứng hoạt họa làm lag máy | Thấp | Sử dụng pure CSS animations & SVG hardware-accelerated, tối ưu 60fps không tốn tài nguyên GPU/CPU. |

---

## Open Questions cho User
1. **Hình thức hiển thị ưu tiên**: Bạn muốn Dashboard mở ra một **Tab trình duyệt đầy đủ** (hỗ trợ màn hình lớn) kèm tùy chọn mở trong **VS Code Simple Browser**, hay bắt buộc phải là một **VS Code Extension riêng biệt (.vsix)**?
2. **Phong cách hoạt họa**: Bạn thích phong cách **Sci-Fi Cyberpunk Operations** (neon, radar, particle dataflow) hay phong cách **Modern Clean Enterprise / SaaS** (tinh gọn, tối giản, chuyên nghiệp)?
