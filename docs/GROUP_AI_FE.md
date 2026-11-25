# AI cho nhóm (Work Group): hướng dẫn cho FE (Web / Mobile)

Cập nhật: 25/11/2026 · Người viết: Hiếu (AI) · Code: AI PR #54, #55, #56, #58, #59 (đã merge)

**Tóm tắt nhanh**
- Có 3 API AI mới cho nhóm. Hiện **chưa màn hình nào gọi**, FE/Mobile cần nối:

| # | Chức năng | Ai dùng | Màn hình gợi ý | API | Tốn lượt AI |
|---|---|---|---|---|---|
| 1 | **Gợi ý người nhận việc**: "giao việc này cho ai?" | Leader | Chi tiết việc nhóm, cạnh ô "Người được giao" | `POST /groups/tasks/{id}/assignee-suggestions` | Tối đa 1 lượt |
| 2 | **Chat AI lập kế hoạch nhóm**: dán brief → bàn → bản nháp giai đoạn / việc / việc con, mỗi việc có sẵn người được gợi ý → Áp dụng | Leader (gói Pro / Premium) | Tab "Chat AI" trong nhóm | `/groups/{id}/chats`… | 1 lượt / tin |
| 3 | **Gợi ý giờ làm việc nhóm** (**mới**): AI tìm giờ trống trong lịch của mình cho phần việc nhóm được giao | Người được giao (Leader hoặc Member) | Chi tiết việc nhóm / "Việc nhóm của tôi", nút "Gợi ý giờ" | `POST /groups/tasks/{id}/slot-suggestions` | **Không** |

- AI **không ghi vào bảng nhóm**. Ở cả 3 chức năng, FE gọi API Backend để giao việc, tạo việc hoặc tạo slot.
- Từ 25/11, AI coi **slot nhóm là giờ bận** ở mọi chỗ. Hệ quả cho các màn hình đang có được ghi ở mục 4 (briefing có `kind: "group"`, overrun).
- Chat nhóm đã sẵn sàng trên DB (Backend đã chạy `008_chat_group.sql` ngày 24/11).

---

## 0. Những điều chung

### Gọi AI service ở đâu
- Gọi **thẳng AI service** (không qua Backend), giống chat dự án và trợ lý 3D.
- Base URL: Web `VITE_AI_API_URL`, Mobile `AI_SERVICE_URL`.
- Header: `Authorization: Bearer <access token của Backend>` (đúng token đang dùng gọi Backend).

### Ai được dùng
- Chức năng 1 và 2: **chỉ Leader** của nhóm. Member / Viewer: **ẩn nút**, gọi vẫn bị `403`.
- Chức năng 3: **người được giao** việc (hoặc ít nhất một việc con đang mở) của việc đó. Người khác: ẩn nút (`403`).
- Nhóm đã lưu trữ (Archived): ẩn nút (`409`).

### Mã lỗi
AI service trả lỗi dạng FastAPI: `{ "detail": "..." }` hoặc `{ "detail": { "code": "...", ... } }` — **khác** Backend (`{ "code": ..., "details": ... }`).

| Mã | Khi nào | FE làm gì |
|---|---|---|
| `401` | Token sai / hết hạn / tài khoản đã xóa | Refresh token như mọi API |
| `403` | Không phải Leader | Ẩn chức năng |
| `403` `{"detail": {"code": "plan_feature_locked", "feature": "group_chat"}}` | Gói **Free** dùng chat nhóm | "Chat AI nhóm có ở gói Pro / Premium" + nút nâng cấp |
| `404` | Không thuộc nhóm / không có việc / không có chat | "Không tìm thấy" |
| `409` | Nhóm đã lưu trữ; việc đã Done / Skipped; việc có giờ cố định (chức năng 3); bản nháp đã áp dụng (`"already_applied"`); không có gói (`"no_active_plan"`) | Hiện thông báo tương ứng |
| `422` | Tin nhắn rỗng (khi `discuss`) hoặc dài quá 4000 ký tự | Chặn ở ô nhập |
| `429` | Hết lượt AI của gói | "Bạn đã dùng hết lượt AI của gói" + nút nâng cấp |
| `502` | AI tạm lỗi (Gemini và dự phòng đều lỗi) | "AI đang bận, thử lại sau" + nút Thử lại. **Không bị trừ lượt** |

