# FR-22c — Trợ lý giọng nói, giai đoạn 2: lệnh có ghi + xác nhận (W6-07), phía AI

Trạng thái: Xong (phía AI) · Branch: `feature/fr22c-voice-commands` · Cập nhật: 2026-11-11

<!-- Hiếu chốt 2026-11-11: (1) AI chỉ hiểu câu + trả "hành động chờ xác nhận"; **Mobile** gọi API ghi của Backend
sau khi user nói / bấm "Có" — AI service vẫn chỉ đọc (như W4-09). (2) Claude Code soạn plan rồi làm liền.
Phía Mobile: plans/avatar-3d-agent-2.md (repo Mobile). -->

## 0. Mục tiêu
- **Làm gì:** Thêm 5 lệnh ghi cho trợ lý. AI trả `kind = "confirm"` + `action` + câu hỏi lại; không ghi gì.
  | Công cụ | Câu ví dụ | `action` | Mobile gọi khi user đồng ý |
  |---|---|---|---|
  | `start_timer(task)` | "Bắt đầu bấm giờ việc viết blog" | `{type: start_timer, task_id, task_title, slot_id?}` | `POST /api/focus/start` |
  | `stop_timer` | "Dừng lại", "Tạm dừng" | `{type: stop_timer, session_id, stop_reason: Interrupted}` | `POST /api/focus/{id}/stop` |
  | `finish_timer` | "Xong rồi", "Làm xong rồi" | `{type: stop_timer, session_id, stop_reason: Completed}` | `POST /api/focus/{id}/stop` |
  | `extend_timer(minutes)` | "Thêm 15 phút", "Cần thêm nửa tiếng" | `{type: extend_timer, slot_id, minutes}` | `POST /schedule/overrun` (FR-16b) rồi `PUT` slot |
  | `approve_proposal` | "Duyệt lịch đề xuất" | `{type: approve_proposal, proposal_id}` | `POST /api/schedule/proposals/{id}/approve` |
- **Tiêu chí xong:**
  - [x] AC1 — 5 lệnh trên → `confirm` + `action` đúng id; câu hỏi lại ≤ 60 từ (vi / en), có tên việc / số phút.
  - [x] AC2 — Không làm được thì nói rõ, **không** `action`: đang có timer khi bắt đầu; không có timer khi dừng / xong / thêm giờ; timer không gắn slot khi thêm giờ; không có lịch đề xuất khi duyệt; không tìm thấy / nhiều task giống tên (`clarify` như FR-22).
  - [x] AC3 — AI service **không gọi API ghi nào** (chỉ GET Backend + SQL đọc). Lệnh ghi khác (tạo / xoá / sửa task, thanh toán…) vẫn `unsupported`.
  - [x] AC4 — Router tại chỗ (FR-22b) chọn được các lệnh quen, không tốn lượt; không chắc → Gemini (prompt `v4`).
  - [x] AC5 — Câu đọc không đổi hành vi (14 công cụ đọc như cũ); `pytest` xanh.
- **Ngoài phạm vi:** Backend thực thi (đã có) · nhớ lệnh qua nhiều lượt ngoài `action` hiện tại · tạo / sửa task bằng giọng.

## 1. Bên ngoài
### API — `POST /assistant/turn` thêm
- `kind`: thêm `"confirm"`.
- `action` (chỉ khi `confirm`): `{ "type": "start_timer" | "stop_timer" | "extend_timer" | "approve_proposal", "task_id"?, "task_title"?, "slot_id"?, "session_id"?, "stop_reason"?: "Completed" | "Interrupted", "minutes"?, "proposal_id"? }`.
### Đọc thêm
- Backend `GET /api/focus/current` lấy thêm `sessionId`, `scheduleSlotId`; `GET /api/schedule/proposals/pending` lấy thêm `id`.
- DB: slot hôm nay của task để gắn khi bắt đầu (`Approved` / `Due`, đang trong giờ hoặc bắt đầu trong 30 phút tới).

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Ai ghi | Mobile, sau khi user đồng ý | Hiếu chốt; AI vẫn chỉ đọc |
| 2 | Lượt AI | Như FR-22 / FR-22b: chọn tại chỗ → không trừ; qua Gemini → 1 lượt. Lượt "Có / Không" do Mobile xử lý, không gọi AI | Không tốn lượt cho câu xác nhận |
| 3 | Số phút | Lấy số trong câu ("15 phút", "1 tiếng", "nửa tiếng" = 30); không có số → 15; 5–480 | FR-16b giới hạn 5–480 |
| 4 | Gắn slot khi bắt đầu | Slot `Approved` / `Due` của task, đang diễn ra hoặc bắt đầu trong 30 phút tới; không có → bắt đầu không gắn slot | Backend cho phép; có slot thì W4-09 dùng được |
| 5 | "Xong rồi" | Dừng với `Completed`; **không** tự đánh dấu task Done | Backend tự cho slot `Done`; Done của task là việc riêng (W4-08) |

