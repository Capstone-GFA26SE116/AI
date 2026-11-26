# FR-22d — Chèn việc gấp, dời sang mai, xếp lại việc bị lỡ (M3, M4, M7, M10, M18)

Trạng thái: Đang code · Branch: `feature/fr22d-urgent-block` · Cập nhật: 2026-11-26

<!-- Hiếu chốt 2026-11-26 (chat): (1) chỉ dời lịch của Metanoia, không đụng sự kiện gõ thẳng trên Google;
(2) việc gấp trùng việc Fixed → báo, so sánh mức quan trọng, gợi ý; user chọn hủy hoặc ghi đè;
(3) đợt đầu làm M3 + M4 + M7 + M10 + M18 ("làm theo lộ trình của bạn"). Claude Code soạn plan rồi làm liền
(workflow tự làm). ⚠️ = báo Backend / Mobile. -->

## 0. Mục tiêu
- **Làm gì:** 3 đề xuất dời lịch tính bằng code (không LLM, không ghi DB), dùng được từ màn hình lẫn trợ lý giọng nói:
  - **M3 + M4 — Chèn việc gấp:** "chiều nay 14h–16h có việc gấp" → dời các slot bị đè và đẩy lùi các slot sau
    trong ngày, giữ thứ tự; nếu trùng việc không dời được (Fixed, sự kiện Google, việc nhóm, slot đang bấm giờ)
    → báo trùng, so sánh mức quan trọng, gợi ý 1 trong 3: **giờ khác** / **ghi đè** / **hủy**.
  - **M7 — Để mai làm:** "bỏ việc này, để mai làm" → slot đó sang giờ trống sớm nhất của ngày làm việc kế tiếp.
  - **M10 — Xếp lại việc bị lỡ:** slot đã qua giờ mà chưa làm → xếp lại từ bây giờ, giữ thứ tự, kiểm tra hạn chót.
  - **M18 — Hoàn tác:** mọi đề xuất trả kèm giờ cũ; Mobile giữ nút "Hoàn tác" vài phút sau khi áp dụng.
- **Ai gọi, khi nào:** Mobile / Web gọi thẳng 3 endpoint mới (xem trước) ; trợ lý giọng nói (FR-22) gọi chúng
  bên trong và trả `kind = "confirm"` + `action` có sẵn bản xem trước. Người dùng nói / bấm "Có" → **Mobile ghi qua Backend**.
- **Tiêu chí xong:**
  - [ ] AC1 — Chèn việc gấp không trùng gì: slot Flexible bị đè và các slot sau nó trong ngày được đẩy lùi đúng thứ tự,
        giữ độ dài, có buffer, tròn 5', bỏ qua giờ nghỉ trưa; dừng đẩy khi slot sau không còn bị đụng.
  - [ ] AC2 — Không bao giờ dời: task Fixed, sự kiện Google, slot việc nhóm, slot đã bấm giờ / đang chạy / xong / hủy (quyết định 1).
  - [ ] AC3 — Trùng (M4): trả `conflicts` (loại, tên, giờ, quan trọng hay không + lý do), `options`, `recommendation`,
        `other_time` (khoảng trống cùng độ dài gần nhất trong cùng ngày, không trùng gì). Ghi đè task Fixed → `skip_task_ids`.
  - [ ] AC4 — M7: slot sang giờ trống sớm nhất từ ngày làm việc kế tiếp; không kịp hạn chót → `not_placed`.
        Slot đã bấm giờ / Fixed / việc nhóm / không còn trên lịch → 409.
  - [ ] AC5 — M10: slot `Missed`, hoặc `Approved` / `Due` đã hết giờ mà chưa bấm giờ, của task chưa xong, trong
        `CATCH_UP_LOOK_BACK_DAYS` ngày → đặt lại từ bây giờ theo thứ tự cũ; không kịp hạn → `not_placed`.
  - [ ] AC6 — Mỗi slot bị dời có giờ cũ + giờ mới (M18). Không gọi LLM, không trừ lượt, không ghi DB.
  - [ ] AC7 — Trợ lý: 3 công cụ mới `insert_block`, `postpone_task`, `catch_up` → `confirm` + `action` (kèm bản xem trước);
        câu trả lời ≤ 60 từ (vi / en), có số việc bị dời và việc trùng. Các công cụ cũ không đổi; `pytest` xanh.
  - [ ] AC8 — Chạy thật: tạo dữ liệu thử, gọi 3 endpoint + 3 câu nói qua Gemini, xóa đúng các dòng đã tạo, còn 0.
