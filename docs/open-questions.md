# Câu hỏi mở & việc chờ Backend/team

Cập nhật: 2026-10-17. Đây là danh sách DUY NHẤT. Trong plan chỉ đánh dấu ⚠️ cạnh quyết định liên quan.
Chốt xong → ghi kết quả vào `docs/` hoặc plan, rồi chuyển dòng xuống mục "Đã chốt".
Số `#` = số trong tab Problems của Google Doc (nếu có).

## Chờ Backend

| Việc | Liên quan | Ghi chú |
|---|---|---|
| Gọi `POST /learning/task-completed` sau khi lưu task `Done` | FR-17 | Đã thống nhất đường gọi, Backend chưa code |
| Đồng ý AI ghi `UserDomainProfile`, `KBiasUpdateLog` | FR-17, #9 | |
| Đồng ý AI ghi `ScheduleProposal`, `ScheduleSlot` (chỉ `Proposed`), `UserSubscription.AiCallsUsed`, `AIUsageLog` | FR-10, #9 | |
| Gọi `POST /schedule/propose` khi user bấm "Xếp lịch", gửi kèm `language` (vi/en) | FR-10 | DB không có cột ngôn ngữ |
| Xử lý kết quả `/schedule/propose`: 200 có thể có `proposal_id: null` (không có việc cần xếp); 409 = user không có gói `Active`; 429 = hết lượt AI | FR-10 | `task_ids: []` = không chọn task nào; bỏ trường = mọi task |
| FR-11: chỉ duyệt đề xuất `Pending`; bỏ qua đề xuất `Superseded` và slot `Cancelled` | FR-10, FR-11 | Mỗi lần "Xếp lịch" mới, AI chuyển đề xuất `Pending` cũ → `Superseded` |
| Ai reset `AiCallsUsed` khi tới `QuotaResetAt`? | Quota | Giả định: job của Backend |
| Cập nhật entity / migration cho `ScheduleProposal.Status` = `Superseded` và cột `Warnings` | FR-10 | 2026-10-13: AI đã thêm `Superseded` vào CHECK trên DB (Hiếu cho phép, đã báo Backend). Migration của Backend phải giữ giá trị này |
| Thêm unique (`UserId`) WHERE `Status = 'Active'` cho `UserSubscription` | FR-10, Quota | DB chưa chặn 2 gói `Active`; AI tạm lấy dòng `StartAt` mới nhất |
| Đơn vị tiền của `AIUsageLog.Cost` (USD hay VND)? | FR-10, Quota | AI tính từ config giá token |
| ⚠️ Đổi mặc định `UserDomainProfile.KBias` từ `1.250` → `1.300` cho khớp tài liệu | FR-17 | Nhắc lại 2026-10-28 (fix-quota-sync) |
| Ý định của cột `ConfidenceTier` (AI đang tính theo nguồn T_act) | FR-17 | Hỏi người thiết kế ERD |
| Cập nhật script/ERD cho 2 cột Hiếu đã thêm: `ScheduleProposal.Warnings`, `FocusSession.RemainingMinutes`; ghi `RemainingMinutes` ở FR-16 | FR-10 | |
| Ai đổi slot `Proposed` → đã duyệt và ghi `ScheduleChangeLog`? | FR-11 | Giả định: Backend |
| Quota: DB chỉ có 1 bộ đếm `AiCallsUsed`, luật cần đếm riêng 3 loại | Quota, #13 | |
| ⚠️ Gói trả phí hết `EndAt` mà không gia hạn: đổi `Status` → `Expired` và tạo gói Free mới | FR-02, Quota | AI đã tự bỏ qua gói quá `EndAt` (2026-10-28) |
| ⚠️ Gói `Status = 'Active'` nhưng `StartAt` ở tương lai (gặp 2026-10-28, gói `60c9384b…`) — nên là `Scheduled`? | Quota | AI không bị ảnh hưởng (lọc `StartAt <= now`) |
| Đồng ý cách hiểu buffer (khoảng nghỉ riêng, không ghi lên lịch) | FR-10 | |
| Role: không chặn Admin theo role; quyền gọi AI dựa vào gói + quota | — | Hiếu đã quyết, chỉ báo |
| Refresh token: token sống 60', Focus mode dài hơn → Stop bị 401 | Backend + Frontend | Đưa vào tab Problems. 2026-10-14: Backend `develop` đã có refresh token trong `AuthController` → kiểm lại rồi đóng |
| Cập nhật script/entity cho `TaskExecutionMemory`: cột `EmbeddingModel` varchar(100) NULL + `UNIQUE ("TaskId")` (`TaskExecutionMemory_TaskId_key`), bỏ `IX_TaskExecutionMemory_TaskId` | FR-19 #21, #22 | 2026-10-15: AI đã chạy `ALTER` trên DB (Hiếu cho phép, đã báo Backend). Script/entity của Backend phải giữ các thay đổi này |
| Chính sách xóa task: `TaskExecutionMemory`, `FocusSession`, `KBiasUpdateLog` đều `ON DELETE RESTRICT` (task đã có lịch sử thì không xóa được). Cố ý? Đề xuất memory → `CASCADE` | FR-19 #25, FR-17 | `SubTask`, `ScheduleSlot`, `ChatConversation` là `CASCADE` |
| Gọi `POST /learning/task-completed` cả khi task chuyển sang `Skipped` (không chỉ `Done`) | FR-19 #2 | |
| Đồng ý AI ghi `TaskExecutionMemory` | FR-19, #9 | |
| Chuyển tiếp `GET /memory/similar?task_id=&limit=` cho Frontend (đường gọi Frontend → Backend → AI) | FR-09 | Chỉ đọc + ghi `AIUsageLog`; không trừ quota. `search_mode` ∈ `hybrid`/`text_only`/`none` |
| Đồng ý AI ghi `ChatConversation`, `ChatMessage`, `SubTask` (lúc Apply) | FR-08a #8 | Bản nháp nằm trong `ChatMessage.ProposalJson` (có `version`); chỉ AI đọc / ghi định dạng này |
| Chuyển tiếp 3 endpoint chat cho Frontend: `POST /chat/tasks/{id}/messages`, `GET /chat/tasks/{id}`, `POST /chat/tasks/{id}/apply` | FR-08a | 409 có `detail.code`: `task_not_splittable` (+ `reason`), `no_active_plan`, `not_latest_draft`, `has_subtasks`; 502 = AI không trả lời được (không trừ lượt) |
| Quota chat dùng chung `AiCallsUsed` với xếp lịch (1 tin = 1 lượt). Cần đếm riêng từng loại → đếm `AIUsageLog.Feature` | FR-08a #8c | Không đổi schema |
| (Frontend) Tự hiện nút "Chia nhỏ với AI" khi task `Flexible` có `EstimatedMinutes > 120`; không gọi AI | FR-08a #3 | Không tốn lượt |
| Xác nhận nghĩa cột `Task.AiEstimatedMinutes` = số phút AI đoán; AI service ghi cột này (FR-08b-1 Apply), Backend không dùng vào việc khác | FR-17b #2, FR-08b-1 #11 | 2026-10-16: chưa task nào có giá trị. Ước lượng là "của AI" khi `EstimatedMinutes = AiEstimatedMinutes`; user sửa số → tự thành "của user", Backend **không cần** làm gì thêm (#3) |
| `/learning/task-completed` có thêm `reason = "ai_estimate"`; `/memory/similar` mỗi item có thêm `estimate_source` (`user` / `ai`), `ratio = null` khi `ai` | FR-17b | Chỉ thêm, không đổi trường cũ |
| (Frontend) FR-08a không streaming: hiện "AI đang soạn…", câu trả lời < 10 s. Lệch NFR-07 (chữ đầu < 3 s) — streaming để FR-08b | FR-08a #1 | Ghi chú NFR-07 trong Google Doc |
| Đồng ý AI ghi `Milestone`, `Task` (chỉ tạo mới, lúc Apply chat dự án) | FR-08b-1 #13 | `Milestone`: `Name`, `OrderIndex` (= max + 1, khóa `Project`), `DueDate`; `Status` để mặc định `Planned`. `Task`: `Flexible`, `ToDo`, `MilestoneId`, `DomainId`, `Deadline` (23:59 giờ user), `EstimatedMinutes`, `AiEstimatedMinutes`. Không sửa / xóa dòng có sẵn |
| Chuyển tiếp 5 endpoint chat dự án cho Frontend: `POST`/`GET /chat/projects/{id}/conversations`, `POST /chat/conversations/{id}/messages`, `GET /chat/conversations/{id}`, `POST /chat/conversations/{id}/apply` | FR-08b-1 | 409 có `detail.code`: `project_closed`, `already_applied`, `no_active_plan`, `not_latest_draft`, `domain_inactive`; 422 = tin rỗng / > 8.000 ký tự / sửa tay sai luật; 502 = AI không trả lời được (không trừ lượt). Chỉ `messages` tốn lượt |
| Project "đã đóng" = `Status` ∈ {`Completed`, `Archived`} → không chat / Apply. `OnHold` vẫn được | FR-08b-1 #28 | Báo Backend để màn hình project hiện đúng |
| Ai được thêm `Domain` mới? AI chỉ chọn trong danh sách `IsActive` | FR-08b-1 #5 | Đề xuất: Admin / Backend. Domain lộn xộn → K không hội tụ |
| (Frontend) `reminders` của bản nháp = việc giờ cố định (họp, demo…): hiện nút "Tạo task cố định", AI không tạo | FR-08b-1 #13b | Không ghi DB |
| (Frontend) Hiện `estimated_minutes` là "AI đoán — sửa nếu bạn nghĩ khác"; khi Apply gửi lại `key` của task để giữ số AI | FR-08b-1 #11, #21 | Task không có `key` = user tự thêm |
| (Frontend) FR-08b-1 không streaming: hiện "AI đang lên kế hoạch…", mục tiêu p95 < 20 s. Lệch NFR-07 (chữ đầu < 3 s) | FR-08b-1 #23, AC9 | Streaming để FR-08b-2 hoặc bỏ; ghi chú NFR-07 |
| Cập nhật script/entity cho 2 cột mới: `UserCircadianProfile.PriorScore` numeric(4,3) NOT NULL DEFAULT 0.500 (+ CHECK [0, 1]) và `User.ProductiveBlocks` text[] NULL (+ CHECK `CK_User_ProductiveBlocks`) | FR-18 #3, #19 | 2026-10-16: AI đã chạy `ALTER` trên DB (Backend đồng ý, Hiếu cho phép) và chép `PriorScore = Score` cho 132 dòng cũ. Migration của Backend phải giữ 2 cột này |
| Đồng ý AI ghi `UserCircadianProfile` (`Score`, `PriorScore`, `Source`, `UpdatedAt`) và `User.ProductiveBlocks`. Backend **ngừng tự chấm điểm** giờ hiệu quả lúc onboarding | FR-18 #1 | Tạo sẵn 24 dòng 0.5 lúc đăng ký vẫn được (#18), AI ghi đè sau |
| Gọi `POST /learning/productive-hours/recompute` ngay sau mỗi lần Stop timer: **không chờ** kết quả, bỏ qua lỗi | FR-18 #5 | Tính lại từ đầu nên gọi trùng / gọi lỗi tự đúng lại ở lần Stop sau. Trả `{"hours_updated", "sessions_used"}`; 404 = user không còn |
| (Tùy chọn) Gọi recompute khi user đổi gói | FR-18 #17 | Nếu không: hạ gói → điểm đã học còn tới lần Stop kế tiếp |
| Chuyển tiếp cho Frontend: `PUT /profile/productive-hours/onboarding` (`{"blocks": [...]}`) và `GET /profile/productive-hours` | FR-18 #1 | 422 = `blocks` sai (1–5 mục trong `morning`/`midday`/`afternoon`/`evening`/`late_night`, không trùng); 404 = user không còn. Không tốn lượt AI |
| Quan hệ `User.OnboardingCompleted` (cột của Backend) với `ProductiveBlocks`? AI chỉ dùng `ProductiveBlocks` cho `onboarded` | FR-18 #19 | 2026-10-16: 1 user thật có `OnboardingCompleted = true` nhưng `ProductiveBlocks` trống → hiện `onboarded: false` tới khi trả lời lại |
| Chỉ gói `Cao cấp` học từ timer (`PRODUCTIVE_LEARNING_PLANS`). Đổi tên gói trong `SubscriptionPlan` → báo AI sửa config | FR-18 #16 | Gói khác: điểm = điểm onboarding |
| (Frontend) Màn onboarding / Cài đặt: chọn 1–5 khung, nhãn tiếng Việt ("Sáng 6h–11h", "Trưa 11h–14h", "Chiều 14h–18h", "Tối 18h–22h", "Khuya 22h–2h"); không hiện chữ `Score`, `Source` | FR-18 §1 | Nên ẩn `top_hours` khi `onboarded: false` (24 giờ bằng điểm → `[0, 1, 2]`) |
| Chuyển tiếp `GET /briefing/today?language=vi\|en` cho Frontend, gửi `language` = ngôn ngữ giao diện; gọi lúc mở app hoặc khi user bấm xem lại | FR-12 | 404 = user không còn; 422 = `language` sai. Không gọi LLM, không tốn lượt; mỗi lần ≤ 3 lần embedding (ghi `AIUsageLog` `Embedding`). Không lưu / cache bản tóm tắt |
| (Frontend) Màn "Hôm nay": chỉ hiện `text`, dùng `code` để chọn icon / màu. `top_tasks: []` → mời tạo task; `calendar_connected: false` → nhắc kết nối Google; `has_pending_proposal: true` → nhắc duyệt đề xuất; `similar_warning` / `productive_hint` = `null` → ẩn | FR-12 §1 | Giờ trả theo múi giờ user (ISO 8601 có offset). Không cần hiện `remaining_minutes` / `missing_minutes` dạng số trần |
| (Frontend) Lần gọi đầu sau khi server AI khởi động có thể ~5 s (mở kết nối DB + Gemini); các lần sau ~2,5 s từ máy dev | FR-12 AC6 | Nên hiện khung chờ, không chặn màn hình |

