# Database — phần AI service đụng tới

Cập nhật: 2026-10-16 · Nguồn: code Backend `develop` + đọc trực tiếp Supabase (chỉ đọc).
Schema do **Backend** sở hữu. File này chỉ ghi những bảng/cột AI dùng. Thấy lệch với DB thật → báo Hiếu.

Ký hiệu: **AI ghi** = AI service được INSERT/UPDATE · **Chỉ đọc** = không bao giờ ghi.
⚠️ = chưa xác nhận đủ / chưa chốt với Backend (xem `open-questions.md`).

## Bảng AI ghi

### `UserDomainProfile` — AI ghi (FR-17)
- Unique (`UserId`, `DomainId`).
- `KBias` numeric(5,3), > 0, mặc định DB 1.250 ⚠️ (AI luôn ghi rõ K ban đầu từ config, không dựa vào mặc định).
- `SampleCount` ≥ 0, mặc định 0. `UpdatedAt`.
- Backend không tự tạo dòng nào → AI tạo khi task đầu tiên của (user, domain) được học.

### `KBiasUpdateLog` — AI ghi (FR-17)
- `TaskId` **unique** (mỗi task học 1 lần). Unique (`UserDomainProfileId`, `SampleIndex`).
- `OldKBias`, `NewKBias` numeric(5,3) · `Alpha` numeric(3,2), trong (0, **0.30**] ·
  `ObservedRatio` numeric(6,3) · `EstimatedMinutes` > 0 · `ActualMinutes` > 0 · `SampleIndex` ≥ 1.
- `CaptureMethod` ∈ {`Timer`, `SelfReported`, `AutoTimeout`} · `ConfidenceTier` ∈ {`High`, `Medium`, `Low`}.
- `CreatedAt` mặc định now().
- Index (đọc 2026-10-29, FR-20 bước 0): `TaskId` (unique), (`UserDomainProfileId`, `SampleIndex`) (unique). **Không có** (`UserDomainProfileId`, `CreatedAt`);
  lọc theo profile vẫn dùng được cột đầu của index unique. 2 dòng.

### `ScheduleProposal` — AI ghi (FR-10) ⚠️
- Ghi khi đề xuất: `Status = 'Pending'`, `Level = 3`.
- `UserId` NOT NULL · `Level` ∈ {1, 2, 3}, mặc định 3 · `IsFeasible` NOT NULL, mặc định true ·
  `MissingMinutes` trống hoặc > 0, **bắt buộc có khi `IsFeasible = false`** · `Explanation` text NOT NULL ·
  `Warnings` jsonb, trống hoặc mảng (Hiếu thêm 2026-10-07) · `CreatedAt` mặc định now().
- `BlindTestSessionId`, `DisplayPosition`, `IsChosen`: cho FR-13, AI không ghi.
- `Status` ∈ {`Pending`, `Approved`, `PartlyApproved`, `Rejected`, `Superseded`}.
  ⚠️ `Superseded` do AI thêm thẳng vào DB 2026-10-13 (Hiếu cho phép, đã báo Backend):
  Backend phải cập nhật entity / migration, nếu không migration sau có thể xoá mất giá trị này.

### `ScheduleSlot` — AI ghi, chỉ slot trạng thái `Proposed` (FR-10)
- `TaskId` NOT NULL · `SubTaskId?` · `ScheduleProposalId?` · `StartTime`, `EndTime` timestamptz, `EndTime > StartTime`.
- `Status` ∈ {`Proposed`, `Approved`, `Done`, `Skipped`, `Missed`, `Cancelled`}, mặc định `Proposed`.
- `ProposedMinutes` > 0 (phần làm việc) · `KBiasUsed` numeric (gói Free: trống) · `Explanation` varchar.
- `OriginalStartTime`, `IsManualOverride`, `GoogleEventId`, `SyncFailed`: của Backend (FR-11), AI không ghi.
- Đổi sang đã duyệt: việc của Backend (FR-11).

