# FR-22b — Trợ lý giọng nói: chọn công cụ tại chỗ trước, chỉ gọi Gemini khi không chắc

Trạng thái: Xong · Branch: `feature/fr22b-local-router` · Cập nhật: 2026-11-11

<!-- Nháp do Claude Code soạn, chờ Hiếu duyệt. Lý do: Gemini gói Free hết quota trong ngày (FR-22 nhật ký
2026-11-04, 2026-11-10: 502 / 503) và user gói Free hết lượt AI sau vài câu → trợ lý chỉ còn trả lời
"hôm nay làm gì". Việc của trợ lý chỉ là chọn 1 trong 16 nhãn + lấy buổi / tên → không cần LLM cho câu quen.
Không thêm thư viện: phân loại bằng Python thuần. (?) = cần Hiếu chốt. -->

## 0. Mục tiêu
- **Làm gì:** Trước khi gọi Gemini, code tự chọn công cụ bằng cách so câu nói với ~300 câu mẫu (vi / en).
  Đủ chắc → chạy công cụ luôn, **không gọi Gemini**. Không đủ chắc (câu lạ, câu nối tiếp "còn ngày mai thì sao?",
  công cụ cần tên mà không tìm được tên) → đi đường cũ của FR-22 (Gemini chọn).
- **Ai gọi, khi nào:** không đổi — `POST /assistant/turn`.
- **Tiêu chí xong:**
  - [x] AC1 — Bộ câu kiểm tra riêng (không nằm trong câu mẫu, ≥ 60 câu vi + en, gồm 20 câu `assistant_router_samples.json`): tại chỗ trả lời **≥ 70%** số câu, và trong số đó chọn đúng **≥ 98%**; phần còn lại chuyển cho Gemini.
  - [x] AC2 — Yêu cầu ghi (bắt đầu timer, tạo / xoá task, duyệt lịch…) không bao giờ bị chọn thành công cụ đọc: hoặc `unsupported`, hoặc chuyển Gemini.
  - [x] AC3 — Lượt chọn tại chỗ: không gọi LLM, không ghi `AIUsageLog`, không trừ lượt (#2).
  - [x] AC4 — Hết lượt AI (429) / không có gói (409): câu chọn được tại chỗ **vẫn trả lời**; chỉ câu phải gọi Gemini mới báo lỗi như cũ.
  - [x] AC5 — Gemini lỗi (502 / 503 / hết quota Free): câu chọn được tại chỗ vẫn trả lời.
  - [x] AC6 — Thời gian chọn tại chỗ < 20 ms phía server.
  - [x] AC7 — Các tiêu chí FR-22 còn nguyên: câu trả lời từ mẫu, ≤ 60 từ, đọc đúng dữ liệu của user.
- **Ngoài phạm vi:** lệnh ghi (giai đoạn 2) · đổi nhà cung cấp LLM khi lỗi · các chức năng khác dùng Gemini (chat task / dự án, lý do xếp lịch) · học thêm từ câu của user (không lưu câu nói, #11 FR-22).

## 1. Bên ngoài — nói chuyện với ai

### API
Không đổi `POST /assistant/turn` (request / response như FR-22). Mobile không phải sửa.

### Database
- Đọc: như FR-22, thêm tên task chưa xong + tên dự án `Active` của user để tìm tên trong câu (#4) — đã có `open_tasks`, `projects` trong `AssistantRepository`.
- Ghi: `AIUsageLog` / `AiCallsUsed` **chỉ khi gọi Gemini** (#2).

### Vấn đề & quyết định
| # | Vấn đề | Quyết định đề xuất | Lý do |
|---|---|---|---|
| 1 | Cách phân loại | Python thuần: n-gram ký tự (3–5) + TF-IDF + cosine với câu mẫu; lấy nhãn của câu mẫu gần nhất; "chắc" khi điểm ≥ ngưỡng **và** hơn nhãn thứ hai một khoảng | Không thêm thư viện; câu ngắn, ~300 mẫu → nhanh; giải thích được (chỉ ra câu mẫu gần nhất) |
| 2 | Lượt chọn tại chỗ có trừ lượt AI không | **Chốt (Hiếu, 2026-11-11): không trừ** | CLAUDE.md §7: "No LLM call → no quota"; người dùng gói Free hỏi được nhiều hơn. Ngược lại FR-22 #7 / BR-87 coi mỗi câu chat = 1 lượt |
| 3 | Câu mẫu | File `prompts/assistant_router_examples.json` (nhãn + câu, vi / en, viết tay), tách khỏi bộ kiểm tra AC1 | Sửa câu mẫu không cần sửa code; bộ kiểm tra riêng để không tự chấm điểm trên bài đã học |
| 4 | Tên task / dự án (`similar_task_time`, `project_progress`, `upcoming_milestones`) | Tìm tên ngay trong câu: so khớp từng tên task / dự án của user với câu (bỏ dấu, `name_matching`); 1 tên rõ → dùng; nhiều / không có → chuyển Gemini (`upcoming_milestones` không tên = mọi dự án) | Tên do user đặt, không có trong câu mẫu |
| 5 | Buổi (`schedule_for`) | Luật từ khoá: sáng nay / chiều nay / tối nay / ngày mai / mai / this morning / this afternoon / tonight / tomorrow; không có → hôm nay | Như prompt FR-22 |
| 6 | Câu có lịch sử nối tiếp ("còn ngày mai thì sao?", "dự án đó") | Có lịch sử và câu ngắn / có "đó", "thì sao" → chuyển Gemini | Cần hiểu lượt trước |
| 7 | Câu ghi (AC2) | Nhãn `unsupported` có nhiều câu mẫu ghi; thêm luật: có động từ ghi (bắt đầu, dừng, tạo, xoá, sửa, duyệt, thanh toán / start, stop, create, delete, approve, pay) mà không phải câu hỏi → `unsupported` tại chỗ | An toàn: chỉ đọc, không bao giờ chạy lệnh |
| 8 | Ngưỡng | Chọn ở bước 3 trên bộ kiểm tra để đạt AC1 (ưu tiên đúng hơn là trả lời nhiều) | Sai thì user nghe câu trả lời lạc đề |

## 2. Bên trong — logic

### Code nằm ở đâu
- domain: `local_router.py` (chuẩn hoá chữ, n-gram, TF-IDF, cosine, chọn nhãn, buổi, luật câu ghi) · dùng lại `name_matching.py`.
- prompts: `assistant_router_examples.json` (câu mẫu) · `assistant_router_checks.json` (bộ kiểm tra AC1).
- application: `assistant_turn.py` — thử tại chỗ trước; chỉ kiểm quota + gọi Gemini khi cần.
- config: `ASSISTANT_LOCAL_MIN_SCORE`, `ASSISTANT_LOCAL_MIN_GAP` (chốt ở bước 3).

### Luồng `/assistant/turn` mới
1. Kiểm đầu vào (như cũ). Đọc múi giờ user.
2. **Tại chỗ:** câu ghi → `unsupported`; câu nối tiếp → bỏ qua; còn lại phân loại → nhãn + điểm. Công cụ cần tên → tìm tên trong câu (#4).
3. Đủ chắc → `ToolChoice` → chạy công cụ như FR-22, **không** kiểm / trừ quota (#2), `calls_left` = lượt còn lại hiện tại (đọc, không trừ).
4. Không đủ chắc → luồng FR-22 cũ: kiểm quota → Gemini → trừ 1 lượt → chạy công cụ.

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | domain | Chuẩn hoá chữ (dấu, hoa / thường, khoảng trắng), n-gram | đúng | — |
| T2 | domain | Leave-one-out trên câu mẫu (giả lập câu lạ): câu nào được chọn tại chỗ thì không được sai | 0 câu sai | AC1 |
| T3 | domain | Bộ kiểm tra AC1 | ≥ 70% trả lời tại chỗ, đúng ≥ 98% | AC1 |
| T4 | domain | 30 câu ghi vi / en | không câu nào thành công cụ đọc | AC2 |
| T5 | domain | Buổi + câu nối tiếp + tìm tên trong câu | đúng / chuyển Gemini | — |
| T6 | application | Câu chọn tại chỗ | LLM giả không được gọi, không trừ lượt, không `AIUsageLog` | AC3 |
| T7 | application | Hết lượt / không gói / LLM lỗi + câu tại chỗ | vẫn trả lời; câu cần Gemini → 429 / 409 / 502 như cũ | AC4, AC5 |
| T8 | chạy thật | `scripts/try_assistant.py` + bộ kiểm tra | ghi % tại chỗ, % đúng, thời gian vào mục 5 | AC1, AC6 |

## 4. Các bước code
- [x] **1.** `local_router.py` (chuẩn hoá, n-gram, TF-IDF, cosine) — T1.
- [x] **2.** Câu mẫu + bộ kiểm tra (json) — T2.
- [x] **3.** Chọn ngưỡng, luật câu ghi, buổi, câu nối tiếp, tìm tên — T3, T4, T5.
- [x] **4.** Nối vào `assistant_turn.py` + config — T6, T7.
- [x] **5.** Chạy thật — T8. Docs: `FR-22.md` nhật ký, `CLAUDE.md` §6.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do |
|---|---|---|
| 2026-11-11 | Tạo nháp | Hiếu: giải quyết giới hạn Gemini / lượt AI cho trợ lý |
| 2026-11-11 | Hiếu duyệt; #2 chốt: lượt chọn tại chỗ không trừ lượt AI | CLAUDE.md §7 |
| 2026-11-11 | Bước 3: chỉ n-gram + cosine thì leave-one-out có 20 câu "chắc mà sai" (vd. "Thời tiết hôm nay thế nào?" → `today_briefing` vì chung "hôm nay") → thêm **từ khoá bắt buộc** mỗi nhãn (`ANCHORS`); `unsupported` chỉ chọn tại chỗ khi câu không có từ khoá của công cụ đọc nào. T2 đổi từ "≥ 95% đúng nhãn" sang "câu chọn tại chỗ không sai câu nào" (đúng mục đích hơn). Ngưỡng chốt 0,45 / 0,05: leave-one-out tại chỗ 85/228, sai 0; bộ kiểm tra tại chỗ 62/70 (89%), đúng 100% | Ưu tiên đúng hơn trả lời nhiều (#8) |
| 2026-11-11 | Câu có động từ ghi mà là câu hỏi ("Sửa lỗi … mất bao lâu?" — tên task) → chuyển Gemini; câu ghi có "giúp / đi / nhé / được không" → `unsupported` tại chỗ | AC2 |
| 2026-11-11 | Bước 4: `execute` đọc múi giờ trước, thử tại chỗ, rồi mới kiểm quota + gọi Gemini; công cụ cần tên chỉ chọn tại chỗ khi tên khớp đúng 1 task / dự án. User không có gói hỏi lượt AI → "còn 0 lượt" (không phải "không giới hạn"). Config `ASSISTANT_LOCAL_ROUTER` (bật/tắt), `ASSISTANT_LOCAL_MIN_SCORE` 0,45, `ASSISTANT_LOCAL_MIN_GAP` 0,05. Router dựng 1 lần (~10 ms), mỗi câu trung vị 0,75 ms, p95 1,3 ms (AC6) | — |
| 2026-11-11 | Bước 5 (T8): `scripts/try_assistant.py` (sửa tên gói "Tiêu chuẩn" → "Pro", DB đã đổi tên gói; kiểm AC4 theo luật mới): **20/20 đúng, 18 câu chọn tại chỗ (không trừ lượt), 2 câu qua Gemini** ("landing page" có 2 dự án → Gemini → `clarify`; "Còn ngày mai thì sao?" cần lịch sử); AIUsageLog 2 dòng = AiCallsUsed 2; p95 3,1 s; dữ liệu thử xoá, 0 dòng còn lại. 401 ở 3 công cụ gọi Backend vẫn do khoá JWT máy dev (đã biết) | — |
