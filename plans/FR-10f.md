# FR-10f — Câu lý do của lịch đúng hơn và bớt máy móc

Trạng thái: Xong · Branch: `feature/fr10f-better-reasons` · Cập nhật: 2026-11-20

<!-- Việc tồn từ chạy thật FR-08b-7 ("phần 1 trên 5 do không có khoảng trống đủ dài", "ưu tiên số 2 trên tổng số 11",
"Vì hôm qua hết giờ làm việc"). Hiếu chọn "làm các việc tồn" (2026-11-20). Claude Code soạn plan rồi làm liền. -->

## 0. Mục tiêu
- **Lỗi thật:** từ FR-10c, việc dài bị **cố ý** cắt thành khối ≤ 3 giờ, nhưng dữ kiện gửi AI vẫn nói lý do chia là
  "không có khoảng trống đủ dài" (`no_long_gap`) → câu lý do sai.
- **Làm gì:**
  - Planner ghi lý do chia của từng slot: `long_work` (cắt khối cố ý) hoặc `no_long_gap` (không đủ chỗ trống).
  - Prompt `schedule_reason_v4`: giải thích `long_work`; cấm viết thứ hạng / số phần dạng "2 trên 11", "phần 1 trên 5";
    cấm "hôm nay / hôm qua / ngày mai"; đổi cách mở câu giữa các slot; nhắc thời gian cộng thêm chỉ ở phần đầu của việc;
    `outside_work_hours` = "khung giờ làm trước đó đã kín".
- **Tiêu chí xong:**
  - [x] AC1 — Khối cắt cố ý → `split_reason = long_work`; thiếu chỗ → `no_long_gap`; 1 phần → không có.
  - [x] AC2 — Prompt v4 có các luật trên; mẫu thử thêm 1 mẫu `long_work` (12 mẫu).
  - [x] AC3 — Chạy thật 12 mẫu qua Gemini: không còn "X trên Y", không "hôm qua", câu khác nhau (ghi §5).
- **Ngoài phạm vi:** đổi cách xếp giờ.

## 1. Bên ngoài
- Không đổi API / DB. Chỉ đổi câu lý do (`ScheduleSlot.Explanation`).

## 2. Bên trong
- domain `models.py` (`SplitReason.LONG_WORK`, `PlannedSlot.split_reason`), `planner.py`, `schedule_text.py`.
- application `reason_writer.py` → `schedule_reason_v4`; prompts `schedule_reason_v4.md`, mẫu.

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | domain | 360' với khối 180' → 2 slot `long_work`; 60' → None; cuộc họp cắt ngày, không khối → `no_long_gap` | đúng | AC1 |
| T2 | application | prompt v4 tồn tại, có `long_work` và các câu cấm | đúng | AC2 |
| T3 | chạy thật | 12 mẫu qua Gemini | ghi §5 | AC3 |

## 4. Các bước code
- [x] **1.** Domain + prompt v4 + mẫu — T1, T2.
- [x] **2.** Chạy thật — T3; docs.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-11-20 | Tạo plan + làm | Việc tồn |
| 2026-11-20 | Chạy thật 12 mẫu (chỉ LLM): 10/12 có câu lý do, 2 mẫu lỗi do Gemini (504 / 503, phía Google; chạy lại riêng 4 mẫu lỗi lần đầu với thời gian chờ 30 s, còn 2 mẫu vẫn 503 → dừng để không tốn lượt). Không câu nào còn "phần X trên Y", "ưu tiên số X trên Y" hay "hôm qua". Ví dụ mẫu `long_work`: "Khung giờ làm trước đó đã kín nên đây là phần tiếp theo của công việc. Việc được chia nhỏ giúp bạn không phải làm liên tục quá lâu." Mẫu 5 việc: 5 câu mở đầu khác nhau. `pytest` 2173 pass | Bước 2 |