### Hiển thị tên người
AI **không bao giờ** trả tên / email (riêng tư). AI chỉ trả `user_id`. FE ghép tên từ Backend:
`GET /api/groups/{groupId}/members` → `[{ userId, fullName, email, role, joinedAt }]` → map `user_id` → `fullName`.

### Giờ giấc
- `free_times[].start/end` (chức năng 1) và `slots[].start/end` (chức năng 3): ISO 8601 **theo múi giờ của người đó** (ví dụ `+07:00`) → hiển thị thẳng.
- `created_at` của chat: ISO 8601 UTC (`Z`) → đổi sang giờ máy như bình thường.

---

## 1. Gợi ý người nhận việc

### Khi nào gọi
Leader mở một việc nhóm **chưa xong** (ToDo / InProgress) → bấm **"Gợi ý người làm"**.
Mỗi lần bấm = tối đa **1 lượt AI** của Leader (chỉ trừ khi AI viết được lý do).

### Request
```
POST /groups/tasks/{groupTaskId}/assignee-suggestions
```
```json
{ "domain_id": "uuid-hoặc-null", "language": "vi" }
```
| Trường | Bắt buộc | Ghi chú |
|---|---|---|
| `domain_id` | Không | **Loại công việc** (Coding, Design…) để AI dùng đúng thói quen ước lượng của từng người. Việc nhóm chưa có loại công việc trong DB nên FE cho Leader chọn (dropdown từ Backend `GET /api/domains`); không chọn → vẫn chạy được |
| `language` | Không | `vi` (mặc định) hoặc `en` — ngôn ngữ của câu lý do |

### Response 200 (ví dụ; đúng cấu trúc API trả về)
```json
{
  "task_id": "00000000-0000-0000-0000-0000000000c8",
  "suggestions": [
    {
      "user_id": "00000000-0000-0000-0000-000000000002",
      "role": "member",
      "rank": 1,
      "is_current_assignee": false,
      "busy_at_fixed_time": false,
      "free_minutes": 2699,
      "group_work_minutes": 0,
      "fits": true,
      "reason": "Thành viên này là lựa chọn tốt nhất, còn 45 giờ rảnh với lịch trống Thứ Ba 24/11, 13:00–18:00.",
      "free_times": [
        { "start": "2026-11-24T13:00:00+07:00", "end": "2026-11-24T18:00:00+07:00" },
        { "start": "2026-11-25T13:00:00+07:00", "end": "2026-11-25T18:00:00+07:00" }
      ]
    },
    {
      "user_id": "00000000-0000-0000-0000-000000000001",
      "role": "leader",
      "rank": 2,
      "is_current_assignee": false,
      "busy_at_fixed_time": false,
      "free_minutes": 365,
      "group_work_minutes": 0,
      "fits": true,
      "reason": "Lựa chọn tốt thứ hai, còn 6,1 giờ rảnh và có lịch trống Thứ Hai 23/11, 18:00–23:00.",
      "free_times": [
        { "start": "2026-11-23T18:00:00+07:00", "end": "2026-11-23T23:00:00+07:00" }
      ]
    }
  ],
  "reasons_skipped": null
}
```

| Trường | Ý nghĩa | Gợi ý hiển thị |
|---|---|---|
| `suggestions` | Đã **sắp xếp sẵn** (tốt nhất trước). Chỉ Leader / Member, không có Viewer. Rỗng = nhóm chưa có ai nhận việc được | Danh sách theo `rank` |
| `rank` | 1 = tốt nhất | Huy hiệu "Đề xuất" cho hạng 1 |
| `is_current_assignee` | Đang là người được giao | Nhãn "Đang làm" |
| `busy_at_fixed_time` | Việc có giờ cố định và người này đã có lịch trùng giờ đó | Icon cảnh báo đỏ "Bận giờ này" |
| `fits` | `true` đủ giờ trước hạn · `false` có thể không đủ · `null` việc chưa có ước lượng | Xanh / cam / xám |
| `free_minutes` | Giờ rảnh trước hạn (đã trừ việc khác của họ) | "Còn ~45 giờ rảnh" |
| `group_work_minutes` | Việc nhóm đang ôm trong khoảng đó | "Đang có 2 giờ việc nhóm" |
| `free_times` | ≤ 2 khung lịch trống dài nhất | Chip giờ |
| `reason` | 1–2 câu do AI viết, có số liệu cụ thể. **Có thể `null`** (xem dưới) | Chữ nhỏ dưới tên |
| `reasons_skipped` | `null` = có lý do · `"quota"` = hết lượt / không có gói · `"llm_failed"` = AI lỗi | Vẫn hiện danh sách; ghi chú "Hết lượt AI nên không có lý do" / "AI tạm lỗi" |