- **Ngoài phạm vi:** Backend thực thi (đã có API) · sửa / xóa sự kiện Google · tạo task Flexible bằng giọng (M14) ·
  M1, M2, M5, M6, M8, M9 (đợt sau).

## 1. Bên ngoài — nói chuyện với ai

### API (đều `POST` vì có body; **không ghi** gì, không LLM, không quota, mọi gói dùng được như FR-16b)
| Method | Endpoint | Request | Response | Lỗi |
|---|---|---|---|---|
| POST | `/schedule/insert-block` | `{ title, start, end, language? }` | 200 `InsertPlan` | 401 · 422 (giờ đã qua, end ≤ start, dài > 12 h, quá cửa sổ 7 ngày) |
| POST | `/schedule/postpone` | `{ slot_id, language? }` | 200 `PostponePlan` | 401 · 404 · 409 (slot không dời được) |
| POST | `/schedule/catch-up` | `{ language? }` | 200 `CatchUpPlan` | 401 |

```json
// InsertPlan
{ "block": {"title": "…", "start": "…", "end": "…"}, "past_day_end": false,
  "project_id": "…|null", "domain_id": "…|null",              // gợi ý để Mobile tạo task Fixed
  "moves": [{"slot_id","task_title","old_start","old_end","new_start","new_end"}],
  "not_placed": [{"slot_id","task_title"}],
  "conflicts": [{"kind": "fixed_task|calendar_event|group_slot|started_slot", "id": "…|null",
                 "title": "…", "start": "…", "end": "…", "important": true, "why": "…"}],
  "options": ["other_time", "overwrite", "cancel"],            // chỉ khi có conflicts
  "recommendation": "other_time|overwrite|null",
  "other_time": {"start": "…", "end": "…"} | null,
  "skip_task_ids": ["…"],                                      // task Fixed bị ghi đè → Mobile đặt Skipped
  "summary": "…" }
// PostponePlan
{ "slot_id": "…", "task_title": "…", "move": {…} | null, "not_placed": bool, "summary": "…" }
// CatchUpPlan
{ "moves": [{"slot_id","task_id","subtask_id","task_title","old_start","old_end","new_start","new_end"}],
  "not_placed": [{"slot_id","task_title"}], "summary": "…" }
```

### Mobile làm gì sau "Có" ⚠️ (ghi `docs/open-questions.md`, `docs/GROUP_AI_FE.md` không đổi)
- **Chèn việc gấp:** `POST /api/projects/{project_id}/tasks` (`TaskType = Fixed`, `FixedStart/End`, `DomainId`)
  → `PUT /api/schedule-slots/{id}` cho từng `moves` → nếu ghi đè: `PUT /api/tasks/{id}` `Status = Skipped` cho `skip_task_ids`.
  `project_id` / `domain_id` null → Mobile hỏi user chọn. Chọn "giờ khác" → gọi lại `/schedule/insert-block` với `other_time`.
- **Để mai / Xếp lại:** `PUT /api/schedule-slots/{id}` giờ mới, `Status = Approved`, `OriginalStartTime` = giờ cũ.
  ⚠️ Backend cần cho `Missed` → `Approved` qua `PUT` (chưa kiểm).
- **Hoàn tác (M18):** giữ 5 phút: `PUT` lại giờ cũ, xóa task Fixed vừa tạo, trả `Status` cũ cho task bị ghi đè.

### Database (chỉ đọc)
- `ScheduleSlot` (cá nhân + nhóm), `Task` (`TaskType`, `Status`, `Deadline`, `FixedStart/End`, `Title`), `Project`
  (`ClientId`, `IsDefault`), `FocusSession` (đã bấm giờ), `GoogleCalendarEvent` (`Title`), `User` (giờ làm).
- Ghi: **không có**.

