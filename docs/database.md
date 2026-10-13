# Database — phần AI service đụng tới

Cập nhật: 2026-10-13 · Nguồn: code Backend `develop` + đọc trực tiếp Supabase (chỉ đọc).
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

### `UserSubscription.AiCallsUsed` — AI ghi (bộ đếm quota)
- Chỉ 1 bộ đếm cho mọi loại ⚠️ (luật cần đếm riêng 3 loại). ≥ 0. Xem bảng `UserSubscription` bên dưới.

## Bảng chỉ đọc

### `Task`
- **Không có `UserId`** → chủ sở hữu qua `Project.UserId` (luôn JOIN `Project`).
- `TaskType` ∈ {`Flexible`, `Fixed`} · `Status` ∈ {`ToDo`, `Scheduled`, `InProgress`, `Done`, `Skipped`}.
- Task `Flexible` **bắt buộc** có `Deadline` và `EstimatedMinutes` > 0.
- Task `Fixed` **bắt buộc** có `FixedStart`, `FixedEnd` (timestamptz, `FixedEnd > FixedStart`).
- `Title`, `DomainId`, `CompletedAt`.

### `Project`
- `UserId` — dùng để lọc mọi truy vấn theo chủ sở hữu.

### `SubTask`
- `Status` ∈ {`ToDo`, `Done`, `Skipped`} · `EstimatedMinutes` > 0 · `OrderIndex` ≥ 1, unique theo task · `Title`.
- Không có deadline riêng (dùng deadline task cha).

### `FocusSession`
- `TaskId`, `SubTaskId?`, `StartedAt`, `EndedAt?` (trống = đang chạy; có thì > `StartedAt`).
- `CaptureMethod` ∈ {`Timer`, `SelfReported`, `AutoTimeout`}, mặc định `Timer` · `IsConfirmed` mặc định true.
- Có `EndedAt` thì bắt buộc `StopReason` ∈ {`Completed`, `Interrupted`, `Blocked`, `Other`}.
- `RemainingMinutes` trống hoặc > 0 (Hiếu thêm 2026-10-07; Backend ghi ở FR-16).

### `UserCircadianProfile` (FR-10, FR-18)
- `HourOfDay` 0–23, unique (`UserId`, `HourOfDay`) → mỗi giờ 1 dòng · `Score` trong [0, 1], mặc định 0.5.
- `Source` ∈ {`Onboarding`, `Learned`}. FR-10 đọc mọi dòng, không lọc theo `Source`.

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

### `UserSubscription`
- `Status` ∈ {`Active`, `Scheduled`, `Expired`, `Cancelled`} · `StartAt`, `EndAt?` · `AiCallsUsed` ≥ 0 · `QuotaResetAt`.
- ⚠️ Không có unique cho dòng `Active` → 1 user có thể có nhiều dòng `Active` (lỗi). AI lấy dòng `StartAt` mới nhất.
- Nâng cấp gói = dòng mới → `AiCallsUsed` bắt đầu từ 0.

### `SubscriptionPlan`
- `Price` bigint ≥ 0 (Free = 0) · `AiCallLimit` ≥ 0, trống = không giới hạn · `Name` unique.
