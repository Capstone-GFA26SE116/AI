# FR-08c — Nhập kế hoạch từ Excel

Trạng thái: Xong trên máy · Branch: `feature/fr08c-excel-plan` · Cập nhật: 2026-11-27

## 0. Mục tiêu

- [x] AC1. Đọc workbook .xlsx theo ba sheet Projects / Milestones / Tasks (có tên tiếng Việt tương ứng trong mẫu). Giữ mã, tên, hạn, thời lượng và dòng nguồn. Dòng thiếu, giá trị sai và mã trùng được đưa vào bản xem trước để sửa hoặc bỏ.
- [x] AC2. API xác nhận kiểm tra lại dữ liệu, mã cha, domain và giới hạn gói. Tạo Project, Milestone, Task trong một giao dịch, có thể thử lại cùng bản xác nhận sau lỗi mạng.
- [x] AC3. Luồng Excel có cấu trúc dùng parser, áp dụng cho mọi gói trong giới hạn dự án. Luồng brief cũ giữ nguyên.
- Ngoài phạm vi: nhận diện cột tự do bằng LLM, subtasks, ghi lịch và OCR.

## 1. Bên ngoài

| Method | Endpoint | Request | Response |
|---|---|---|---|
| POST | `/projects/import/plan/preview` | multipart file | rows {projects, milestones, tasks}, domains, issues, project_limit, projects_left |
| POST | `/projects/import/plan` | request_id (UUID), projects, milestones, tasks | projects (id/name), milestone_count, task_count |

Mẫu: Projects (`project_code,name,description,deadline`), Milestones
(`project_code,milestone_code,name,deadline`), Tasks
(`project_code,milestone_code,name,estimated_minutes,domain,deadline`).
Cho dùng sheet `Danh sách dự án`, `Cột mốc`, `Công việc chi tiết` với cùng header.
Mã cột mốc chỉ cần duy nhất trong từng dự án. Hạn ngày Excel hoặc ISO; task có thể kế thừa
hạn cột mốc rồi dự án. Mỗi task cần domain đang hoạt động và số phút nguyên dương.
Mã dạng chữ được giữ nguyên và so khớp không phân biệt hoa thường. Mã số nên định dạng Text
nếu cần số 0 đầu. Tiêu đề tối đa theo schema hiện hành.
Các ô đầu vào phải là giá trị; công thức cần dán thành giá trị trước khi nhập.

## 2. Bên trong

- Parser openpyxl read-only. Tối đa 20 MB khi tải lên, 100 MB sau giải nén, 5.000 dòng dữ liệu mỗi sheet và 64 cột. Tối đa 20 dự án mỗi lần xác nhận.
  Từ chối sheet vượt giới hạn hoặc sai header thay vì trả kết quả thiếu.
- Domain kiểm dữ liệu độc lập; API xác nhận dùng lại bộ kiểm tra của preview. Task import
  là Flexible/ToDo, estimate lấy từ Excel, AiEstimatedMinutes rỗng.
- Dùng các quyền INSERT hiện có với Project, Milestone, Task. Giữ các cột và trạng thái
  đang dùng trong luồng áp dụng bản kế hoạch. Không đổi schema.
- Khóa theo user và kiểm quota dự án trước khi ghi. ID dự án xác định từ user, request ID
  và nội dung xác nhận. Retry cùng payload trả lại các ID. Nếu chỉ còn một phần
  dự án của lần nhập trước, trả 409. FE cấp ID mới khi sửa bản xác nhận.

## 3. Test

- Parser: file nhiều sheet, Excel date, các dòng rỗng và sheet có header sai.
- Validator: cùng mã cột mốc khác dự án, task mồ côi, mã trùng, hạn kế thừa và domain.
- Use case: giới hạn gói, ghi theo cây, retry cùng yêu cầu, lỗi giữa giao dịch.
- API: xác thực, multipart preview, xác nhận và ánh xạ mã lỗi.

## 4. Các bước code

- [x] 1. Domain và parser với unit test.
- [x] 2. Port, use case, SQL và API, kèm unit test.
- [x] 3. Chạy pytest và ruff, bàn giao hợp đồng cho FE.

## 5. Nhật ký thay đổi

- 2026-11-27: Thêm đường nhập kế hoạch XLSX ba sheet theo MVP đã trao đổi.

- 2026-11-27: Pytest đạt 2.637 test, coverage 96,04%, ruff đạt. PostgreSQL cục bộ với schema rút gọn xác nhận nối đúng cây và hai request đồng thời cùng payload trả về cùng ID.

## Luồng nhập brief hiện hữu

### Mục tiêu cũ

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
