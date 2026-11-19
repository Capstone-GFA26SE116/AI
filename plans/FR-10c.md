# FR-10c — Xếp lịch không dồn cục: rải đều tới deadline, không ngồi một việc quá lâu

Trạng thái: Xong (chưa chạy thật với DB) · Branch: `feature/fr10c-spread-schedule` · Cập nhật: 2026-11-19

<!-- Hiếu giao 2026-11-19: "đừng dồn nó 1 cục... sáng làm task này, tối làm task khác". Không giới hạn cứng
giờ / ngày (freelancer có lúc làm 12–14 h). Đổi việc: ưu tiên khác dự án, không có thì khác loại việc.
Luật "học sức làm mỗi ngày" + cảnh báo quá tải là bước sau (FR-10d). Claude Code soạn plan rồi làm liền. -->

## 0. Mục tiêu
- **Làm gì:** Bộ xếp lịch FR-10 thôi dồn việc vào những ngày đầu:
  1. **Rải đều:** mỗi việc vào **ngày đang ít việc nhất** trước deadline (cùng mức → ngày sớm hơn).
     Ngày deadline chỉ dùng khi các ngày trước không còn chỗ.
  2. **Không ngồi một việc quá lâu:** việc dài hơn `SCHEDULE_MAX_BLOCK_MINUTES` (mặc định 180) chia thành các khối
     đều nhau ≤ 180 phút (mỗi khối ≥ 30 phút).
  3. **Đổi việc:** hai slot liền nhau tránh cùng **dự án**; nếu chỉ còn việc cùng dự án thì tránh cùng **loại việc**.
     Là luật mềm: không còn chỗ nào khác thì vẫn xếp liền.
- **Không đổi:** thứ tự xếp (deadline gần trước), giữ task Cố định, giờ nghỉ trưa, buffer, chỉ xếp trong giờ làm,
  không kịp deadline → không xếp + cảnh báo, ưu tiên giờ làm hiệu quả **trong ngày đã chọn**, K nhân 1 lần.
- **Tiêu chí xong:**
  - [x] AC1 — 2 việc 3 h, còn 3 ngày → 2 ngày khác nhau (trước: cùng 1 ngày).
  - [x] AC2 — Việc 5 h → 2 khối 150 phút; không khối nào > 180 phút.
  - [x] AC3 — Cùng ngày có việc dự án A và B: không xếp 2 khối dự án A liền nhau khi có cách khác.
  - [x] AC4 — Deadline gấp: vẫn xếp đủ (dồn được) như cũ; không đủ chỗ → cảnh báo như cũ.
  - [x] AC5 — Tắt 2 luật (tham số mặc định của planner) → kết quả y hệt cũ: test FR-10 / FR-10b cũ pass nguyên.
- **Ngoài phạm vi:** giới hạn giờ / ngày, học sức làm, cảnh báo quá tải (FR-10d) · đổi thứ tự ưu tiên.

## 1. Bên ngoài
- API, DB ghi: không đổi. DB đọc thêm `Task.ProjectId` (đã có, để biết dự án của việc).
- Config mới: `SCHEDULE_SPREAD` (bool, mặc định true), `SCHEDULE_MAX_BLOCK_MINUTES` (mặc định 180, 0 = tắt).
- Lý do slot (LLM) không đổi.

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | "Ít việc" tính thế nào | Phút việc của đề xuất này + slot đã duyệt trong ngày | Họp / lịch Google đã làm giảm giờ trống; chỉ đếm việc |
| 2 | Rải đều có làm trễ không | Việc gần deadline xếp trước; ngày deadline để cuối cùng | Còn khoảng dự phòng trước hạn |
| 3 | Đổi việc khi chỉ có 1 dự án | Tránh cùng loại việc; vẫn không được thì cho liền | Luật mềm, không làm hỏng lịch |
| 4 | Test cũ | 2 luật là tham số, mặc định tắt trong planner; use case bật theo config | AC5, sửa ít |

