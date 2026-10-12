# Database — phần AI service đụng tới

Cập nhật: 2026-10-12 · Nguồn: code Backend `develop` + đọc trực tiếp Supabase (chỉ đọc).
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
- `Warnings` jsonb (mảng, cho phép trống — Hiếu thêm 2026-10-07), `IsFeasible`, `MissingMinutes`, `Explanation`.
- ⚠️ Chưa đọc đủ cột / ràng buộc thật.

### `ScheduleSlot` — AI ghi, chỉ slot trạng thái `Proposed` (FR-10) ⚠️
- `Status = 'Proposed'`, `ProposedMinutes` (phần làm việc), `KBiasUsed` (gói Free: trống), `Explanation`.
- Đổi sang đã duyệt: việc của Backend (FR-11).
- ⚠️ Chưa đọc đủ cột / ràng buộc thật.

### `AIUsageLog` + bộ đếm quota — AI ghi ⚠️
- Chưa thiết kế. DB hiện chỉ có 1 bộ đếm `AiCallsUsed` (luật cần đếm riêng 3 loại).

## Bảng chỉ đọc

### `Task`
- **Không có `UserId`** → chủ sở hữu qua `Project.UserId` (luôn JOIN `Project`).
- `TaskType` ∈ {`Flexible`, `Fixed`} · `Status` ∈ {`ToDo`, `Scheduled`, `InProgress`, `Done`, `Skipped`}.
- Task `Flexible` **bắt buộc** có `Deadline` và `EstimatedMinutes` > 0.
- `DomainId`, `CompletedAt`.

### `Project`
- `UserId` — dùng để lọc mọi truy vấn theo chủ sở hữu.

### `SubTask`
- `Status` ∈ {`ToDo`, `Done`, `Skipped`} · `EstimatedMinutes` > 0 · `OrderIndex`.

### `FocusSession`
- `TaskId`, `SubTaskId?`, `StartedAt`, `EndedAt?` (trống = đang chạy; có thì > `StartedAt`).
- `CaptureMethod` ∈ {`Timer`, `SelfReported`, `AutoTimeout`}, mặc định `Timer` · `IsConfirmed` mặc định true.
- Có `EndedAt` thì bắt buộc `StopReason` ∈ {`Completed`, `Interrupted`, `Blocked`, `Other`}.
- `RemainingMinutes` trống hoặc > 0 (Hiếu thêm 2026-10-07; Backend ghi ở FR-16).

### `UserCircadianProfile` (FR-10, FR-18)
- `Score` trong [0, 1].

### `GoogleCalendarEvent` (FR-09, FR-10)
- Giờ bận đã đồng bộ từ Google. Bỏ qua dòng có `IsCreatedByMetanoia = true` (đã có trong `ScheduleSlot`).

### `User`
- `TimeZone` (FR-10). Không đọc `Email`, tên để gửi LLM.