> Hết lượt AI **vẫn trả 200 kèm xếp hạng** (xếp hạng do code, không tốn lượt) — chỉ thiếu `reason`.

### Giao việc sau khi chọn
AI **không giao việc**. Leader chọn người → gọi Backend:
```
PUT /api/group-tasks/{groupTaskId}/assignee
{ "assigneeId": "<user_id đã chọn>" }
```
Nếu Backend trả `409 GROUP_ASSIGNEE_BUSY` → hiện hộp thoại cảnh báo của Backend → Leader đồng ý thì gửi lại kèm `"confirmConflicts": true` (xem `docs/GROUP_FE.md` §8 của Backend).

### Lỗi riêng
`404` không thuộc nhóm / không có việc · `403` không phải Leader · `409` nhóm lưu trữ hoặc việc đã Done / Skipped.

---

## 2. Chat AI lập kế hoạch nhóm

### Luồng màn hình
```
[Leader mở tab "Chat AI" của nhóm]
   │  GET  /groups/{groupId}/chats            → danh sách chat (mới nhất trước)
   │  POST /groups/{groupId}/chats            → tạo chat mới (nút "+ Chat mới")
   ▼
[Màn chat]   GET /groups/chats/{chatId}       → toàn bộ tin nhắn (cả bản nháp cũ)
   │
   ├─ Leader gõ brief / câu hỏi → POST …/messages  { text, mode: "discuss" }
   │      → AI trả checklist: đã rõ / còn thiếu / nên hỏi khách / rủi ro
   │
   ├─ Bấm "Lên kế hoạch" → POST …/messages  { text: "", mode: "plan" }
   │      (hoặc gõ yêu cầu sửa, ví dụ "tách việc API làm 2", mode "plan")
   │      → AI trả bản nháp; MỖI VIỆC có suggested_assignee
   │
   └─ Leader xem / đổi người / bấm "Áp dụng"
          → FE tạo giai đoạn, việc, việc con bằng API Backend (mục 2.6)
          → xong gọi POST …/messages/{messageId}/applied
```
Mỗi tin nhắn gửi = **1 lượt AI** của Leader (chỉ trừ khi AI trả lời được). Gói Free → `403 plan_feature_locked`.

### 2.1 Tạo chat
```
POST /groups/{groupId}/chats
{ "title": null, "language": "vi" }        // title bỏ trống → "Lập kế hoạch nhóm"
```
`201`:
```json
{
  "conversation_id": "a9e67bb0-d93f-48f1-9623-e913bb439db1",
  "group_id": "00000000-0000-0000-0000-000000000064",
  "title": "Lập kế hoạch nhóm",
  "status": "open",
  "created_at": "2026-11-23T01:00:00Z"
}
```

### 2.2 Danh sách chat của nhóm
```
GET /groups/{groupId}/chats      → [ { conversation_id, group_id, title, status, created_at }, ... ]
```

### 2.3 Mở một chat
```
GET /groups/chats/{chatId}       → { "conversation": {...}, "messages": [ Message, ... ] }
```
`messages` cũ → mới. Mỗi `Message` có dạng như mục 2.4.

### 2.4 Gửi tin nhắn
```
POST /groups/chats/{chatId}/messages
{ "text": "Khách Bloom Coffee cần website…", "mode": "discuss" }
```
| `mode` | Khi nào | `text` |
|---|---|---|
| `discuss` | Bàn brief, hỏi AI (mặc định nên dùng cho tin đầu tiên) | Bắt buộc, ≤ 4000 ký tự |
| `plan` | Nút **"Lên kế hoạch"** / **"Cập nhật kế hoạch"**, hoặc gõ yêu cầu sửa bản nháp | Được để `""` (= bấm nút) |

`200`: `{ "conversation_id", "user_message": Message, "ai_message": Message }`

