# Lịch sử chat — danh sách mọi cuộc chat của user, tìm theo nội dung

Trạng thái: Xong · Branch: `feature/chat-history` · Cập nhật: 2026-11-20

<!-- Hiếu giao 2026-11-20 ("Làm luôn đi"): trang "Lịch sử chat" kiểu Claude / ChatGPT / Gemini, nhưng giữ quyết định A
(mỗi dự án 1 cuộc chat): không có "chat mới" ở đây, bấm vào thì mở đúng màn chat của dự án / task. Claude Code soạn plan rồi làm liền. -->

## 0. Mục tiêu
- **Làm gì:** Một endpoint chỉ đọc trả về các cuộc chat của user (chat dự án FR-08b và chat task FR-08a), mới nhất
  trước, có ô tìm theo nội dung tin nhắn / tên dự án / tên task. Không gọi LLM, không tốn quota.
- **Ai gọi, khi nào:** Web / Mobile mở trang "Lịch sử chat" hoặc gõ vào ô tìm.
- **Tiêu chí xong:**
  - [x] AC1 — Chỉ chat của chính user (lọc `Project.UserId`); chat chưa có tin nào thì không hiện.
  - [x] AC2 — Mỗi dòng: loại (`project` / `task`), dự án (id, tên), task (id, tên) nếu là chat task, trạng thái,
    `applied` (đã từng áp dụng bản nháp nào chưa), số tin, thời gian + câu xem trước của tin cuối (≤ 120 ký tự, theo giờ user).
  - [x] AC3 — Sắp theo tin cuối mới nhất; phân trang `limit` (1–50, mặc định 20) + `offset` (0–1000); `has_more`.
  - [x] AC4 — `q` (≤ 100 ký tự, khoảng trắng gom lại): không phân biệt hoa thường; khớp nội dung tin **hoặc** tên dự án /
    task / tiêu đề chat. Khớp trong tin → có `match` (id tin, thời gian, đoạn trích quanh chữ khớp) để mở đúng chỗ.
    `%` `_` `\` trong `q` là chữ thường, không phải ký tự đại diện. `q` rỗng = không lọc.
  - [x] AC5 — `q` quá dài / `limit`, `offset` ngoài khoảng → 422.
- **Ngoài phạm vi:** tìm không dấu ("ke hoach" ↔ "kế hoạch": DB chưa có extension `unaccent`) · xóa / đổi tên / ghim
  cuộc chat · tạo chat mới từ trang này · tìm trong tài liệu đính kèm.

## 1. Bên ngoài

### API
| Method | Endpoint | Request | Response | Lỗi |
|---|---|---|---|---|
| GET | `/chat/conversations` | `q?`, `limit?`, `offset?` | `{items: [...], has_more}` | 401, 422 |

Một phần tử `items`:
```json
{"conversation_id": "…", "scope": "project", "title": "…", "status": "applied", "applied": true,
 "project": {"id": "…", "name": "Website tiệm bánh"}, "task": null,
 "message_count": 9, "last_message": {"role": "assistant", "preview": "Thêm blog tốn khoảng…", "created_at": "…+07:00"},
 "match": {"message_id": "…", "snippet": "…khách vừa xin thêm trang blog 3 bài…", "created_at": "…"}}
```
Mở cuộc chat: `scope = project` → `GET /chat/conversations/{conversation_id}`; `scope = task` → `GET /chat/tasks/{task.id}`.

### Database
- Đọc: `ChatConversation`, `ChatMessage`, `Project` (lọc `UserId`), `Task` (tên), `User.TimeZone`. Không ghi gì.
- Index sẵn có: (`ConversationId`, `CreatedAt`) cho tin cuối. Tìm bằng `ILIKE` (dữ liệu mỗi user nhỏ; `pg_trgm` có sẵn nếu sau này cần).

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Đường dẫn | `GET /chat/conversations` | Cạnh `GET /chat/conversations/{id}`; không đụng `/chat/tasks/{task_id}` |
| 2 | Có nút "chat mới" như ChatGPT? | Không | Quyết định A: mỗi dự án 1 cuộc chat |
| 3 | Phân trang | `limit` + `offset` | Danh sách nhỏ, dễ dùng cho Web / Mobile |
| 4 | Tìm không dấu | Ngoài phạm vi | Cần `unaccent` (Backend sở hữu schema) |
| 5 | "Đã áp dụng" | `IsApplied` (dòng cũ, chat task) hoặc `ProposalJson.applied_at` (FR-08b-4) | Như FR-08b-4 |

## 2. Bên trong
- domain `chat_history.py`: `clean_query`, `preview`, `snippet` (đoạn trích quanh chữ khớp, không phân biệt hoa thường).
- application port `chat_history_repository.py`: `ChatSummary`, `ChatHistoryRepository.search(...)`, `time_zone(...)`.
- application use case `list_chat_history.py`: kiểm `q` / `limit` / `offset`, lấy `limit + 1` dòng để biết `has_more`,
  làm câu xem trước + đoạn trích.
- infrastructure `chat_history_repository.py`: 1 câu SQL (LATERAL tin cuối, tin khớp mới nhất, số tin + đã áp).
  Thoát `%` `_` `\` trong `q`.
- api `routes/chat_history.py` + `deps.py` + `main.py`.

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | domain | `clean_query`: rỗng / khoảng trắng → None; gom khoảng trắng; > 100 → lỗi | đúng | AC4, AC5 |
| T2 | domain | `preview` cắt 120 ký tự + "…", gom xuống dòng; `snippet` quanh chữ khớp (đầu / giữa / cuối, hoa thường) | đúng | AC2, AC4 |
| T3 | application | `limit + 1` dòng → `has_more`; preview + snippet; giờ theo user; giới hạn `limit` / `offset` → lỗi | đúng | AC2–AC5 |
| T4 | infrastructure | SQL lọc `UserId`, bỏ chat không có tin, sắp theo tin cuối, đã áp từ 2 nguồn; thoát `%` `_` `\` | đúng | AC1, AC4 |
| T5 | api | 200 + shape; `q` dài / `limit` 0 → 422; không token → 401 | đúng | AC2–AC5 |
| T6 | chạy thật | `EXPLAIN` + chạy SELECT trên DB thật cho 1 user có chat (chỉ đọc) | ghi §5 | AC1–AC4 |

## 4. Các bước code
- [x] **1.** Domain — T1, T2.
- [x] **2.** Port + use case — T3.
- [x] **3.** Repository SQL — T4.
- [x] **4.** API + wiring — T5.
- [x] **5.** Chạy thật (chỉ đọc) — T6; docs.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-11-20 | Tạo plan | Hiếu: thêm trang lịch sử chat |
| 2026-11-20 | Route test đọc `/openapi.json` thay vì `app.routes` (bản FastAPI này không trải route của router con ra `app.routes`) | Test |
| 2026-11-20 | Chạy thật (chỉ đọc; `EXPLAIN` 2 câu SQL hợp lệ): user có nhiều chat nhất có 21 cuộc chat, 12 có tin → trang 1 (limit 5) ra 5 dòng mới nhất, `has_more` = true, đủ chat dự án + chat task, `applied` đúng; tìm 1 chữ ra đoạn trích đúng; chữ có `%` `_` không khớp bừa (0 dòng). `ILIKE` trên DB (collation `en_US.UTF-8`) không phân biệt hoa thường cả chữ có dấu ("ÔN THI" khớp "ôn thi", "đề" khớp "Đề"). `pytest` 2115 pass | Bước 5 |