### `AIUsageLog` — AI ghi (mỗi lần gọi LLM 1 dòng)
- `UserSubscriptionId` **NOT NULL** (user không có gói → không ghi được) · `Feature` ∈ {`Chat`, `Schedule`, `Briefing`, `Embedding`}.
- `Model` NOT NULL · `InputTokens`, `OutputTokens` ≥ 0 · `Cost` numeric NOT NULL ⚠️ (đơn vị tiền chưa chốt) ·
  `LatencyMs` ≥ 0, NOT NULL · `Succeeded` NOT NULL · `QuotaCharged` mặc định false · `CreatedAt` mặc định now().

### `TaskExecutionMemory` — AI ghi (FR-19) ⚠️
Đọc trực tiếp DB 2026-10-15 (chỉ đọc). Bảng đang trống.
- `Id` uuid · `TaskId` uuid NOT NULL · `SubTaskId` uuid trống được · `Content` text NOT NULL ·
  `Outcome` varchar(20) NOT NULL ∈ {`Completed`, `Partial`, `NotDone`} ⚠️ ·
  `EstimatedMinutes` int NOT NULL > 0 · `ActualMinutes` int NOT NULL ≥ 0 ·
  `Embedding` **vector(1536)** trống được · `EmbeddingStatus` varchar(10) ∈ {`Pending`, `Done`, `Failed`}, mặc định `Pending` ·
  `CreatedAt` mặc định now().