**Message**
| Trường | Ghi chú |
|---|---|
| `message_id` | Dùng cho `/applied` |
| `role` | `"user"` / `"assistant"` |
| `content` | Chữ hiển thị trong bong bóng chat |
| `created_at` | ISO UTC |
| `notes` | Có khi AI trả lời `discuss` (checklist), ngược lại `null` |
| `draft` | Có khi AI trả lời `plan` (bản nháp), ngược lại `null` |
| `is_applied` | `true` = bản nháp này đã áp dụng → **ẩn / khóa nút Áp dụng** |

**`ai_message` khi `discuss`** (`notes`):
```json
{
  "clear": ["Khách: Bloom Coffee", "Website giới thiệu + đặt bàn online", "Deadline: 05/12/2026"],
  "missing": [],
  "questions": ["Khách đã có sẵn ảnh và nội dung chưa?"],
  "risks": ["Thời gian khá gấp nếu phải chờ nội dung từ khách"],
  "ready_to_plan": true
}
```
Gợi ý: 4 khối "Đã rõ / Còn thiếu / Nên hỏi khách / Rủi ro"; `ready_to_plan = true` → làm nổi nút "Lên kế hoạch" (Leader vẫn bấm được khi `false`).

**`ai_message` khi `plan`** (`draft`, ví dụ; đúng cấu trúc API trả về):
```json
{
  "message_id": "d53af38e-395d-4bbd-8c45-6cb1b771ad23",
  "role": "assistant",
  "content": "Mình chia 2 giai đoạn cho nhóm.",
  "created_at": "2026-11-23T01:00:00Z",
  "draft": {
    "milestones": [
      {
        "key": "m1",
        "title": "Thiết kế",
        "deadline": "2026-11-25",
        "reason": "Làm giao diện trước để khách duyệt sớm",
        "tasks": [
          {
            "key": "m1-t1",
            "title": "Wireframe trang chủ",
            "domain_id": "00000000-0000-0000-0000-00000000012e",
            "domain_name": "Design",
            "estimated_minutes": 120,
            "reason": "Cần thống nhất bố cục trước khi lập trình",
            "subtasks": [],
            "suggested_assignee": {
              "user_id": "00000000-0000-0000-0000-000000000001",
              "fits": true,
              "reason": "Còn 27 giờ rảnh trước hạn Thứ Tư 25/11, 23:59; lịch trống Thứ Hai 23/11, 08:00–17:00."
            }
          }
        ]
      },
      {
        "key": "m2",
        "title": "Lập trình",
        "deadline": "2026-11-27",
        "reason": null,
        "tasks": [
          {
            "key": "m2-t1",
            "title": "Lập trình form đặt bàn",
            "domain_id": "00000000-0000-0000-0000-00000000012d",
            "domain_name": "Coding",
            "estimated_minutes": 240,
            "reason": "Chức năng chính của khách",
            "subtasks": [
              { "title": "Giao diện form", "estimated_minutes": 90, "reason": null },
              { "title": "Gửi email cho quán", "estimated_minutes": 150, "reason": null }
            ],
            "suggested_assignee": {
              "user_id": "00000000-0000-0000-0000-000000000002",
              "fits": true,
              "reason": "Còn 45 giờ rảnh trước hạn Thứ Sáu 27/11, 23:59; lịch trống Thứ Hai 23/11, 08:00–17:00."
            }
          }
        ]
      }
    ],
    "reminders": [],
    "warnings": [],
    "total_estimated_minutes": 360
  },
  "notes": null,
  "is_applied": false
}
```

| Trường | Ghi chú |
|---|---|
| `milestones[].deadline` | Ngày (`YYYY-MM-DD`), luôn có, nằm trong hạn của nhóm |
| `tasks[].estimated_minutes` | Ước lượng của cả nhóm cho việc |
| `tasks[].subtasks` | Có khi việc > 2 giờ (2–6 việc con), ngược lại `[]` |
| `tasks[].domain_*` | Loại công việc — **chỉ để hiển thị** (việc nhóm chưa lưu loại công việc) |
| `tasks[].suggested_assignee` | Người được gợi ý (`user_id` null = nhóm không có ai nhận việc được). `fits` như chức năng 1. `reason` do code viết, luôn có số liệu |
| `reminders` | Việc giờ cố định (họp…) Leader nên tự tạo |
| `warnings` | Cảnh báo cho Leader (ví dụ đã bỏ việc trùng tên việc có sẵn trong nhóm) |

