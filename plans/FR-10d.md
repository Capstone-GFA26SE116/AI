# FR-10d — Học sức làm mỗi ngày, cảnh báo ngày quá tải

Trạng thái: Xong · Branch: `feature/fr10d-daily-capacity` · Cập nhật: 2026-11-19

<!-- Hiếu giao 2026-11-19 sau FR-10c: không có trần giờ cố định (freelancer có lúc làm 12–14 h);
hệ thống học "ngày bận bạn thường làm được bao nhiêu" từ dữ liệu bấm giờ, chỉ CẢNH BÁO, không chặn.
Claude Code soạn plan rồi làm liền. -->

## 0. Mục tiêu
- **Làm gì:** Từ các phiên bấm giờ ~60 ngày gần đây, tính **sức làm mỗi ngày** của người dùng = mức của một
  ngày bận (phân vị 80 % số phút làm thật mỗi ngày có làm). Khi đề xuất lịch làm một ngày có tổng việc
  (slot mới + slot đã duyệt) vượt mức đó → thêm cảnh báo `day_overloaded`. Không chặn, không đổi slot.
- **Tiêu chí xong:**
  - [x] AC1 — Có ≥ 5 ngày có làm → sức làm = phân vị 80 %, làm tròn xuống bội 15 phút.
  - [x] AC2 — < 5 ngày → không có sức làm, không cảnh báo (khung giờ làm là giới hạn duy nhất, như nay).
  - [x] AC3 — Chỉ tính phiên đã kết thúc và đã xác nhận (`IsConfirmed`), của chính user, trước hôm nay.
  - [x] AC4 — Ngày vượt mức → cảnh báo có ngày, số phút việc, sức làm; câu vi / en dễ hiểu, không tên nội bộ.
  - [x] AC5 — `is_feasible`, slot, quota không đổi; câu tóm tắt nhắc số ngày quá tải.
- **Ngoài phạm vi:** dùng sức làm để chọn ngày khi xếp (FR-10c đã rải đều) · hiện sức làm trên Dashboard.

## 1. Bên ngoài
- DB đọc thêm: `FocusSession` (`StartedAt`, `EndedAt`) của user, đã xác nhận, kết thúc trong 60 ngày qua.
- `ScheduleProposal.Warnings` / API: phần tử mới `type = "day_overloaded"`, `task_id = null`,
  `day` (YYYY-MM-DD), `needed_minutes` = phút việc ngày đó, `capacity_minutes`, `message`.
  Backend đọc `Type` dạng chuỗi và bỏ qua trường lạ → không vỡ; `message` hiện được ngay.
- Hằng số trong `domain/daily_capacity.py` (không đưa ra config): `LOOKBACK_DAYS` 60, `MIN_DAYS` 5, `MIN_DAY_MINUTES` 60.

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Lấy số nào làm "sức làm" | Phân vị 80 % các ngày có làm | Ngày bận nhưng không phải ngày cực đoan nhất |
| 2 | Phiên qua nửa đêm | Tính vào ngày bắt đầu (giờ của user) | Đơn giản, ít gặp |
| 3 | Chưa đủ dữ liệu | Không cảnh báo | Không đoán bừa; khung giờ làm vẫn chặn |
| 4 | Phiên AutoTimeout chưa xác nhận | Bỏ | Số liệu có thể sai (giống FR-17) |

## 2. Bên trong
- domain `daily_capacity.py`: `minutes_by_day`, `daily_capacity`, `overloaded_days`.
- domain `models.py`: `WarningType.DAY_OVERLOADED`; `ScheduleWarning` thêm `day`, `capacity_minutes`.
- domain `schedule_text.py`: câu cảnh báo + câu tóm tắt.
- application `propose_schedule.py`: tính sức làm từ `PlanningData.recent_sessions`, thêm cảnh báo.
- infrastructure `schedule_repository.py`: đọc phiên gần đây; `warnings_json` thêm `day`, `capacity_minutes`.
- api `schedule.py`: `WarningResponse` thêm 2 trường.

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | domain | 10 ngày có làm → phân vị 80 %, tròn xuống 15' | đúng | AC1 |
| T2 | domain | 4 ngày → None | đúng | AC2 |
| T3 | domain | phút theo ngày: 2 phiên 1 ngày cộng dồn; theo giờ user; bỏ hôm nay | đúng | AC3 |
| T4 | domain | ngày vượt (slot mới + đã duyệt) → cảnh báo; ngày chỉ có slot đã duyệt → không | đúng | AC4 |
| T5 | domain | câu vi / en, giờ dạng "8 giờ 30 phút" | đúng | AC4 |
| T6 | application | use case thêm cảnh báo, `is_feasible` không đổi | đúng | AC5 |
| T7 | infrastructure | SQL lọc user, `IsConfirmed`, có `EndedAt`, trong 60 ngày; JSON cảnh báo có `day` | đúng | AC3, AC4 |
| T8 | chạy thật | (đọc DB) tính sức làm cho user có dữ liệu bấm giờ | ghi §5 | AC1 |

## 4. Các bước code
- [x] **1.** Domain sức làm + cảnh báo + câu chữ — T1–T5.
- [x] **2.** Repository + use case + API + config — T6, T7.
- [x] **3.** Chạy thật (chỉ đọc) — T8; docs.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-11-19 | Tạo plan | Hiếu: không trần giờ, chỉ học và cảnh báo |
| 2026-11-20 | Ngưỡng là hằng số trong domain thay vì config | Ít dây nối; đổi khi có dữ liệu thật |
| 2026-11-20 | Chạy thật (chỉ SELECT) 5 user có nhiều phiên bấm giờ nhất: phiên chủ yếu là thử nghiệm (0–35'/ngày), nhiều nhất 4 ngày có làm → chưa ai có sức làm → không cảnh báo (đúng AC2). Lộ rủi ro: đủ 5 ngày "bấm thử" sẽ học ra 15' và cảnh báo mọi ngày → thêm `MIN_DAY_MINUTES = 60`: chỉ ngày làm ≥ 60' mới tính. `pytest` 2055 pass | Bước 3 |