## 2. Bên trong
- domain `assistant_tools.py`: 5 `ToolName` mới, `WRITE_TOOLS`, `minutes` trong schema + `ToolChoice`.
- domain `local_router.py`: nhãn + từ khoá cho 5 lệnh; câu ghi thuộc 5 lệnh này đi tiếp, câu ghi khác vẫn `unsupported`; `spoken_minutes`.
- domain `assistant_text.py`: câu hỏi lại + câu từ chối.
- port `backend_reader.py`: `RunningFocus.session_id/slot_id`, `PendingProposal.proposal_id`. port `assistant_repository.py`: `startable_slot`.
- use case `assistant_turn.py`: `TurnKind.CONFIRM`, `PendingAction`, nhánh 5 lệnh.
- prompt `assistant_router_v4.md`; câu mẫu `assistant_router_examples.json`, bộ kiểm tra.
- api: `action` trong `TurnResponse`.

## 3. Test
| ID | Tầng | Tình huống | Mong đợi | AC |
|---|---|---|---|---|
| T1 | domain | `spoken_minutes`: "15 phút", "1 tiếng", "nửa tiếng", không số | 15 / 60 / 30 / 15 | AC1 |
| T2 | domain | router: 5 lệnh vi / en; lệnh ghi khác → `unsupported` | đúng | AC3, AC4 |
| T3 | domain | câu hỏi lại / từ chối ≤ 60 từ, không tên nội bộ | đạt | AC1 |
| T4 | application | 5 lệnh với fake: `action` đúng id; các trường hợp AC2 | đúng | AC1, AC2 |
| T5 | application | fake Backend chỉ có hàm GET | không gọi ghi | AC3 |
| T6 | infra / api | đọc `sessionId`, `scheduleSlotId`, `id`; `startable_slot` SQL; response `action` | đúng | AC1 |
| T7 | chạy thật | Gemini chọn đúng 5 lệnh với prompt `v4` (chỉ bước chọn công cụ) | đúng | AC4 |

## 4. Các bước code
- [x] **1.** Tools + schema + minutes — T1.
- [x] **2.** Ports + client + SQL `startable_slot` — T6.
- [x] **3.** Câu nói — T3.
- [x] **4.** Use case + API — T4, T5.
- [x] **5.** Router tại chỗ + prompt `v4` + câu mẫu — T2, T7.
- [x] **6.** Docs.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do |
|---|---|---|
| 2026-11-11 | Tạo plan | W6-07 |
| 2026-11-12 | Router: câu có từ khoá của 5 lệnh mà không chắc → Gemini (không tự từ chối lệnh hợp lệ); "accept / chấp nhận / đồng ý" vào nhóm động từ ghi; từ hỏi "đâu" đổi thành "tới đâu / ở đâu / đến đâu" vì "bắt đầu" bỏ dấu là "bat dau". Ngưỡng giữ 0,45 / 0,05: leave-one-out 116/281 tại chỗ, đúng 100%; bộ kiểm tra 65/77 tại chỗ, đúng 100% | Ưu tiên đúng (FR-22b #8) |
| 2026-11-12 | Xong phía AI: `pytest` 1888 (+20), prompt `v4` với Gemini thật 34/34 (20 câu mẫu + 9 câu lệnh + 5 câu dễ nhầm). Mẫu "Bắt đầu bấm giờ cho việc viết blog giúp tôi" đổi từ `unsupported` sang `start_timer` | — |
