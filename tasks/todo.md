# Task List: AI Company Orchestrator with Modern Animated UI & 9Router Integration

## Task 1: Xây dựng Module Quản lý Cấu hình Role & Model Động (`ma/config.py`)
**Description:** Tạo module `ma/config.py` để lưu và tải cấu hình roles, primary models, fallback models, system prompts từ `~/.ma/config.json`. Cập nhật `ma/orchestrator.py` và `ma/defaults.py` để ưu tiên nạp cấu hình người dùng thay vì hardcode.
**Acceptance criteria:**
- [x] `ma/config.py` đọc/ghi file `~/.ma/config.json` an toàn.
- [x] Fallback về cấu hình mặc định nếu file chưa tồn tại.
- [x] `ma.orchestrator.Orchestrator` nhận cấu hình từ `config.py`.
**Verification:**
- [x] `python -c "from ma.config import load_config, save_config; c = load_config(); print(c.keys())"` chạy thành công.
**Dependencies:** None
**Files likely touched:**
- `ma/config.py`
- `ma/orchestrator.py`
**Estimated scope:** Small (2 files)

---

## Task 2: Module kết nối 9Router để Fetch & Phân loại Models (`ma/router.py`)
**Description:** Bổ sung phương thức `list_models()` vào `NineRouterClient` trong `ma/router.py` để lấy danh sách toàn bộ models từ `http://127.0.0.1:20128/v1/models`, bóc tách provider (`ntt`, `ag`, `cld`, `cx`, `sol`,...), context window và capabilities.
**Acceptance criteria:**
- [x] Hàm `fetch_available_models()` trả về danh sách model đã nhóm theo provider.
- [x] Xử lý ngoại lệ mượt mà nếu 9Router chưa bật hoặc key lỗi.
**Verification:**
- [x] `python -c "from ma.router import fetch_available_models; print(len(fetch_available_models()))"` in ra số lượng model (>100).
**Dependencies:** Task 1
**Files likely touched:**
- `ma/router.py`
**Estimated scope:** Small (1 file)

---

## Task 3: Xây dựng Backend REST API & Event Streaming Server (`ma/ui_server.py`)
**Description:** Tạo server HTTP nhẹ (`ma/ui_server.py`) cung cấp các endpoint API: `/api/models`, `/api/roles`, `/api/projects`, `/api/ship`, `/api/approve`, `/api/cancel`, và SSE endpoint `/api/events/:id` để stream logs theo thời gian thực.
**Acceptance criteria:**
- [x] API trả về danh sách model từ 9Router.
- [x] API cho phép lưu cấu hình role mới.
- [x] SSE `/api/events/:id` truyền tải được các dòng event JSONL thời gian thực.
- [x] Endpoint `/api/ship` khởi chạy `ma.orchestrator` trong tiến trình nền không block server.
**Verification:**
- [x] Chạy server test và gọi `curl.exe http://127.0.0.1:20129/api/models` nhận JSON hợp lệ.
**Dependencies:** Task 1, Task 2
**Files likely touched:**
- `ma/ui_server.py`
**Estimated scope:** Medium (2 files)

---

## Task 4: Tích hợp Lệnh `ma ui` vào CLI (`ma/cli.py`)
**Description:** Thêm subcommand `ui` vào `ma/cli.py` với các tham số `--port` (mặc định 20129), `--no-open` (không tự mở browser), `--host` (mặc định 127.0.0.1).
**Acceptance criteria:**
- [x] Gõ `ma ui` hoặc `python -m ma.cli ui` khởi động UI server và in URL truy cập.
- [x] Tự động mở trình duyệt nếu không có cờ `--no-open`.
**Verification:**
- [x] `python -m ma.cli ui --help` hiển thị đúng options.
**Dependencies:** Task 3
**Files likely touched:**
- `ma/cli.py`
**Estimated scope:** Small (1 file)

---

## Checkpoint: Backend & Core APIs Ready
- [x] API đọc được 169+ model từ 9Router.
- [x] Config role lưu/đọc ổn định qua API.
- [x] Server chạy mượt mà, không xung đột port.

---

## Task 5: Thiết kế Giao diện Công ty Ảo & Sơ đồ Phòng ban với Hoạt Họa Sinh Động
**Description:** Xây dựng giao diện web Single-Page Application (HTML5, Tailwind CSS, Lucide icons, JavaScript ES6) mô phỏng tòa nhà công ty AI. Bao gồm sơ đồ tương tác của 2 phòng ban: Phòng Kỹ thuật (Engineering) và Phòng Media (Video Production).
**Acceptance criteria:**
- [x] Sơ đồ tổ chức (Org Chart) trực quan với 7 role phòng Coding và 4 role phòng Media.
- [x] Hiệu ứng hoạt họa CSS/SVG:
  - Trạng thái Role: Idle (thở nhẹ), Working/Thinking (vòng sáng neon xoay), Critiquing (tia cảnh báo vàng), Testing (bánh răng quay), Auditing (chớp sáng xanh).
  - Luồng dữ liệu (Dataflow): Đường truyền laser/particle chạy giữa các role theo tiến độ thực tế.
- [x] Responsive, dark mode hiện đại, typography chuẩn code editor.
**Verification:**
- [x] Mở giao diện trên trình duyệt và kiểm tra hiệu ứng chuyển động mượt mà 60fps.
**Dependencies:** Task 3, Task 4
**Files likely touched:**
- `ma/static/index.html`
- `ma/static/app.js`
- `ma/static/style.css`
**Estimated scope:** Medium (3 files)