### Dịch vụ khác
- Endpoint: không LLM, không Backend. Trợ lý: 1 lượt Gemini để hiểu câu (như FR-22), prompt `assistant_router_v5`.

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Dời gì | Chỉ slot Flexible `Approved` / `Due` chưa bấm giờ của Metanoia | Hiếu chốt 2026-11-26 (1); như FR-16b AC3 |
| 2 | Việc gấp lưu thế nào | Task **Fixed** do Mobile tạo qua Backend | Fixed = giờ do người khác / việc khẩn đặt, không học K; FR-10 tự coi là giờ bận |
| 3 | Đẩy lùi hay chỉ dời slot bị đè | Đẩy lùi theo thứ tự (như FR-16b), **dừng** khi slot sau không còn bị đụng | Đúng ý "dời các lịch phía sau"; giữ thứ tự hạn chót FR-10; ít thay đổi nhất |
| 4 | "Quan trọng" (M4) tính sao | Code, không LLM: task Fixed của dự án có khách, sự kiện Google (có thể có người khác), việc nhóm (người khác chờ), slot đang bấm giờ → quan trọng; task Fixed của dự án không có khách → không | Không tốn lượt, giải thích được; LLM không biết thêm gì |
| 5 | Gợi ý | Có `other_time` cùng ngày → gợi ý **giờ khác**; không có → **ghi đè** (quan trọng thì kèm lời nhắc báo khách / nhóm) | User đã nói là gấp; "hủy" luôn có nhưng không gợi ý |
| 6 | Ghi đè sự kiện Google / việc nhóm / slot đang chạy | Không sửa chúng (quyết định 1): Google + nhóm giữ nguyên, câu nhắc tự xử lý; slot đang bấm giờ → không có `overwrite` | AI service không ghi Google; dừng timer là việc khác |
| 7 | Dự án / loại việc của task Fixed | `project_id` = dự án mặc định (`IsDefault`) nếu có; `domain_id` = loại việc của task gần nhất của user; không có → null, Mobile hỏi | DB có `DomainId` bắt buộc; 48/49 dự án không phải mặc định |
| 8 | M7 "mai" | Ngày làm việc kế tiếp (bỏ cuối tuần nếu user không làm), giờ trống sớm nhất; hết chỗ → ngày sau, tới hết cửa sổ | Đúng câu nói, đoán được |
| 9 | M10 lấy slot nào | `Missed`; `Approved` / `Due` đã hết giờ, chưa bấm giờ; task chưa `Done` / `Skipped`; `CATCH_UP_LOOK_BACK_DAYS` = 2 | Job đổi trạng thái của Backend có thể trễ |
| 10 | Lưu đề xuất | Không (như FR-16b #5) | Tính lại rất nhanh |
| 11 | Hoàn tác (M18) | Mobile làm bằng giờ cũ trong response; AI không lưu gì | Không cần Backend sửa |
| 12 | Trợ lý: câu không đủ giờ | `insert_block` thiếu giờ bắt đầu → trả lời hỏi lại ("Mấy giờ bắt đầu?"), không `action`; thiếu giờ kết thúc → mặc định 60' | Không đoán giờ bắt đầu |

## 2. Bên trong — logic

### Code nằm ở đâu
- domain: `reshuffle.py` (thuần: `plan_insert`, `plan_postpone`, `plan_catch_up`, quan trọng / gợi ý) ·
  câu tóm tắt trong `assistant_text.py` (`insert_text`, `postpone_text`, `catch_up_text`).
- application: port `ports/reshuffle_repository.py` · `use_cases/reshuffle.py` (3 use case).
- infrastructure: `db/reshuffle_repository.py` (SQL đọc, dùng lại truy vấn của `overrun_repository.py`).
- api: `routes/schedule.py` thêm 3 endpoint · `routes/assistant.py` `action.preview` · `deps.py`.
- trợ lý: `assistant_tools.py` (3 công cụ + `start_time`, `end_time`), prompt `assistant_router_v5.md`, `assistant_turn.py`.
- config: `CATCH_UP_LOOK_BACK_DAYS` (2), `INSERT_BLOCK_MAX_MINUTES` (720).

### Luồng chèn việc gấp
1. Kiểm block: `start ≥ now − 5'`, `end > start`, ≤ 720', trong cửa sổ → không thì 422.
2. Đọc slot (cửa sổ từ đầu ngày của block), task Fixed, sự kiện Google, giờ làm, giờ nghỉ.
3. `conflicts` = mọi thứ không dời được mà chồng block: task Fixed (không Skipped), sự kiện Google (không all-day /
   hủy / do Metanoia tạo), slot nhóm trên lịch, slot cá nhân không dời được (đã bấm giờ / đang chạy).
4. `affected` = slot dời được cùng ngày có `end > block.start`, theo giờ bắt đầu. Đi lần lượt:
   `earliest = round_up(block.end + buffer)`. Slot bắt đầu ≥ `earliest` và còn trống → giữ nó và mọi slot sau (dừng).
   Ngược lại: đặt vào khoảng trống sớm nhất ≥ `earliest` (giờ bận = mọi thứ trừ `affected` chưa xét, + block + giờ nghỉ),
   giữ độ dài, trước hạn chót; không được → `not_placed`. `earliest = placed.end`.
5. Có conflicts → quan trọng (#4), `other_time` (khoảng trống cùng độ dài sớm nhất ≥ now, cùng ngày, chỉ tính giờ bận
   không dời được + giờ nghỉ; khác block), `options`, `recommendation` (#5), `skip_task_ids`.
6. `project_id`, `domain_id` (#7). Câu tóm tắt.

### Luồng để mai (M7)
1. Slot của user, `Approved` / `Due`, Flexible, chưa bấm giờ → không thì 404 / 409.
2. `earliest` = đầu ngày làm việc kế tiếp; khoảng trống sớm nhất (giờ bận = mọi thứ khác + giờ nghỉ), trước hạn chót.

### Luồng xếp lại việc bị lỡ (M10)
1. Slot lỡ (#9) theo giờ bắt đầu; giờ bận = mọi slot khác trên lịch + Fixed + Google + giờ nghỉ.
2. Đặt lần lượt từ `round_up(now)`, giữ thứ tự (như FR-16b `_place_in_order`), trước hạn chót.

### Trợ lý
- `insert_block(period, start_time, end_time, name)`: không có `start_time` → hỏi lại (#12). Chạy use case chèn;
  `confirm` + `action{type: insert_block, preview}`; câu nói = summary.
- `postpone_task(name)`: không tên → slot đang diễn ra / sắp tới (30') của user; tên → khớp task như FR-22c, lấy slot
  sắp tới gần nhất của task. Không có slot → trả lời, không `action`.
- `catch_up`: không có việc lỡ → trả lời; có → `confirm`.

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | domain | Block đè 1 slot, sau còn 2 slot sát nhau / có khoảng trống | đẩy lùi đúng thứ tự; dừng khi hết đụng; tròn 5', buffer, bỏ giờ nghỉ | AC1 |
| T2 | domain | Block đè slot bắt đầu trước block | slot đó dời ra sau block | AC1 |
| T3 | domain | Fixed / Google / nhóm / slot đã bấm giờ trong và sau block | không dời; trong block → conflicts | AC2, AC3 |
| T4 | domain | Quan trọng: Fixed có khách / không khách, Google, nhóm, đang chạy; có / không `other_time` | `important`, `options`, `recommendation`, `skip_task_ids` đúng | AC3 |
| T5 | domain | Slot không kịp hạn chót sau khi đẩy | `not_placed` | AC1 |
| T6 | domain | M7: mai còn chỗ / hết chỗ / cuối tuần / không kịp hạn | đúng ngày; `not_placed` | AC4 |
| T7 | domain | M10: 3 slot lỡ, 1 không kịp hạn, task đã Done | thứ tự giữ, `not_placed`, bỏ task Done | AC5 |
| T8 | domain | Câu tóm tắt vi / en mọi trường hợp | ≤ 60 từ, không tên nội bộ | AC7 |
| T9 | application | Fake repo: 404 / 409 / 422, 3 use case | đúng; repo không có hàm ghi | AC4, AC6 |
| T10 | infrastructure | SQL đọc slot của user khác | không thấy | AC2 |
| T11 | api | 401, 422, 404, 409, 200 cho 3 endpoint | đúng | — |
| T12 | trợ lý | parse 3 công cụ; thiếu giờ → hỏi lại; `confirm` + `preview`; công cụ cũ không đổi | đúng | AC7 |
| T13 | chạy thật | script tạo dữ liệu thử, 3 endpoint + 3 câu nói, xóa, còn 0 | đúng đề xuất | AC8 |

## 4. Các bước code
- [ ] **1.** `domain/reshuffle.py` — T1–T7.
- [ ] **2.** Câu tóm tắt trong `assistant_text.py` — T8.
- [ ] **3.** Port + use case + fake repo — T9.
- [ ] **4.** `db/reshuffle_repository.py` — T10.
- [ ] **5.** Route + deps + config — T11.
- [ ] **6.** Trợ lý: công cụ, prompt v5, `assistant_turn.py`, `action.preview` — T12.
- [ ] **7.** Chạy thật — T13.
- [ ] **8.** Docs: `CLAUDE.md` §6, `docs/open-questions.md` (Mobile / Backend).

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-11-26 | Tạo plan | Hiếu chọn M3, M4, M7, M10, M18; chốt (1) chỉ dời lịch Metanoia, (2) trùng Fixed → báo + gợi ý, user hủy / ghi đè |
