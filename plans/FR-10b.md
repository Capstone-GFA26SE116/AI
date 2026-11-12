# FR-10b — Xếp lịch tránh giờ nghỉ trưa (W3-01)

Trạng thái: Xong · Branch: `feature/fr10b-lunch-break` · Cập nhật: 2026-11-12

<!-- Hiếu chốt 2026-11-11: config chung 12:00–13:00 cho mọi user (Backend chưa có cột giờ nghỉ trưa riêng);
Claude Code soạn plan rồi làm liền. Sheet W3-01 Problem: "Khi xếp lịch thì tránh giờ nghỉ trưa". -->

## 0. Mục tiêu
- **Làm gì:** FR-10 coi giờ nghỉ trưa của mỗi ngày làm việc là giờ bận → không xếp slot vào đó (kể cả phần chia nhỏ).
  FR-16b (lố giờ → dời) cũng không dời việc vào giờ nghỉ, nhưng slot đang lố vẫn được kéo dài qua trưa.
- **Tiêu chí xong:**
  - [x] AC1 — `SCHEDULE_LUNCH_BREAK` mặc định `12:00-13:00`; `none` / `off` → tắt; giá trị sai → app không khởi động.
  - [x] AC2 — FR-10: không slot nào chồng giờ nghỉ trưa của bất kỳ ngày làm việc nào trong cửa sổ 7 ngày; cuối tuần theo `WorksOnWeekend`.
  - [x] AC3 — FR-16b: slot bị dời không rơi vào giờ nghỉ; slot đang lố được kéo dài qua trưa (user đang làm).
  - [x] AC4 — `pytest` xanh; không đổi API.
- **Ngoài phạm vi:** giờ nghỉ riêng từng user (chờ Backend thêm cột — `docs/open-questions.md`).

## 1. Code
- domain `free_time.py`: `DailyBreak`, `break_ranges(hours, now, window_days, breaks)`.
- `propose_schedule.py`: thêm `break_ranges` vào giờ bận khi tính giờ trống (không đưa vào dữ kiện cho LLM).
- `overrun_plan.py` / `plan_overrun.py`: `breaks` chỉ dùng khi đặt lại slot bị dời.
- `config.py`: `SCHEDULE_LUNCH_BREAK`, `lunch_breaks()`; `deps.py` truyền vào FR-10 và FR-16b.

## 2. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do |
|---|---|---|
| 2026-11-12 | Xong: `pytest` 1896 (+8). Biến môi trường rỗng bị bộ đọc config bỏ qua → dùng `none` / `off` để tắt | W3-01 |