- CHECK: `EmbeddingStatus = 'Done'` thì bắt buộc có `Embedding`.
- `EmbeddingModel` varchar(100) trống được: tên model đã tạo vector (FR-19 #21).
- **Unique** `TaskId` (`TaskExecutionMemory_TaskId_key`): mỗi task 1 memory (FR-19 #22).
  Hai mục này AI thêm bằng `ALTER` 2026-10-15 (Hiếu cho phép, đã báo Backend); bỏ index thường `IX_TaskExecutionMemory_TaskId` (thừa).
- **Không có** cột tỷ lệ, domain, `UpdatedAt` (tính lúc đọc, #24).
- Khóa ngoại `TaskId` → `Task`, `SubTaskId` → `SubTask`: **`ON DELETE RESTRICT`** ⚠️ (task còn memory thì không xóa được task).
- Index (đọc lại 2026-10-15, FR-09 bước 0):
  - HNSW `IX_TaskExecutionMemory_Embedding_Hnsw` (`"Embedding" vector_cosine_ops`), tham số mặc định (`m` = 16, `ef_construction` = 64).
  - GIN `IX_TaskExecutionMemory_Content_Fts` trên `to_tsvector('simple', "Content")` → câu SQL phải ghi rõ `'simple'`:
    config mặc định của DB là `english`, thiếu `'simple'` thì Postgres không dùng index.
  - `EmbeddingStatus <> 'Done'` (btree, partial) · unique `TaskId`.
- Extension `vector` 0.8.2: `SET LOCAL hnsw.iterative_scan = relaxed_order` chạy được trong transaction chỉ đọc
  (mặc định `hnsw.ef_search` = 40, `hnsw.max_scan_tuples` = 20000).

### `ChatConversation` — AI ghi (FR-08a) ⚠️
Đọc DB 2026-10-15 (chỉ đọc, FR-08a bước 0). Bảng đang trống.
- `Id` uuid · `ProjectId` uuid **NOT NULL** · `TaskId` uuid trống được · `Scope` varchar(10) ∈ {`Project`, `Task`} ·
  `Title` varchar(200) **NOT NULL** · `Status` varchar(10) ∈ {`Open`, `Applied`, `Closed`}, mặc định `Open` · `CreatedAt`.
- CHECK: `Scope = 'Task'` ⇔ có `TaskId`; `Scope = 'Project'` ⇔ `TaskId` trống.
- **Không có** unique trên `TaskId` (chỉ index thường `IX_ChatConversation_TaskId`) ⚠️ → DB không chặn 2 cuộc chat cho 1 task.
- FK `ProjectId` → `Project`, `TaskId` → `Task`: `ON DELETE CASCADE`. Index (`ProjectId`, `CreatedAt`).

### `ChatMessage` — AI ghi (FR-08a) ⚠️
- `Id` uuid · `ConversationId` uuid NOT NULL · `Sender` varchar(10) ∈ {`User`, `AI`} · `Content` text NOT NULL ·
  `ProposalJson` **jsonb** trống được · `IsApplied` bool NOT NULL mặc định false · `CreatedAt`.
- CHECK: chỉ tin `AI` mới có `ProposalJson`.
- Unique `UX_ChatMessage_OneAppliedPerConversation` (`ConversationId`) WHERE `IsApplied` → mỗi cuộc chat tối đa 1 bản nháp đã áp dụng.
- **Không có** cột `kind`: `question` / `draft` phân biệt bằng `ProposalJson` trống hay không. Không có cột model / token.
- FK `ConversationId` → `ChatConversation`: `ON DELETE CASCADE`. Index (`ConversationId`, `CreatedAt`).

### `SubTask` — AI ghi lúc Apply (FR-08a) ⚠️
- `Id` uuid · `TaskId` NOT NULL · `Title` varchar(200) NOT NULL · `EstimatedMinutes` int NOT NULL > 0 ·
  `OrderIndex` int NOT NULL ≥ 1 · `Status` varchar(10) ∈ {`ToDo`, `Done`, `Skipped`}, mặc định `ToDo`. Không còn cột bắt buộc nào khác.
- Unique (`TaskId`, `OrderIndex`) `DEFERRABLE INITIALLY DEFERRED`. FK `TaskId` → `Task`: `ON DELETE CASCADE`.
- Không có deadline riêng (dùng deadline task cha).

### `ChatConversation`, `ChatMessage` — cũng dùng cho chat dự án (FR-08b-1)
- `Scope = 'Project'`, `TaskId` trống. Mọi truy vấn chat dự án lọc `Scope = 'Project'`.
- `ProposalJson` của bản nháp dự án có `"version": "project_v1"` (bản nháp FR-08a: `version` = 1).

### `ChatAttachment` — AI ghi (FR-08b-2) ⚠️
Bảng mới, **AI tạo 2026-10-28** (Hiếu cho phép, đã báo Backend; migration của Backend phải khớp).
- `Id` uuid PK · `MessageId` uuid NOT NULL FK → `ChatMessage` `ON DELETE CASCADE` ·
  `DocumentId` uuid trống được FK → `ProjectDocument` `ON DELETE SET NULL` ·
  `Source` varchar(10) ∈ {`Hub`, `Upload`} · `Name` varchar(255) NOT NULL · `ExtractedText` text NOT NULL ·
  `IsTruncated` bool NOT NULL mặc định false · `CreatedAt` timestamptz mặc định now() (AI ghi `clock_timestamp()`).
- CHECK `CK_ChatAttachment_UploadHasNoDocument`: `Source = 'Upload'` ⇒ `DocumentId IS NULL`.
  Index `IX_ChatAttachment_MessageId`. Bật RLS như các bảng khác.
- Chỉ gắn với tin của **user** trong chat dự án. `ExtractedText` = chữ đã cắt theo ngân sách, **chưa che**
  (che lúc ghép prompt). Không trả về qua API.

### `Milestone` — AI ghi lúc Apply (FR-08b-1) ⚠️
Đọc DB 2026-10-16 (chỉ đọc, FR-08b-1 bước 0). Đang có 2 dòng.
- `Id` uuid · `ProjectId` uuid NOT NULL · `Name` **varchar(150)** NOT NULL (plan gọi là `title`) ·
  `OrderIndex` int NOT NULL ≥ 1 · `DueDate` **date** trống được (plan gọi là `deadline`) ·
  `Status` varchar ∈ {`Planned`, `InProgress`, `Delivered`, `InRevision`, `Approved`}, mặc định `Planned` ·
  `DeliveredAt`, `ApprovedAt` timestamptz trống được · `RevisionCount` int NOT NULL ≥ 0, mặc định 0.
- AI chỉ ghi `ProjectId`, `Name`, `OrderIndex`, `DueDate`; các cột khác để mặc định.
- Unique `UQ_Milestone_ProjectId_OrderIndex` (`ProjectId`, `OrderIndex`) `DEFERRABLE INITIALLY DEFERRED`.
  FK `ProjectId` → `Project`: `ON DELETE CASCADE`. **Không có** `CreatedAt`.

### `Task` — AI ghi lúc Apply (FR-08b-1) ⚠️
- Chỉ tạo mới task `Flexible`, `ToDo`: `ProjectId`, `MilestoneId`, `DomainId`, `Title`, `Deadline`, `EstimatedMinutes`,
  `AiEstimatedMinutes`. Không sửa task có sẵn. Cột và CHECK: xem `Task` ở mục chỉ đọc.

### `UserSubscription.AiCallsUsed` — AI ghi (bộ đếm quota)
- Chỉ 1 bộ đếm cho mọi loại ⚠️ (luật cần đếm riêng 3 loại). ≥ 0. Xem bảng `UserSubscription` bên dưới.

## Bảng chỉ đọc

### `Task` (chỉ đọc, trừ lúc Apply FR-08b-1)
- **Không có `UserId`** → chủ sở hữu qua `Project.UserId` (luôn JOIN `Project`).
- Cột (đọc DB 2026-10-16): `Id` · `ProjectId` NOT NULL · `MilestoneId` trống được (FK → `Milestone`, `ON DELETE SET NULL`) ·
  `DomainId` NOT NULL (FK `ON DELETE RESTRICT`) · `Title` **varchar(200)** NOT NULL · `Description` · `TaskType` mặc định `Flexible` ·
  `Status` mặc định `ToDo` · `CreatedAt` mặc định now() · `CompletedAt` · `Deadline` · `EstimatedMinutes` · `AiEstimatedMinutes` ·
  `FixedStart`, `FixedEnd`. **Không có** `OrderIndex`, `Priority`.
- `TaskType` ∈ {`Flexible`, `Fixed`} · `Status` ∈ {`ToDo`, `Scheduled`, `InProgress`, `Done`, `Skipped`}.
- Task `Flexible` **bắt buộc** có `Deadline` và `EstimatedMinutes` > 0.
- Task `Fixed` **bắt buộc** có `FixedStart`, `FixedEnd` (timestamptz, `FixedEnd > FixedStart`).
- `Title`, `Description` (text, trống được), `DomainId`, `CompletedAt` (timestamptz, trống được). Không có CHECK nối `CompletedAt` với `Status`.
  2026-10-15: 2 task `Done` đều có `CompletedAt`; chưa có task `Skipped` nào nên chưa biết Backend có ghi không → coi là trống được.
  2026-10-29 (FR-20 bước 0): 5 task `Done` (Flexible) đều có `CompletedAt`; vẫn chưa có task `Skipped`. 61 task.
- Index (đọc 2026-10-29): (`ProjectId`, `Status`) · `Deadline` WHERE `Status` ∈ {`ToDo`, `Scheduled`} · `MilestoneId`.
- `AiEstimatedMinutes` int trống được, CHECK > 0 ⚠️ (FR-17b): số phút AI đoán. **Không có** cột `EstimateSource`.
  Ước lượng là "của AI" khi `AiEstimatedMinutes IS NOT NULL AND EstimatedMinutes = AiEstimatedMinutes`;
  user sửa số → hai số khác nhau → là "của user". Đọc DB 2026-10-16: 39 task, 0 task có giá trị, cột không có comment
  → chưa ai ghi. Chưa xác nhận với Backend nghĩa của cột.

### `Project`
- `UserId` — dùng để lọc mọi truy vấn theo chủ sở hữu.
- `Name` varchar(150) NOT NULL · `Description` text trống được · `ClientId` trống được ·
  `StartDate`, `Deadline` **date** trống được (`Deadline >= StartDate`) · `IsDefault` bool · `CreatedAt`.
- `Status` ∈ {`Active`, `OnHold`, `Completed`, `Archived`}, mặc định `Active`. FR-08b-1 #28: `Completed` / `Archived` = đã đóng.
  2026-10-16: 20 project, đều `Active`.
- `AllowAiDocumentAccess` bool NOT NULL DEFAULT false — công tắc "Cho AI đọc tài liệu" (FR-08b-2, B1).
  **AI thêm cột 2026-10-28** (Hiếu cho phép, đã báo Backend). AI chỉ đọc; Backend bật / tắt qua `PATCH /projects/{id}` ⚠️.
  Tắt → AI không đọc file trong kho, kể cả file đã đính kèm ở lượt trước. Upload thẳng vào chat không cần cờ.

### `ProjectDocument` (FR-08b-2, chỉ đọc)
Đọc DB 2026-10-28: 0 dòng.
- `Id` uuid · `ProjectId` uuid NOT NULL FK → `Project` `ON DELETE CASCADE` · `Name` varchar(255) NOT NULL (tên hiển thị, có thể mất đuôi) ·
  `SourceType` varchar(10) ∈ {`File`, `Link`} · `StoragePath` varchar(500) · `LinkUrl` varchar(2000) ·
  `SizeBytes` bigint trống được, CHECK 0–20.971.520 (20 MB) · `Tag` ∈ {`Brief`, `Contract`, `Design`, `Other`} · `UploadedAt`.
- CHECK `CK_ProjectDocument_Source`: `File` ⇒ có `StoragePath`, không `LinkUrl`; `Link` ⇒ ngược lại.
- AI chỉ đọc file loại `File` (đuôi lấy từ `StoragePath`); nội dung tải qua `GET /api/documents/{id}/download` của Backend
  với token của user. Không có `UserId` → lọc chủ qua `Project.UserId`.


### `FocusSession`
- `TaskId`, `SubTaskId?`, `StartedAt`, `EndedAt?` (trống = đang chạy; có thì > `StartedAt`).
- `CaptureMethod` ∈ {`Timer`, `SelfReported`, `AutoTimeout`}, mặc định `Timer` · `IsConfirmed` mặc định true.
- Có `EndedAt` thì bắt buộc `StopReason` ∈ {`Completed`, `Interrupted`, `Blocked`, `Other`}.
- `RemainingMinutes` trống hoặc > 0 (Hiếu thêm 2026-10-07; Backend ghi ở FR-16).
- `Note` text trống được (ghi chú của phiên; FR-19 đưa vào text memory sau khi che thông tin).
- Cột khác: `ScheduleSlotId?`, `FocusTrackId?` (AI không dùng). Index: `TaskId`, `StartedAt`, `ScheduleSlotId`.
- 2026-10-16: 7 dòng, **đều của user test** (`@example.invalid`, sót từ các lần chạy thử) → chưa có phiên thật.

### `Domain`
- `Name` varchar(50) NOT NULL · `IsActive` bool NOT NULL, mặc định true. Dùng chung, **không có** `UserId`.
  2026-10-16: 4 domain, cả 4 `IsActive`.

### `Client` (chỉ đọc `Name`, để che tên trong text FR-19)
- `UserId` NOT NULL · `Name` NOT NULL · `ContactEmail`, `Phone`, `Notes`: **không đọc**.
- `Project.ClientId` trống được (project không có khách).

### `UserCircadianProfile` (FR-10, FR-18) — AI ghi (FR-18) ⚠️
Đọc DB 2026-10-16 (chỉ đọc, FR-18 bước 0).
- **`PriorScore` numeric(4,3) NOT NULL DEFAULT 0.500**, CHECK `UserCircadianProfile_PriorScore_check` [0, 1]: điểm onboarding của giờ đó.
  AI thêm bằng `ALTER` 2026-10-16 (Hiếu cho phép, Backend đồng ý). Dòng cũ đều `Onboarding` → đã chép `PriorScore = Score` (132 dòng).
  Script/entity của Backend phải giữ cột này ⚠️.
- `Id` uuid · `UserId` uuid NOT NULL (FK → `User`, `ON DELETE RESTRICT`) · `HourOfDay` **smallint** 0–23 ·
  `Score` **numeric(4,3)** NOT NULL trong [0, 1], mặc định 0.500 · `Source` varchar NOT NULL ∈ {`Onboarding`, `Learned`},
  mặc định `Onboarding` · `UpdatedAt` timestamptz NOT NULL, mặc định now() (không có trigger → ai ghi phải tự đặt).
- Unique `UQ_UserCircadianProfile_User_Hour` (`UserId`, `HourOfDay`) → mỗi giờ 1 dòng.
- FR-10 đọc mọi dòng, không lọc theo `Source`.
- Dữ liệu 2026-10-16: 132 dòng, 9 user, tất cả `Source = Onboarding`, chưa dòng nào `Learned`.
  - 5 user thật có đủ 24 dòng, `UpdatedAt` ≈ `User.CreatedAt` → **Backend đã tự ghi lúc tạo user** ⚠️.
    4 user toàn 0.500; 1 user có mẫu 0.2 (0–7h, 18–23h) / 0.8 (9–10h, 14–15h) / 0.5 (còn lại) → có vẻ Backend đã ghi điểm onboarding.
  - 4 user test (`@example.invalid`) có 3 dòng (14h 0.9, 15h 0.8, 16h 0.7): sót lại từ `scripts/try_schedule.py` (FR-10).
  - 8/17 user chưa có dòng nào.

### `GoogleCalendarEvent` (FR-09, FR-10)
- Giờ bận đã đồng bộ từ Google. Bỏ qua dòng có `IsCreatedByMetanoia = true` (đã có trong `ScheduleSlot`).
- **Không có `UserId`** → JOIN `GoogleConnection` (`GoogleConnectionId`) để lọc theo user.
- `StartTime`, `EndTime` timestamptz (`EndTime >= StartTime`) · `IsAllDay` · `IsCancelled` · `Title`.

### `GoogleConnection`
- `UserId` **unique** (mỗi user ≤ 1 kết nối) · `Status` ∈ {`Active`, `Expired`, `Revoked`}.
- Không đọc các cột token.

### `User`
- `TimeZone` mặc định `Asia/Ho_Chi_Minh` · `WorkDayStart`, `WorkDayEnd` kiểu `time` (mặc định 08:00, 18:00; End > Start) ·
  `WorksOnWeekend` mặc định false · `DefaultBufferMinutes` 0–120, mặc định 15.
- Không đọc `Email`, `FullName` để gửi LLM.
- `OnboardingCompleted` bool NOT NULL, mặc định false (cột của Backend; đọc DB 2026-10-16). Chỉ 1 user thật = true,
  đúng user có điểm `UserCircadianProfile` khác 0.5 → Backend bật cờ này sau màn onboarding (gồm cả phần khác ngoài giờ hiệu quả?) ⚠️.
- **`ProductiveBlocks` text[] NULL** — AI ghi (FR-18 #19, chỉ lúc onboarding giờ hiệu quả). `NULL` = chưa trả lời.
  CHECK `CK_User_ProductiveBlocks`: 1–5 phần tử, mỗi phần tử ∈ {`morning`, `midday`, `afternoon`, `evening`, `late_night`}.
  AI thêm bằng `ALTER` 2026-10-16 (Hiếu cho phép, Backend đồng ý). Script/entity của Backend phải giữ cột này ⚠️.
  User đã onboarding theo cách cũ của Backend (1 user) vẫn `NULL` → hiện `onboarded: false` cho tới khi trả lời lại.

### `UserSubscription`
- `Status` ∈ {`Active`, `Scheduled`, `Expired`, `Cancelled`} · `StartAt`, `EndAt?` · `AiCallsUsed` ≥ 0 · `QuotaResetAt`.
- Unique index `UX_UserSubscription_OneActivePerUser` (`UserId` WHERE `Status = 'Active'`) → mỗi user ≤ 1 dòng `Active`.
- Nâng cấp gói = dòng mới → `AiCallsUsed` bắt đầu từ 0.

### `SubscriptionPlan`
- `Price` bigint ≥ 0 (Free = 0) · `AiCallLimit` ≥ 0, trống = không giới hạn · `Name` unique.
- Tên gói **tiếng Việt** (2026-10-16): `Miễn phí` (0) · `test` (2.000) · `Tiêu chuẩn` (79.000) · `Cao cấp` (149.000).
  **Không có** gói tên `Premium` → gói cao nhất là `Cao cấp`.
