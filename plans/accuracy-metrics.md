# accuracy-metrics — Đo AI đúng và đáng tin (Problem #6)

Trạng thái: Xong · Branch: `research/accuracy-metrics` · Cập nhật: 2026-11-14

## 0. Mục tiêu
- **Làm gì (1–2 câu):** Script chỉ đọc DB, in 3 nhóm số để trả lời Problem #6 của tài liệu Metanoia
  ("Lịch AI sát thực tế hơn người dùng tự đoán bao nhiêu %; bao nhiêu % lịch được duyệt mà không sửa;
  lịch luôn không trùng giờ, không quá deadline").
- **Ai gọi, khi nào:** Hiếu / nhóm chạy tay trước Review hoặc khi viết báo cáo:
  `uv run python -m research.accuracy.run --user <user_id>`.
- **Tiêu chí xong:**
  - [x] AC1: M1 — sai số ước lượng: so `|T_act − T_est| / T_act` (người dùng tự đoán) với
    `|T_act − T_est × K_cũ| / T_act` (đã chỉnh bằng K lúc đó). In số mẫu, trung vị 2 sai số, % lần AI sát hơn.
  - [x] AC2: M2 — duyệt: số đề xuất theo trạng thái; trong slot của đề xuất đã duyệt, % slot không bị sửa.
  - [x] AC3: M3 — hợp lệ: trong slot AI đề xuất chưa bị sửa, đếm slot trùng nhau trong cùng đề xuất,
    slot trùng task Fixed đã có lúc đề xuất, slot kết thúc sau deadline.
  - [x] AC4: Chỉ SELECT, lọc theo 1 user (CLAUDE.md §8). Không in email, tên, tiêu đề task.
  - [x] AC5: Ít mẫu thì vẫn in, kèm cảnh báo "chưa đủ dữ liệu" (M1 < 10 mẫu, M2 < 10 slot).
- **Ngoài phạm vi:** gộp nhiều user (cần Hiếu quyết, xem #1) · vẽ biểu đồ · ghi kết quả vào DB.

## 1. Bên ngoài — nói chuyện với ai

### Database (chỉ đọc)
- `KBiasUpdateLog` ⋈ `UserDomainProfile` (lọc `UserId`): `EstimatedMinutes`, `ActualMinutes`, `OldKBias`.
- `ScheduleProposal` (lọc `UserId`): `Id`, `Status`, `CreatedAt`.
- `ScheduleSlot` ⋈ `Task`: `StartTime`, `EndTime`, `Status`, `IsManualOverride`, `OriginalStartTime`, `Task.Deadline`.
- `ScheduleChangeLog`: dòng `Source = 'Manual'` khác `Created` → slot đã bị sửa.
- `Task` Fixed của user (`FixedStart`, `FixedEnd`, `CreatedAt`) ⋈ `Project.UserId`.

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Báo cáo cần số của nhiều user, CLAUDE.md §8 bắt lọc 1 user | Chỉ làm `--user`; gộp nhiều user chờ Hiếu quyết | Không tự phá luật |
| 2 | "Slot bị sửa" nhận ra thế nào | `IsManualOverride` hoặc `OriginalStartTime` có giá trị hoặc có log `Manual` khác `Created` | Backend ghi cả 3 kiểu |
| 3 | Fixed tạo sau khi đề xuất | Chỉ so với Fixed có `CreatedAt` ≤ lúc đề xuất | Không đổ lỗi AI cho việc chưa có |
| 4 | Slot sau deadline | Đếm riêng; FR-10 cho phép kèm cảnh báo, nên đây là số để xem, không phải lỗi | FR-10 bước 5 |

## 2. Bên trong — logic
- `research/accuracy/metrics.py`: hàm thuần (không DB) — `estimate_accuracy`, `approval_summary`, `validity`.
- `research/accuracy/run.py`: đọc DB bằng engine của repo, gọi các hàm trên, in kết quả.

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | research | M1 với mẫu tay (60/90/K 1.3 …) | Sai số và % đúng | AC1 |
| T2 | research | M1 rỗng | n = 0, không lỗi chia 0 | AC1, AC5 |
| T3 | research | M2: trạng thái đề xuất, slot sửa / không sửa, slot `Cancelled` / `Proposed` bị loại | Đếm đúng | AC2 |
| T4 | research | M3: trùng trong đề xuất, trùng Fixed (trước / sau lúc đề xuất), sau deadline, không deadline | Đếm đúng | AC3 |
| T5 | research | Cảnh báo ít mẫu | Có cờ | AC5 |
| T6 | thật | Chạy với 1 user thật | In số, 0 dòng ghi | AC4 |

## 4. Các bước code
- [x] **1.** Hàm tính + test — file: `research/accuracy/metrics.py` — test: T1–T5.
- [x] **2.** Script đọc DB — file: `research/accuracy/run.py` — test: T6.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-11-14 | Tạo plan | Problem #6 (tài liệu Metanoia), Hiếu OK làm |
| 2026-11-14 | Chạy thật (chỉ SELECT) với 2 user: user nhiều đề xuất nhất (302 đề xuất, 189 slot) và user duy nhất có log K (2 task). M1: tự đoán sai trung vị 27%, chỉnh bằng K còn 4%, K sát hơn 2/2 task (quá ít mẫu). M2: chưa có slot nào thuộc đề xuất đã duyệt của 2 user này. M3: 192 slot, 0 trùng nhau, 0 trùng Fixed, 0 sau deadline. DB mới có 2 dòng `KBiasUpdateLog` và 5 đề xuất `Approved` → cần dữ liệu thật trước khi đưa số lên báo cáo | Bước 2 |
