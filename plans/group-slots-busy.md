# Group slots are busy time (FR-10, FR-16b, FR-12, FR-22, FR-23/24)

Trạng thái: Xong · Branch: `feature/group-slots-busy` · Cập nhật: 2026-11-25

## 0. Mục tiêu
- **Làm gì:** Backend PR #17 (`docs/sql/009_group_schedule_slot.sql`, chạy trên DB 2026-11-25) cho member tự xếp
  slot cho việc nhóm trong chính bảng `ScheduleSlot`: `TaskId` NULL, `GroupTaskId` / `GroupSubTaskId` có giá trị,
  chủ slot là `UserId`. Mọi truy vấn slot của AI đều `JOIN "Task"` nên không thấy slot nhóm → AI coi giờ đó là rảnh.
  Sửa để slot nhóm của user là **giờ bận** ở mọi chỗ AI đọc lịch.
- **Ai gọi:** không có API mới. Đổi kết quả của `/schedule/propose`, `/schedule/overrun`, `/briefing/today`,
  trợ lý (`SCHEDULE_FOR`), `/groups/tasks/{id}/assignee-suggestions`, chat nhóm.
- **Tiêu chí xong:**
  - [x] AC1: FR-10 không đặt slot đề xuất chồng lên slot nhóm Approved / Due / InProgress / Overrun của user
    (Backend kiểm chồng giờ bằng `ScheduleSlotQueries.OwnedBy` khi duyệt, nên trước đây sẽ 409 / bỏ slot).
  - [x] AC2: FR-16b không dời slot cá nhân chồng lên slot nhóm; slot nhóm không bao giờ bị dời; gọi overrun cho
    chính một slot nhóm đang trễ thì chạy được (trước đây 404).
  - [x] AC3: FR-23/24 tính giờ rảnh của member đã trừ slot nhóm (cùng dữ liệu FR-10).
  - [x] AC4: FR-12 và trợ lý hiện slot nhóm trong lịch với `kind = "group"`; kiểm tra nguy cơ trễ deadline coi slot
    nhóm là giờ bận.
  - [x] AC5: `docs/database.md` ghi cột mới và luật lọc slot nhóm.
- **Ngoài phạm vi:** học K / productive hours từ `GroupFocusSession` (Hiếu: không làm, 2026-11-25); AI xếp lịch cho
  việc nhóm (plan riêng).

## 1. Bên ngoài

### Database
- Đọc thêm: `ScheduleSlot."UserId"`, `"GroupTaskId"`, `"GroupSubTaskId"`; `GroupTask."Title"`, `"Deadline"`;
  `GroupSubTask."Title"`; `GroupFocusSession."ScheduleSlotId"`.
- Ghi: không đổi. `INSERT_SLOT` vẫn ghi slot cá nhân (`TaskId` có giá trị, không `UserId`) → thỏa 3 CHECK mới.

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Lọc slot nhóm theo user | `s."GroupTaskId" IS NOT NULL AND s."UserId" = :user_id`, `UNION ALL` với nửa cá nhân | Giống `OwnedBy` của Backend; dùng index `IX_ScheduleSlot_UserId_StartTime` |
| 2 | Slot nhóm của nhóm Archived / việc đã giao lại | Vẫn tính bận nếu status còn trên lịch | Backend (`GroupSlotCleanup`) tự hủy slot khi giao lại / rời nhóm; luật chồng giờ của Backend cũng không lọc theo nhóm |
| 3 | Briefing: `kind` cho slot nhóm | Giá trị mới `"group"`, `task_id` = `GroupTaskId` | App không mở nhầm việc cá nhân; Web rơi về kiểu `slot` khi chưa biết `group`, Mobile không đọc `kind` |
| 4 | FR-23: việc nhóm đã có slot bị tính 2 lần (phút việc nhóm + giờ bận) | Chấp nhận | Chỉ làm ứng viên trông bận hơn một chút (an toàn); sửa đúng cần ghép slot theo việc, để sau nếu cần |
| 5 | Overrun trên slot nhóm | Cho phép (late = slot nhóm); slot nhóm khác không bao giờ được dời | App áp dụng dời qua `PUT /api/schedule-slots/{id}` chỉ cho slot cá nhân |

## 2. Code
- `infrastructure/db/schedule_repository.py`: `GROUP_SLOT_OF_USER`, `GET_APPROVED_SLOTS` (+ nửa nhóm),
  `GET_GROUP_SLOT_TIMES`.
- `infrastructure/db/overrun_repository.py`: `GET_SLOT` / `GET_SLOTS` có nửa nhóm, cột `IsGroup`;
  `SlotRow.is_group`; `plan_overrun` use case: slot nhóm không movable.
- `infrastructure/db/briefing_repository.py`: `GET_SLOTS` có nửa nhóm; `BriefingData.group_busy`.
  `assistant_repository.py` dùng chung `GET_SLOTS`.
- `domain/briefing.py`: `SlotEntry.is_group`, `ScheduleKind.GROUP`, `risk_free_ranges(..., group_slots)`.

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | infra | SQL `GET_APPROVED_SLOTS` | có nửa nhóm lọc theo `UserId` | AC1, AC3 |
| T2 | infra | SQL overrun | `GET_SLOT` / `GET_SLOTS` có nửa nhóm, `Started` đọc `GroupFocusSession` | AC2 |
| T3 | app | overrun: slot nhóm sau slot trễ | không bị dời, slot cá nhân dời qua nó | AC2 |
| T4 | app | overrun trên slot nhóm | trả kế hoạch, không 404 | AC2 |
| T5 | domain | `schedule_between` có slot nhóm | item `kind = group` | AC4 |
| T6 | domain/app | nguy cơ trễ với slot nhóm chiếm hết giờ rảnh | task bị báo rủi ro | AC4 |
| T7 | real DB | chạy các SELECT mới trên DB (chỉ đọc) | không lỗi, thấy slot nhóm | AC1–AC4 |

## 4. Các bước code
- [x] **1.** FR-10 + FR-23/24: `GET_APPROVED_SLOTS` — T1
- [x] **2.** FR-16b — T2, T3, T4
- [x] **3.** FR-12 + trợ lý — T5, T6
- [x] **4.** `docs/database.md` + chạy SELECT thật (T7)

## 5. Change log
- 2026-11-25: tạo plan (Hiếu: "fix từ 1 đến 5").
