# FR-16b — Task lố giờ → đề xuất dời lịch (W4-09)

Trạng thái: Đã duyệt · Branch: `feature/fr16b-overrun-replan` · Cập nhật: 2026-11-10

<!-- Nháp do Claude Code soạn từ code hiện có (AI + Backend develop 6b141e7), chờ Hiếu duyệt.
Luật gốc: CLAUDE.md §5 "Task runs over its slot: never auto-move; extend if the next gap fits,
else propose a re-plan of the rest of the day. No LLM, no quota." · FR-16: "Task chưa xong mà cần
thêm giờ: hệ thống đề xuất dời các task sau, người dùng duyệt mới dời." · Sheet W4-09: "Không gọi
LLM. Không dời Fixed, không dời task đã bắt đầu. User duyệt mới dời."
⚠️ = cần báo Backend / team. Câu hỏi cho Hiếu: mục 1 "Vấn đề & quyết định", các dòng có (?) -->

## 0. Mục tiêu
- **Làm gì:** User đang làm một slot mà hết giờ chưa xong, nói "cần thêm X phút". AI service tính
  **đề xuất** (không ghi gì): kéo dài slot nếu khoảng trống ngay sau đủ chỗ; nếu không thì dời các slot
  Flexible phía sau trong ngày sang giờ trống còn lại (và ngày làm việc kế tiếp nếu hết chỗ).
  User duyệt thì client gọi Backend để áp dụng.
- **Ai gọi, khi nào:** Mobile / Frontend gọi thẳng AI service (như FR-12, FR-22) khi user trả lời câu hỏi
  "Chưa xong, cần thêm bao lâu" (FR-16), từ thông báo `OVERRUN` / `CHECK_IN` hoặc màn Focus.
- **Tiêu chí xong:**
  - [ ] AC1 — Khoảng trống sau slot đủ chỗ cho X phút (+ buffer) trước việc bận kế tiếp và trước giờ kết thúc ngày làm → `kind = "extend"`, chỉ đổi giờ kết thúc slot đang lố; không đụng slot nào khác.
  - [ ] AC2 — Không đủ chỗ → `kind = "replan"`: các slot Flexible **sau** slot đang lố trong hôm nay được xếp lại theo đúng thứ tự cũ, giữ nguyên độ dài, bắt đầu từ sau giờ kết thúc mới (+ buffer), giờ bắt đầu tròn 5'.
  - [ ] AC3 — Không bao giờ dời: task Fixed, sự kiện Google, slot đã bắt đầu (có phiên bấm giờ, hoặc `InProgress` / `Overrun` / `Due` đã qua giờ bắt đầu), slot `Done` / `Skipped` / `Missed` / `Cancelled`.
  - [ ] AC4 — Slot không còn chỗ hôm nay → xếp vào ngày làm việc kế tiếp (tối đa 7 ngày, như FR-10); không kịp trước deadline của task → không xếp, vào `not_placed` kèm cảnh báo.
  - [ ] AC5 — Không gọi LLM, không kiểm / không trừ lượt, không ghi `AIUsageLog`, **không ghi DB**.
  - [ ] AC6 — Chỉ đọc dữ liệu của user trong JWT; slot của người khác → 404.
  - [ ] AC7 — Câu tóm tắt (vi / en) để Mobile / trợ lý 3D đọc, ≤ 60 từ, không tên nội bộ.
  - [ ] AC8 — Tốc độ phía server: p95 < 1 s (chỉ đọc DB + code).