## 2. Bên trong
- domain `models.py`: `ScheduleUnit` thêm `project_id`, `domain_id` (mặc định `None`).
- domain `planner.py`: `split_long_units`, chọn ngày ít việc nhất, tránh liền cùng dự án / loại việc.
- application `propose_schedule.py`: điền `project_id` / `domain_id`, tải phút đã duyệt theo ngày, truyền config.
- infrastructure `schedule_repository.py`: đọc `Task.ProjectId`.

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | domain | 2 việc 3 h, 3 ngày trống | 2 ngày khác nhau | AC1 |
| T2 | domain | tải có sẵn (slot đã duyệt) ở ngày 1 | việc đi ngày 2 | AC1 |
| T3 | domain | ngày deadline chỉ dùng khi hết chỗ trước đó | đúng | AC1, AC4 |
| T4 | domain | việc 300' → 150 + 150; việc 400' → 3 khối ≤ 180; việc 120' giữ nguyên | đúng | AC2 |
| T5 | domain | khối của cùng việc theo thứ tự, không chồng nhau | đúng | AC2 |
| T6 | domain | A, A, B cùng ngày → không A liền A khi có B chen được | đúng | AC3 |
| T7 | domain | chỉ 1 dự án → tránh liền cùng loại việc; hết cách thì cho liền | đúng | AC3 |
| T8 | domain | deadline gấp → vẫn dồn đủ; thiếu chỗ → NOT_ENOUGH_TIME | đúng | AC4 |
| T9 | domain | tắt 2 luật → giống hệt cũ | test cũ pass | AC5 |
| T10 | application | use case truyền project / domain / tải đã duyệt / config | đúng | AC1–AC3 |
| T11 | chạy thật | `scripts/try_schedule.py` hoặc kịch bản 3 dự án, so trước / sau (đọc DB, ghi đề xuất thật → cần Hiếu OK) | ghi số vào §5 | AC1–AC3 |

## 4. Các bước code
- [x] **1.** `ScheduleUnit` + chia khối dài — T4, T5.
- [x] **2.** Chọn ngày ít việc nhất — T1–T3, T8, T9.
- [x] **3.** Đổi việc (dự án → loại việc) — T6, T7.
- [x] **4.** Use case + repository + config — T10.
- [ ] **5.** Chạy thật — T11; docs.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-11-19 | Tạo plan | Hiếu: lịch dồn cục gây mệt |
| 2026-11-19 | Luật "đổi việc" không làm được chỉ bằng chọn giờ (bộ xếp đặt lần lượt từng task, chỉ chọn ở đầu khoảng trống) → thêm `interleave_projects`: trong các task cùng ngày deadline, xếp thứ tự xen kẽ dự án rồi loại việc (A, B, A). Ưu tiên deadline giữ nguyên | Bước 3 |
| 2026-11-19 | `Placement.SPREAD_OUT` + prompt lý do `schedule_reason_v3` (thêm 1 dòng giải thích) + 1 câu mẫu; `earlier_blocked_by` coi `SPREAD_OUT` như chờ giờ hiệu quả (giờ sớm hơn vẫn trống) | Lý do slot không được nói sai "hết giờ làm" |
| 2026-11-19 | Demo bộ xếp thật (không DB), 8:00–22:00, nghỉ trưa, 6 task / 3 dự án (21,7 h). **Trước:** thứ 2 11,8 h (có khối viết blog 5 h liền), thứ 3 7,5 h, thứ 4 trở đi trống. **Sau:** thứ 2 5,0 h · thứ 3 4,5 h · thứ 4 4,8 h · thứ 5 5,0 h; mọi khối ≤ 3 h; thứ 4 xen A → B → A; không task nào xếp vào ngày deadline. `pytest` 2027 pass | Bước 5 |
| 2026-11-19 | Chưa chạy `scripts/try_schedule.py` (ghi DB thật) — chờ Hiếu OK | Bước 5 |
