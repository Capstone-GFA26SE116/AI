# FR-10e — Báo việc hạn xa chưa xếp hết (ngoài cửa sổ đề xuất)

Trạng thái: Xong · Branch: `feature/fr10e-beyond-window` · Cập nhật: 2026-11-20

<!-- Thấy khi chạy thật FR-08b-7: đề xuất chỉ phủ SCHEDULE_WINDOW_DAYS (7) ngày → "Xếp được 8/11 task" mà
không cảnh báo gì. Hiếu: "ok làm tiếp đi" (2026-11-20) → chọn cách an toàn: giữ cửa sổ 7 ngày, chỉ thêm cảnh báo.
Claude Code soạn plan rồi làm liền. -->

## 0. Mục tiêu
- **Làm gì:** Việc có hạn **sau** cửa sổ đề xuất mà chưa xếp đủ → cảnh báo mới `beyond_window` (mỗi việc 1 dòng): hạn,
  số phút còn chưa xếp, "bấm Xếp lịch lại sau". Câu tóm tắt nhắc số việc này. Cách xếp **không đổi**.
- **Tiêu chí xong:**
  - [x] AC1 — Việc hạn sau cửa sổ, xếp thiếu → `beyond_window` có `task_id`, `needed_minutes` = phút chưa xếp, `day` = ngày hạn.
  - [x] AC2 — Xếp đủ trong cửa sổ → không cảnh báo. Việc hạn trong cửa sổ mà thiếu → vẫn `not_enough_time` như cũ.
  - [x] AC3 — `is_feasible` / `missing_minutes` không đổi (việc này không trễ, chỉ chưa tới lượt).
  - [x] AC4 — Câu vi / en, không tên nội bộ.
- **Ngoài phạm vi:** nới cửa sổ tới hạn dự án (cần Hiếu quyết) · tự xếp phần còn lại.

## 1. Bên ngoài
- `Warnings` (API + `ScheduleProposal.Warnings`): thêm `type = "beyond_window"`. Backend đọc `Type` dạng chuỗi → không vỡ.
  Web / Mobile hiện `message` như các cảnh báo khác.

## 2. Bên trong
- domain `models.py` (`WarningType.BEYOND_WINDOW`), `planner.py` (thêm cảnh báo), `schedule_text.py` (câu + tóm tắt).

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | domain | 1200' hạn sau cửa sổ 2 ngày → 720' xếp, 1 cảnh báo 480' | đúng | AC1 |
| T2 | domain | 60' hạn sau cửa sổ → không cảnh báo | đúng | AC2 |
| T3 | domain | câu vi / en + tóm tắt | đúng | AC4 |
| T4 | domain | giá trị enum ổn định | đúng | AC1 |

## 4. Các bước code
- [x] **1.** Domain + test — T1–T4. (Không chạy thật: chỉ đổi code thuần, không đổi SQL / LLM.)

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-11-20 | Tạo plan + làm. Test T19 cũ ("không cảnh báo") đổi thành "có cảnh báo beyond_window" | Quyết định mới |
