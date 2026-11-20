# FR-08c — Nhập 1 file (Excel…) → nhiều dự án, mỗi dự án có sẵn chat để lên kế hoạch

Trạng thái: Xong · Branch: `feature/fr08c-import-projects` · Cập nhật: 2026-11-20

<!-- Hiếu duyệt 2026-11-20: "ok làm đi và sửa lại luật cho phần AI có thể chỉnh sửa bảng project".
Luật mới: AI chỉ TẠO dự án khi nhập file, kiểm giới hạn gói như Backend, không sửa / xóa (CLAUDE.md §8, docs/database.md).
Claude Code soạn plan rồi làm liền. -->

## 0. Mục tiêu
- **Làm gì:** 2 bước.
  1. **Xem trước** — user tải 1 file (xlsx, csv, pdf, docx, txt, md) ở trang Dự án → AI đọc và **chia thành các dự án**:
     tên, mô tả ngắn, hạn (nếu file có), phần nào của file (`source`, vd "Sheet Yêu cầu, dòng 2–14"), và `brief` = nội dung
     của riêng dự án đó. 1 lượt AI. Không ghi gì vào DB (trừ quota + AIUsageLog).
  2. **Tạo** — user sửa / bỏ bớt rồi bấm "Tạo dự án" → AI tạo các `Project` + mỗi dự án 1 cuộc chat dự án có sẵn tin đầu
     là `brief` → user vào từng dự án dùng luồng có sẵn (bàn bạc → Lên kế hoạch → Áp dụng & xếp lịch). Không gọi AI.
- **Tiêu chí xong:**
  - [x] AC1 — Xem trước: gói Free → 403 `plan_feature_locked` (như chat); hết lượt → 429; file không đọc được → 422 như FR-08b-2.
  - [x] AC2 — Xem trước trả 1–20 dự án, tên ≤ 150 ký tự và không trùng nhau, hạn quá khứ → bỏ hạn; `brief` ≤ 6000 ký tự;
    kèm `project_limit` / `projects_left` (null = không giới hạn) để UI cảnh báo trước.
  - [x] AC3 — Tạo: 1–20 dự án; tên rỗng / > 150 / trùng nhau, hạn quá khứ, brief rỗng → 422; vượt giới hạn gói → 409
    `project_limit` (không tạo dự án nào); tất cả trong 1 giao dịch.
  - [x] AC4 — Mỗi dự án: `Status Active`, `IsDefault false`, `StartDate` = hôm nay (giờ user), `Deadline` như gửi; 1 cuộc chat
    dự án (Open) + 1 tin của user = "Nội dung từ file <tên file> (<source>):" + brief. Trả `project_id`, `conversation_id`.
  - [x] AC5 — Tên khách / email / số điện thoại trong file được che trước khi gửi AI (`mask_contact`).
  - [x] AC6 — Chạy thật: 1 file Excel 3 dự án → xem trước (Gemini) → tạo → bàn + lên kế hoạch 1 dự án; dọn còn 0 dòng.
- **Ngoài phạm vi:** gắn khách hàng (`ClientId`) · lưu file vào thư viện tài liệu · tự lên kế hoạch cho mọi dự án cùng lúc.

## 1. Bên ngoài
| Method | Endpoint | Request | Response | Lỗi |
|---|---|---|---|---|
| POST | `/projects/import/preview` | multipart `file`, `language?` | `{file_name, projects: [{name, description, deadline, source, brief}], warnings, project_limit, projects_left}` | 401, 403, 409 `no_active_plan`, 422, 429, 502 |
| POST | `/projects/import` | `{file_name, projects: [{name, description?, deadline?, source?, brief}]}` | 201 `{projects: [{project_id, conversation_id, name}]}` | 401, 409 `project_limit`, 422 |

- DB ghi: `Project` (mới, chỉ INSERT), `ChatConversation`, `ChatMessage`; quota + `AIUsageLog` (Feature `Chat`) ở bước 1.
- Prompt mới `project_import_v1` + mẫu thử.

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Ai tạo dự án | AI (Hiếu cho phép), kiểm giới hạn như Backend | Một bước, không cần API Backend mới |
| 2 | Giữ bản xem trước ở đâu | Không lưu; bước 2 nhận lại danh sách đã sửa | Không thêm bảng |
| 3 | Gói Free | Không dùng được bước 1 (cần AI, như chat) | Free không có AI chat |
| 4 | Brief vào chat thế nào | Tin đầu tiên của user trong chat dự án | `pick_history` luôn giữ tin đầu (brief) |
| 5 | Feature trong AIUsageLog | `Chat` (DB chỉ cho Chat / Schedule / Briefing / Embedding) | Không đổi schema |

## 2. Bên trong
- domain `project_import.py`: giới hạn, `ImportedProject`, `check_preview` (AI), `check_import` (user), `projects_left`.
- application: port `project_import_repository.py`; `import_writer.py` (prompt + schema + kiểm); use case
  `preview_project_import.py`, `import_projects.py`.
- infrastructure `project_import_repository.py` (SQL).
- api `routes/project_import.py`, `deps.py`, `main.py`.

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | domain | kiểm xem trước / tạo: số lượng, tên, trùng, hạn, brief; số chỗ còn lại | đúng | AC2, AC3 |
| T2 | application | xem trước: Free 403, hết lượt, file hỏng, che liên hệ, charge + log, AI hỏng → 502 không trừ lượt | đúng | AC1, AC2, AC5 |
| T3 | application | tạo: vượt giới hạn → không tạo gì; đủ → dự án + chat + tin đầu | đúng | AC3, AC4 |
| T4 | infrastructure | SQL: đếm giống Backend, INSERT có lọc user, chat Scope Project | đúng | AC3, AC4 |
| T5 | api | 2 endpoint, mã lỗi | đúng | AC1–AC4 |
| T6 | chạy thật | như AC6 | ghi §5 | AC6 |

## 4. Các bước code
- [x] **1.** Domain + writer + prompt — T1.
- [x] **2.** Port + use case + fake — T2, T3.
- [x] **3.** SQL + API + wiring — T4, T5.
- [x] **4.** Chạy thật — T6; docs.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-11-20 | Tạo plan; luật mới cho bảng `Project` | Hiếu |
| 2026-11-20 | Chạy thật (Supabase + Gemini; user + gói test, xóa đúng các dòng đã tạo, còn 0): Excel 1 sheet, 4 dòng / 3 khách → xem trước **3 dự án** (website 2 dòng gộp 1 dự án, logo, blog), đúng hạn, `source` "Sheet Việc tháng 12, rows 2-3", brief giữ dòng tiêu đề cột → tạo: DB 3 dự án `Active`, không mặc định, `StartDate` hôm nay, đúng hạn, `AllowAiDocumentAccess` false, mỗi dự án 1 chat + 1 tin đầu. 3/3 kiểm tra PASS. Bước "lên kế hoạch từ chat" lỗi do **cả Gemini (504) lẫn Groq (429)** không trả lời (phía nhà cung cấp); theo quy tắc chỉ chạy lại riêng bước đó (chỉ LLM, tin đầu tạo bằng `first_message`): kế hoạch 2 cột mốc có lý do, đủ hạng mục của 2 dòng. Gói Standard của user test không giới hạn dự án nên chưa thấy 409 trên DB thật (đã có test) | Bước 4 |
| 2026-11-20 | Ghi nhận: tên khách nằm trong file (vd "Tiệm bánh Mai") vẫn tới AI, vì lúc nhập chưa biết khách nào để che; chỉ email / số điện thoại bị che. Tên dự án AI đặt cũng có thể chứa tên khách | Chạy thật → báo Hiếu |