Gợi ý UI bản nháp: mỗi giai đoạn là một khối; mỗi việc có **dropdown người làm** (mặc định = `suggested_assignee.user_id`, danh sách = Leader / Member), lý do hiện chữ nhỏ, `fits = false` tô cam. Leader được sửa tên / ước lượng / người làm trước khi Áp dụng (sửa ở FE, không cần gọi AI). Muốn AI làm lại → gõ yêu cầu với `mode: "plan"`.

### 2.5 Lỗi khi gửi tin
`422` tin rỗng / quá dài · `403` Free / không phải Leader · `409` nhóm lưu trữ · `429` hết lượt · `502` AI lỗi (không trừ lượt, không lưu tin — cho bấm Thử lại, giữ nguyên chữ đã gõ). Thời gian trả lời: `discuss` ~4 s, `plan` ~9–15 s → hiện "AI đang suy nghĩ…" và khóa nút gửi.

### 2.6 Áp dụng bản nháp (FE gọi Backend)
AI **không ghi vào bảng nhóm**. Khi Leader bấm **"Áp dụng"**:

1. Lấy `orderIndex` tiếp theo: `GET /api/groups/{groupId}/milestones` → `next = max(orderIndex) + 1` (nhóm chưa có giai đoạn → 1).
2. Với **mỗi giai đoạn** (theo thứ tự trong bản nháp):
   ```
   POST /api/groups/{groupId}/milestones
   { "name": milestone.title, "orderIndex": next++, "dueDate": milestone.deadline }
   ```
3. Với **mỗi việc** của giai đoạn đó:
   ```
   POST /api/groups/{groupId}/tasks
   {
     "title": task.title,
     "milestoneId": "<id giai đoạn vừa tạo>",
     "estimatedMinutes": task.estimated_minutes,
     "deadline": "<milestone.deadline> 23:59 giờ Việt Nam, dạng ISO, ví dụ 2026-11-25T23:59:00+07:00",
     "assigneeId": "<người Leader chọn, mặc định suggested_assignee.user_id; null nếu bỏ trống>"
   }
   ```
   - Backend trả `409 GROUP_ASSIGNEE_BUSY` → hỏi Leader như mục 1, đồng ý thì gửi lại cùng body + `"confirmConflicts": true`.
4. Với **mỗi việc con**:
   ```
   POST /api/group-tasks/{taskId vừa tạo}/subtasks
   { "title": subtask.title, "estimatedMinutes": subtask.estimated_minutes }
   ```
   (người làm việc con: bỏ trống → người của việc cha làm; muốn giao riêng thì thêm `assigneeId`.)
5. Tất cả thành công → `POST /groups/chats/{chatId}/messages/{messageId}/applied` (AI, trả `204`) → khóa nút Áp dụng của bản nháp đó.
   - `409 already_applied` = đã áp dụng ở tab / máy khác → khóa nút, không tạo lại.

**Lỗi giữa chừng** (ví dụ mạng rớt ở việc thứ 5): **không** gọi `/applied`; báo Leader phần nào đã tạo (đã có trong nhóm), phần nào chưa; cho "Thử lại phần còn lại" (bỏ qua những mục đã tạo — lưu id tạm ở FE). Gọi tuần tự, không gọi song song (tránh trùng `orderIndex`).

---

## 3. Gợi ý giờ làm việc nhóm (mới, FR-25)

### Khi nào gọi
Người được giao mở một việc nhóm **chưa xong**, không có giờ cố định, rồi bấm **"Gợi ý giờ"**.
Không gọi LLM nên **không tốn lượt AI** và chạy được với mọi gói.

AI chỉ đọc. Kết quả không chồng lên việc cố định, event Google, slot cá nhân hay slot nhóm khác, không rơi vào giờ nghỉ trưa, và nằm trong giờ làm việc của người đó. Thứ tự là việc con theo `orderIndex`, mỗi khối ≤ 3 giờ. Cách xếp giống nút "Xếp lịch" cá nhân.

### Request
```
POST /groups/tasks/{groupTaskId}/slot-suggestions
{ "language": "vi" }        // tùy chọn, mặc định "vi"
```