---

## Task 6: Xây dựng Modal Tùy chỉnh AI Agent cho từng Role với Model Picker 9Router
**Description:** Tạo giao diện Modal "Role Configuration / AI Staffing". Cho phép click vào từng Role trên sơ đồ công ty để:
- Chọn Primary Model và Fallback Model từ dropdown danh sách 9Router (có ô tìm kiếm và lọc theo provider).
- Tùy chỉnh Persona / System Prompt cho Role.
- Lưu cấu hình và áp dụng ngay lập tức cho các lần chạy `ma ship`.
**Acceptance criteria:**
- [x] Dropdown hiển thị đầy đủ các model của 9Router kèm thông tin capabilities (tools, reasoning).
- [x] Đổi model cho bất kỳ role nào và bấm Save thành công.
- [x] Reload trang vẫn giữ nguyên cấu hình đã lưu.
**Verification:**
- [x] Chọn model cho role "design", lưu lại, kiểm tra file `~/.ma/config.json` có cập nhật giá trị mới.
**Dependencies:** Task 5
**Files likely touched:**
- `ma/static/app.js`
- `ma/static/index.html`
**Estimated scope:** Small (2 files)

---

## Task 7: Bảng Điều khiển Pipeline Coding Thời gian thực (Run, Diff & Human Gate)
**Description:** Xây dựng khu vực điều khiển tác vụ coding:
- Form tạo Run mới: chọn thư mục dự án, nhập Goal, nhập lệnh Verify.
- Live Pipeline Visualizer: Highlight Role đang làm việc theo thời gian thực (Design -> Critique -> Judge -> Worker Waves -> Machine Verify -> Audit).
- Diff Viewer: Xem trực tiếp patch code sinh ra bởi Dev workers.
- Machine Test Terminal: Xem stdout/stderr từ lệnh test thật trên máy.
- Human Approval Banner: Nút "Approve Merge" hoặc "Reject" khi pipeline đến chặng kiểm duyệt cuối.
- Cost & Token Tracker: Thống kê chi phí USD và tokens đã tiêu hao.
**Acceptance criteria:**
- [x] Tạo task và theo dõi luồng chạy trực tiếp trên UI mà không cần gõ lệnh terminal.
- [x] Đổi màu và kích hoạt animation của đúng Role đang xử lý stage tương ứng.
- [x] Nút Approve kích hoạt lệnh merge an toàn.
**Verification:**
- [x] Chạy thử một ship task giả lập/thực tế và thấy animation kích hoạt theo từng stage.
**Dependencies:** Task 6
**Files likely touched:**
- `ma/static/app.js`
- `ma/static/index.html`
**Estimated scope:** Medium (2 files)

---

## Task 8: Tích hợp VS Code (Tasks, Launch & Webview)
**Description:** Tạo cấu hình `.vscode/tasks.json` và tài liệu hướng dẫn mở giao diện trực tiếp bên trong tab Simple Browser của VS Code (`Ctrl+Shift+P -> Simple Browser: Show -> http://127.0.0.1:20129`), đồng thời cung cấp mã nguồn VS Code Extension mở Webview tích hợp nếu muốn nhúng vào thanh công cụ VS Code.
**Acceptance criteria:**
- [x] File `.vscode/tasks.json` cho phép bấm `Terminal -> Run Task -> Start AI Orchestrator UI`.
- [x] Giao diện hiển thị hoàn hảo bên trong tab Simple Browser của VS Code.
**Verification:**
- [x] Khởi chạy task trong VS Code và mở giao diện thành công.
**Dependencies:** Task 7
**Files likely touched:**
- `.vscode/tasks.json`
- `README.md`
**Estimated scope:** Small (2 files)

---

## Task 9: Quy hoạch Giao diện & Pipeline cho Phòng Media / Video Render AI
**Description:** Thiết kế tab riêng biệt trên UI cho "Phòng Media & Truyền thông":
- Giao diện kịch bản (Script Generator) sử dụng model viết content.
- Bảng phân cảnh video (Scene Breakdown & Image/Video Prompts).
- Mockup quy trình ghép nối Audio TTS & Render clip bán hàng.
**Acceptance criteria:**
- [x] Tab "Media Department" hiển thị rõ quy trình sản xuất video 4 bước.
- [x] Cho phép chọn model riêng cho Scriptwriter và Prompt Generator từ 9Router.
**Verification:**
- [x] Chuyển tab sang Media Department và cấu hình thử model.
**Dependencies:** Task 7
**Files likely touched:**
- `ma/static/app.js`
- `ma/static/index.html`
**Estimated scope:** Small (2 files)

---

## Checkpoint: Hệ Thống Hoàn Thiện & Sẵn Sàng Vận Hành
- [x] Giao diện hoạt họa đẹp mắt, trực quan mô phỏng công ty AI.
- [x] 100% tích hợp 9Router local, tùy biến model linh hoạt cho từng role.
- [x] Điều khiển pipeline Coding mượt mà, đầy đủ các chốt kiểm duyệt (Machine test, Auditor, Human approve).
- [x] Chạy độc lập hoặc nhúng trong VS Code chỉ với 1 click.