- **Ngoài phạm vi:** áp dụng thay đổi (Backend, xem #2) · dời slot sang người khác / dời task Fixed ·
  hỏi "cần thêm bao lâu" (Mobile / Frontend, FR-16) · trợ lý 3D tự dời bằng giọng (giai đoạn 2, W6-07) ·
  xếp lại cả tuần (dùng FR-10).

## 1. Bên ngoài — nói chuyện với ai

### API
| Method | Endpoint | Request | Response | Lỗi |
|---|---|---|---|---|
| POST | `/schedule/overrun` | `{ "slot_id": uuid, "extra_minutes": int (5–480), "language": "vi" \| "en"? }` | 200 `OverrunPlan` | 401 · 404 slot không có / không phải của user · 409 slot không ở trạng thái lố được (đã xong / bị huỷ) · 422 |

`POST` vì có body; endpoint **không ghi** gì.

```json
// OverrunPlan
{
  "kind": "extend" | "replan",
  "extended": { "slot_id": "…", "start": "…", "end": "…" },          // slot đang lố, giờ kết thúc mới
  "moves": [ { "slot_id": "…", "task_title": "…", "old_start": "…", "old_end": "…",
               "new_start": "…", "new_end": "…" } ],                 // rỗng khi extend
  "not_placed": [ { "slot_id": "…", "task_title": "…" } ],             // không kịp trước deadline
  "summary": "Mình kéo dài Viết blog thêm 30 phút và dời 2 việc phía sau, việc cuối sang 16 giờ 30."
}
```
Giờ ISO 8601 theo múi giờ user (như FR-10).

### Database (chi tiết bảng: `docs/database.md`)
- Đọc: `ScheduleSlot` của user (slot đang lố + slot hôm nay và 7 ngày tới), `Task` (`Type`, `Deadline`, `Title`),
  `FocusSession` (slot đã bắt đầu chưa), `User` (giờ làm, buffer, múi giờ), task Fixed, `GoogleCalendarEvent`
  — như `get_planning_data` của FR-10, thêm id + trạng thái của từng slot.
- Ghi: **không có**.

### Dịch vụ khác
- Không gọi LLM, không gọi Backend.

### Vấn đề & quyết định
| # | Vấn đề | Quyết định đề xuất | Lý do |
|---|---|---|---|
| 1 | Ai tính đề xuất | AI service, bằng code (dùng lại `free_time.py`, phần đặt slot của `planner.py`) | Cùng luật giờ trống / buffer / làm tròn với FR-10; CLAUDE.md §5 |
| 2 | Ai áp dụng khi user duyệt ⚠️ | **Chốt A (Hiếu, 2026-11-10):** client gọi `PUT /api/schedule-slots/{id}` của Backend cho slot kéo dài + từng slot dời (đã có, có ghi `ScheduleChangeLog`). **B:** Backend thêm 1 endpoint nhận cả `OverrunPlan` và áp dụng trong 1 transaction. | A chạy được ngay, không chờ Backend; B an toàn hơn (không áp dụng nửa chừng) |
| 3 | Google Calendar (?) ⚠️ | `UpdateSlotAsync` của Backend hiện **không** cập nhật sự kiện Google của slot đã duyệt | Slot dời trong app nhưng Google vẫn giờ cũ → Backend cần sửa, chọn A hay B đều cần |
| 4 | Nguồn trong `ScheduleChangeLog` ⚠️ | `PUT` ghi `Source = Manual`; nên là `AI` cho thay đổi từ đề xuất này | Báo Backend: thêm trường nguồn vào request, hoặc chọn B ở #2 |
| 5 | Lưu đề xuất | Không lưu (không `ScheduleProposal`) | Bảng `ScheduleProposal` / FR-11 chỉ duyệt slot `Proposed`; ở đây là dời slot đã `Approved`. Đề xuất tính lại rất nhanh, cũ thì tính lại |
| 6 | "Slot đã bắt đầu" | Có `FocusSession` gắn slot, hoặc trạng thái `InProgress` / `Overrun` | Sheet W4-09: không dời task đã bắt đầu |
| 7 | Slot `Due` (đến giờ mà chưa bấm Start) | Dời được | Chưa bắt đầu; dời đi giúp user hơn là để thành `Missed` |
| 8 | Hết chỗ hôm nay | Sang ngày làm việc kế tiếp, giờ trống sớm nhất, giữ thứ tự | Đơn giản, đoán được; không ưu tiên giờ hiệu quả (khác FR-10) để không đảo thứ tự |
| 9 | Không kịp deadline | Không xếp slot đó, đưa vào `not_placed` + câu cảnh báo | Giống FR-10 AC4: không âm thầm xếp sau deadline |
| 10 | Quota | Không trừ | CLAUDE.md §7: không LLM thì không quota |

## 2. Bên trong — logic

### Code nằm ở đâu
- domain: `overrun_plan.py` (thuần: kéo dài hay dời, xếp lại theo thứ tự) · câu tóm tắt `overrun_text` trong `assistant_text.py` (dùng chung đọc giờ, cắt tên, giới hạn 60 từ, chặn tên nội bộ).
- application: port `ports/overrun_repository.py` · `use_cases/plan_overrun.py`.
- infrastructure: `db/overrun_repository.py` (SQL đọc, dùng lại truy vấn của `schedule_repository.py`).
- api: `routes/schedule.py` thêm `POST /schedule/overrun` · `deps.py`.
- config: `OVERRUN_EXTRA_MINUTES_MAX` (480) · dùng lại `SCHEDULE_ROUND_MINUTES`, cửa sổ 7 ngày của FR-10.

### Luồng `/schedule/overrun`
1. Đọc slot theo `slot_id` + user: không có → 404; `Done` / `Skipped` / `Missed` / `Cancelled` / `Proposed` → 409.
2. `new_end = round_up(max(slot.end, now) + extra_minutes, 5')`.
3. Việc bận kế tiếp = sớm nhất trong: task Fixed, sự kiện Google, slot không dời được, giờ kết thúc ngày làm.
   `new_end + buffer ≤ việc bận kế tiếp` và không có slot dời được nào chen giữa → `extend`.
4. Ngược lại → `replan`: lấy slot dời được (Flexible, `Approved` / `Due`, chưa bắt đầu) bắt đầu sau slot đang lố,
   từ bây giờ đến hết hôm nay, theo giờ bắt đầu. Bỏ chúng khỏi giờ bận, tính giờ trống từ `new_end + buffer`
   (7 ngày, như FR-10), đặt lần lượt **đúng thứ tự cũ**, mỗi slot giữ nguyên độ dài, giờ trống sớm nhất,
   không trước slot vừa đặt. Không kịp deadline → `not_placed`.
5. Ghép câu tóm tắt (AC7). Trả `OverrunPlan`.

### Luật & trường hợp đặc biệt
- Slot đang lố của task Fixed → 409 (Fixed không đo, không kéo dài).
- `extra_minutes` cộng từ `max(slot.end, now)`: đã lố 20' mà cần thêm 30' thì kết thúc mới = bây giờ + 30'.
- Không có slot nào phía sau và giờ kết thúc mới vượt giờ kết thúc ngày làm → vẫn `extend` (vượt giờ làm được phép, như buffer FR-10) kèm câu nhắc "quá giờ làm".

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | domain | Khoảng trống sau đủ / thiếu 5' / đúng bằng (có buffer) | extend / replan / extend | AC1, AC2 |
| T2 | domain | 3 slot phía sau, 1 Fixed giữa chừng, 1 sự kiện Google | Fixed + Google giữ nguyên; 3 slot giữ thứ tự, tròn 5', có buffer | AC2, AC3 |
| T3 | domain | Slot phía sau đã có phiên bấm giờ / `Done` | không bị dời | AC3 |
| T4 | domain | Hết chỗ hôm nay; deadline hôm nay | sang ngày kế tiếp / `not_placed` | AC4 |
| T5 | domain | Câu tóm tắt vi / en, mọi trường hợp | ≤ 60 từ, không tên nội bộ | AC7 |
| T6 | application | Fake repo: 404, 409 (Done / Fixed / Proposed), extend, replan | đúng lỗi / kết quả; repo không có hàm ghi | AC5, AC6 |
| T7 | infrastructure | SQL đọc slot của user khác | không thấy | AC6 |
| T8 | api | 401, 422 (`extra_minutes` 0 / 481), 404, 409, 200 | đúng | — |
| T9 | chạy thật | `scripts/try_overrun.py`: tạo slot thử, gọi, xoá đúng các dòng đã tạo | đúng đề xuất; đo p95 | AC8 |

## 4. Các bước code
*(Mỗi bước = 1 việc nhỏ + test của nó. Claude Code DỪNG sau mỗi bước.)*
- [x] **0.** Đọc DB thật (chỉ SELECT): giá trị `ScheduleSlot.Status` thật (Backend có thêm `Due`, `InProgress`, `Overrun`, `Postponed` mà `docs/database.md` chưa ghi); CHECK constraint. Báo lệch, không code.
- [x] **1.** `overrun_plan.py` — T1–T4.
- [x] **2.** `overrun_text.py` — T5.
- [x] **3.** Port + `plan_overrun.py` + fake repo — T6.
- [x] **4.** `overrun_repository.py` — T7.
- [x] **5.** Route + deps + config — T8.
- [ ] **6.** Chạy thật — T9.
- [x] **7.** Docs: `CLAUDE.md` §6, `docs/open-questions.md` (#2–#4 cho Backend).

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-11-10 | Tạo nháp | Hiếu nhờ làm W4-09; Claude Code soạn từ code AI + Backend `develop` |
| 2026-11-10 | Hiếu duyệt; #2 chốt A: client gọi `PUT /api/schedule-slots/{id}` có sẵn | Chạy được ngay, không chờ Backend |
| 2026-11-10 | Bước 0: CHECK `ScheduleSlot.Status` thật có 10 giá trị (thêm `Due`, `InProgress`, `Overrun`, `Postponed`); `FocusSession.ScheduleSlotId` có. **Lệch ngoài plan:** FR-10 chỉ coi `Approved` là giờ bận, FR-12 / trợ lý chỉ đọc `Proposed` / `Approved` / `Done` → ghi `docs/open-questions.md`, chờ Hiếu | Đọc DB thật |
| 2026-11-10 | Bước 2: câu tóm tắt đặt trong `assistant_text.py` (không tạo `overrun_text.py`) để dùng chung các hàm đọc giờ / giới hạn 60 từ | Tránh chép lại code |
| 2026-11-10 | Bước 1–5, 7 xong: `pytest` 1787 (+40), `ruff` sạch. Bước 6 mới chạy phần chỉ đọc trên 4 slot thật: SQL đúng schema, ~285 ms / lần (máy dev → Supabase). Trường hợp `replan` với dữ liệu thật cần tạo dữ liệu thử → chờ Hiếu cho phép | CLAUDE.md §8 |
