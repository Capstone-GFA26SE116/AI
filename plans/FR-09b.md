# FR-09b — Task tương tự kèm lý do dừng / trễ (W2-14)

Trạng thái: Xong · Branch: `feature/fr09b-stop-reasons` · Cập nhật: 2026-11-11

<!-- Hiếu chốt cách làm 2026-11-11: Claude Code soạn plan rồi làm liền (không dừng duyệt từng bước).
Sheet W2-14: "Mở task thì thấy các task tương tự đã làm và lý do trễ". FR-09 trả task tương tự nhưng chưa có lý do. -->

## 0. Mục tiêu
- **Làm gì:** Mỗi task tương tự trong `GET /memory/similar` có thêm `stop_reasons`: số phiên bấm giờ dừng vì
  bị gián đoạn / bị chặn / lý do khác, và tối đa 2 ghi chú gần nhất của user (đã che liên hệ).
  Frontend / Mobile hiện cạnh tỷ lệ "thường lâu gấp X lần" để user biết **vì sao** lần trước bị trễ.
- **Ai gọi, khi nào:** như FR-09 (mở task).
- **Tiêu chí xong:**
  - [x] AC1 — `stop_reasons` đếm đúng phiên đã kết thúc theo `StopReason` (`Interrupted`, `Blocked`, `Other`; `Completed` không đếm).
  - [x] AC2 — Ghi chú: chỉ phiên có `Note` khác rỗng, mới nhất trước, bỏ trùng, tối đa 2, ≤ 150 ký tự (cắt "…"), đã che email / số điện thoại / tên khách hàng của dự án đó (`mask_contact`).
  - [x] AC3 — Chỉ đọc phiên của task thuộc user trong JWT; 1 truy vấn cho mọi task trong kết quả.
  - [x] AC4 — Không gọi LLM, không tốn lượt; FR-09 còn nguyên (thứ tự, điểm, tỷ lệ).
- **Ngoài phạm vi:** viết câu "lý do trễ" bằng LLM · lý do từ nguồn khác (comment, chat).

## 1. Bên ngoài
### API — thêm vào mỗi phần tử `items` của `GET /memory/similar`
```json
"stop_reasons": { "interrupted": 2, "blocked": 1, "other": 0,
                  "notes": ["Khách đổi yêu cầu giữa chừng", "Chờ duyệt thiết kế"] }
```
### Database
- Đọc: `FocusSession` (`TaskId`, `StopReason`, `Note`, `EndedAt`) join `Task` → `Project` (lọc `UserId`) → `Client` (tên để che).
- Ghi: không.

## 2. Bên trong
- domain `similar_tasks.py`: `StopReasons`, `summarize_stop_reasons(...)` (đếm + chọn ghi chú, dùng `mask_contact` của FR-19).
- port `similar_task_repository.py`: `stop_reasons(user_id, task_ids)` → phiên đã kết thúc của các task đó, mới nhất trước.
- use case `find_similar_tasks.py`: sau khi gộp RRF, đọc 1 lần cho các task trong kết quả.
- api `routes/memory.py`: thêm `stop_reasons`.

## 3. Test
| ID | Tầng | Tình huống | Mong đợi | AC |
|---|---|---|---|---|
| T1 | domain | đếm 3 loại, `Completed` bỏ qua | đúng | AC1 |
| T2 | domain | ghi chú rỗng / trùng / dài / có email + tên khách | 2 ghi chú, đã che, cắt "…" | AC2 |
| T3 | application | fake repo: lý do gắn đúng task; không kết quả → không đọc | đúng | AC3 |
| T4 | infrastructure | SQL lọc `UserId`, `EndedAt IS NOT NULL`, `= ANY(:task_ids)` | đúng | AC3 |
| T5 | api | response có `stop_reasons` | đúng | AC4 |

## 4. Các bước code
- [x] **1.** Domain + T1, T2.
- [x] **2.** Port + SQL + fake + T3, T4.
- [x] **3.** Route + T5; docs.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do |
|---|---|---|
| 2026-11-11 | Tạo plan, Hiếu cho làm liền | W2-14 |
| 2026-11-11 | Xong: `pytest` 1845 (+8), SQL chạy chỉ đọc trên phiên thật (đọc đúng lý do). Lý do không biết → bỏ qua, không đoán. Frontend / Mobile cần hiện `stop_reasons` | — |