## Chờ quyết định (team / giảng viên)

| Câu hỏi | Liên quan | Hiện đang dùng |
|---|---|---|
| Chuyển Gemini sang gói trả phí (trước khi công bố) và đo lại tốc độ chat (cả chat dự án, FR-08b-1 AC9) | FR-08a AC8, #31 | Gói Free: chat trung vị 15 s, p95 21 s, ~10% trả 502 (Gemini > 20 s). Chưa đạt AC8 (< 10 s). FR-08b-1 (2026-10-16, 10 tin / lần): lần 1 trung vị 9.0 s, p95 28.6 s (2/12 lần gọi hỏng); lần 2 trung vị 12.6 s, p95 13.6 s (1/11). Mục tiêu p95 < 20 s: lúc đạt lúc không |
| (Frontend) Chat dự án: ngôn ngữ trả lời do code chọn theo dấu tiếng Việt (tin mới nhất, rồi brief). Gõ tiếng Việt **không dấu** → AI trả lời tiếng Anh | FR-08b-1 #35 | Nếu cần: gửi kèm ngôn ngữ giao diện của user (giống `language` của `/schedule/propose`) |
| K ban đầu lấy từ onboarding hay cố định 1.3? | #10 | 1.3 |
| Ngưỡng outlier | #12 | 5 (ngoài [1/5, 5] thì bỏ qua) |
| Quota đọc tài liệu theo từng gói | #13 | — |
| Chọn model LLM (FR-08) | — | FR-10 dùng Gemini, tên model trong config |
| Database doc lệch với schema Supabase | #19 | Tin DB thật |
| Đo lại tốc độ recompute (FR-18 AC7) khi đã deploy server cùng vùng với Supabase | FR-18 AC7 | 2026-10-16: phía server (DB + Python) p95 20 ms; đo từ máy dev ~380 ms vì mạng (1 transaction rỗng ~390 ms). `scripts/try_productive_hours.py` in cả hai số |
| `PRODUCTIVE_PRIOR_WEIGHT` = 5: 1 giờ cần ≥ 13 phiên tốt để vượt khung onboarding 0.8 khi điểm gốc 0.3. Có muốn dữ liệu thật chiếm ưu thế nhanh hơn? | FR-18 #2, AC5 | Chỉ đổi config, không đổi code |
| FR-10 slot không có lời giải thích khi chạy thử FR-18 (2026-10-16): Gemini không trả lời kịp? | FR-10 | Dữ liệu thử đã xóa nên chưa xem `AIUsageLog`. Có thể cùng nguyên nhân Gemini gói Free chậm ở trên |
| Dọn dữ liệu test còn sót: 4 user `@example.invalid` (từ `scripts/try_schedule.py`) còn dòng `UserCircadianProfile` (14–16h) và 7 `FocusSession` | FR-18 bước 0 | Chưa xóa (không phải dòng FR-18 tạo). Sẽ xóa hết trước khi công bố |
| FR-12: các điểm Claude Code tự chọn khi code (Hiếu duyệt rồi ghi vào plan §5) | FR-12 | (1) task có subtask: cộng các subtask đang mở · (2) Fixed `Skipped` không hiện · (3) slot subtask = "Task: Subtask" · (4) `deadline_soon` luôn là ứng viên, xếp cuối, không có khi quá hạn · (5) chỉ có việc `Skipped` → "Trước đây bạn từng bỏ dở việc tương tự." · (6) giờ rảnh không trừ timer đang chạy, làm tròn `SCHEDULE_ROUND_MINUTES` · (7) `asyncio.wait` thay `wait_for` (giữ kết quả đã xong) · (8) `missing_minutes` tính riêng từng task · (9) số thập phân tiếng Việt dùng dấu phẩy · (10) giờ hiệu quả đang diễn ra vẫn tính · (11) slot `Proposed` chỉ bật cờ khi chưa kết thúc · (12) task cần 0 phút không vào top |
| FR-12: task nguy hiểm / đang làm thường đã đủ 2 lý do → câu `bias` bị cắt (đúng thứ tự #6). Có muốn `bias` luôn hiện? | FR-12 #6, #7 | Theo plan: tối đa 2 lý do |
| Đo lại FR-12 AC6 khi đã deploy | FR-12 AC6 | 2026-10-17 từ máy dev: có embedding trung vị 2,4 s (lần đầu 5,5 s); phía server không embedding p95 8,5 ms |
| Ngưỡng `SIMILAR_MIN_SIMILARITY` (FR-09 #4) | FR-09 | 0.5. Chạy thật 2026-10-15: task không liên quan vẫn qua ngưỡng (Gemini cho câu khác nghĩa ~0.69) → cân nhắc ~0.75 khi có thêm dữ liệu |

## Cập nhật Google Doc

- α = 0.2, K ban đầu = 1.3 (nếu Doc còn ghi 0.3 / 1.25).

## Đã chốt

| Ngày | Câu hỏi | Kết quả |
|---|---|---|
| 2026-10-07 | JWT | HS256, user ID trong `sub`, iss `METANOIA`, aud `METANOIA.Client` |
| 2026-10-11 | Đường gọi | Frontend → Backend → AI |
| 2026-10-12 | DB dev riêng? | Không — làm thẳng trên Supabase, xóa dữ liệu test trước khi công bố |
| 2026-10-14 | Giảng viên duyệt Python + service riêng? (#8) | Đã duyệt: Python, service riêng |
| 2026-10-14 | Chọn model embedding | `gemini-embedding-2`, cắt xuống 1536 chiều (khớp ERD). Dự phòng: OpenAI `text-embedding-3-small` (FR-19 #1) |