### Response 200 (ví dụ; đúng cấu trúc API trả về)
```json
{
  "task_id": "00000000-0000-0000-0000-0000000000c8",
  "slots": [
    {
      "subtask_id": "00000000-0000-0000-0000-000000000003",
      "title": "Báo cáo: Phần 3",
      "start": "2026-11-23T10:00:00+07:00",
      "end": "2026-11-23T11:00:00+07:00",
      "minutes": 60,
      "reason": "Khoảng trống trong lịch của bạn, xong trước hạn Thứ Tư 25/11, 17:00."
    },
    {
      "subtask_id": "00000000-0000-0000-0000-000000000004",
      "title": "Báo cáo: Phần 4",
      "start": "2026-11-23T11:15:00+07:00",
      "end": "2026-11-23T12:15:00+07:00",
      "minutes": 60,
      "reason": "Khoảng trống trong lịch của bạn, xong trước hạn Thứ Tư 25/11, 17:00."
    }
  ],
  "warnings": [],
  "summary": "Mình gợi ý 2 khung giờ cho Báo cáo.",
  "time_zone": "Asia/Ho_Chi_Minh"
}
```

| Trường | Ý nghĩa | Gợi ý hiển thị |
|---|---|---|
| `slots` | Khung giờ gợi ý, theo thứ tự thời gian. Rỗng = không tìm được (xem `warnings`) | Danh sách có checkbox, mặc định chọn hết |
| `slots[].subtask_id` | Việc con của khung này; `null` = việc không có việc con | Gửi lại khi tạo slot |
| `slots[].minutes` | Độ dài khung. Một việc con dài có thể bị chia thành nhiều khung | |
| `slots[].reason` | Một câu lý do (≤ 500 ký tự, vừa với `explanation` của Backend) | Chữ nhỏ |
| `summary` | Một câu tóm tắt | Tiêu đề hộp gợi ý |
| `warnings[].type` | `no_estimate`: việc con của mình chưa có ước lượng, nên không xếp được · `not_enough_time`: không đủ giờ trống trước hạn (`needed_minutes`, `free_minutes`) · `overdue`: việc đã quá hạn · `beyond_window`: hạn còn xa, AI mới xếp trong 7 ngày tới (bấm lại sau) | Dòng cảnh báo cam |

Cách AI tính:
- **Phần việc của mình** gồm việc con đang mở (ToDo / InProgress) có `assigneeId` là mình. Nếu việc con không giao riêng thì tính theo người được giao việc cha. Việc không có việc con thì là chính việc đó.
- **Số phút cần xếp** = ước lượng trừ đi số phút mình **đã có slot** cho phần đó (Approved / Due / InProgress / Overrun / Done). Phần đã xếp đủ thì không gợi ý nữa.
- **Hạn**: lấy hạn của việc. Nếu việc không có hạn thì lấy hạn của nhóm, tính đến cuối giờ làm việc ngày đó. Nếu cả hai đều không có thì AI xếp sớm nhất có thể.
- AI không áp dụng K_bias cho việc nhóm, vì việc nhóm chưa có loại công việc.

### Lưu slot sau khi chọn
AI **không tạo slot**. Người dùng bấm "Thêm vào lịch" thì FE gọi Backend **tuần tự** cho từng khung đã chọn:
```
POST /api/group-tasks/{groupTaskId}/schedule-slots
{
  "subTaskId": "<slots[].subtask_id, bỏ trống nếu null>",
  "startTime": "<slots[].start>",
  "endTime": "<slots[].end>",
  "explanation": "<slots[].reason>",
  "status": "Approved"
}
```
- `409 SLOT_OVERLAP`: lịch đã thay đổi kể từ lúc gợi ý. Bỏ qua khung đó, rồi gọi lại "Gợi ý giờ" cho phần còn thiếu.
- Chi tiết các luật của Backend: `docs/GROUP_FE.md` §11 (repo Backend).

### Lỗi riêng
`404` không có việc hoặc không thuộc nhóm · `403` không có phần nào đang mở giao cho mình · `409` nhóm lưu trữ, việc đã Done / Skipped, hoặc **việc có giờ cố định** (Leader đã đặt giờ; người dùng tạo slot đúng giờ đó bằng tay).

---

## 4. Thay đổi ở màn hình đang có (từ 25/11, AI PR #58)

Slot nhóm (`taskId = null`, có `groupTaskId`) giờ là **giờ bận** với AI:

| Màn hình | Thay đổi | FE cần làm |
|---|---|---|
| Xếp lịch cá nhân (`POST /schedule/propose`) | Không còn đề xuất trùng slot nhóm, nên duyệt không còn bị `409` vì slot nhóm | Không |
| Briefing "Hôm nay" (`GET /briefing/today`) và trợ lý 3D | `schedule[]` có thêm phần tử **`kind: "group"`**, `task_id` = **id việc nhóm** (không phải việc cá nhân), `title` = `Việc: Việc con`. Kiểm tra trễ hạn tính slot nhóm là giờ bận | Thêm `"group"` vào `BriefingScheduleKind` (`api-client.ts`) và chọn icon / màu riêng. Bấm vào thì mở việc nhóm, **không** mở `/tasks/{id}`. Mobile không đọc `kind` nên không phải sửa |
| Chạy quá giờ (`POST /schedule/overrun`) | Gọi được với `slot_id` của **slot nhóm** (trước đây `404`). `moves[]` **không bao giờ** chứa slot nhóm, và slot cá nhân được dời sẽ tránh slot nhóm | Không. `moves[]` vẫn áp dụng bằng `PUT /api/schedule-slots/{id}` như cũ |
| Gợi ý người nhận việc / chat nhóm | `free_minutes`, `free_times` đã trừ slot nhóm của từng người | Không |

---

## 5. Checklist kiểm thử cho FE

| # | Tình huống | Mong đợi |
|---|---|---|
| 1 | Member / Viewer mở việc nhóm, tab chat | Không thấy nút gợi ý / chat |
| 2 | Leader bấm "Gợi ý người làm" | Danh sách xếp hạng, tên đúng người, có lý do có số giờ |
| 3 | Việc có giờ cố định trùng lịch một người | Người đó có cảnh báo "Bận giờ này", xếp cuối |
| 4 | Leader hết lượt AI | Vẫn có xếp hạng, ghi "Hết lượt AI nên không có lý do" |
| 5 | Leader gói Free mở chat nhóm, gửi tin | Thông báo nâng cấp gói (403 `plan_feature_locked`) |
| 6 | Gửi brief `discuss` | Checklist 4 khối |
| 7 | Bấm "Lên kế hoạch" | Bản nháp; mỗi việc có người được gợi ý + lý do |
| 8 | Đổi người ở dropdown rồi Áp dụng | Việc được giao đúng người đã đổi |
| 9 | Áp dụng thành công | Giai đoạn / việc / việc con xuất hiện trong nhóm; nút Áp dụng bị khóa; mở lại chat vẫn khóa |
| 10 | Bấm Áp dụng lần 2 (tab khác) | `409 already_applied`, không tạo trùng |
| 11 | AI lỗi (`502`) | Thông báo thử lại, chữ đã gõ còn nguyên, không mất lượt |
| 12 | Mở lại chat cũ | Thấy đủ tin nhắn, checklist và bản nháp cũ |
| 13 | Member được giao việc nhóm bấm "Gợi ý giờ" | Danh sách khung giờ trong giờ làm việc, không trùng lịch, đúng thứ tự việc con |
| 14 | Người không được giao / Viewer | Không thấy nút "Gợi ý giờ" (`403`) |
| 15 | Chọn 2 khung rồi "Thêm vào lịch" | 2 slot nhóm hiện trong lịch, đồng bộ Google |
| 16 | Bấm "Gợi ý giờ" lần nữa | Không gợi ý lại phần đã xếp đủ |
| 17 | Việc con chưa có ước lượng | Cảnh báo "chưa có ước lượng" |
| 18 | Việc có giờ cố định | Không có nút "Gợi ý giờ" (`409`) |
| 19 | Briefing hôm nay có slot nhóm | Dòng kiểu "nhóm", bấm mở đúng việc nhóm |
| 20 | Xếp lịch cá nhân khi đã có slot nhóm | Không có khung nào trùng slot nhóm |

Hỏi thêm: Hiếu (AI). API Backend của nhóm: `docs/GROUP_FE.md` repo Backend (Việt).
