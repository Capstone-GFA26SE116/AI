# Câu hỏi mở & việc chờ Backend/team

Cập nhật: 2026-10-12. Đây là danh sách DUY NHẤT. Trong plan chỉ đánh dấu ⚠️ cạnh quyết định liên quan.
Chốt xong → ghi kết quả vào `docs/` hoặc plan, rồi chuyển dòng xuống mục "Đã chốt".
Số `#` = số trong tab Problems của Google Doc (nếu có).

## Chờ Backend

| Việc | Liên quan | Ghi chú |
|---|---|---|
| Gọi `POST /learning/task-completed` sau khi lưu task `Done` | FR-17 | Đã thống nhất đường gọi, Backend chưa code |
| Đồng ý AI ghi `UserDomainProfile`, `KBiasUpdateLog` | FR-17, #9 | |
| Đồng ý AI ghi `ScheduleProposal`, `ScheduleSlot` (chỉ `Proposed`), `UserSubscription.AiCallsUsed`, `AIUsageLog` | FR-10, #9 | |
| Gọi `POST /schedule/propose` khi user bấm "Xếp lịch", gửi kèm `language` (vi/en) | FR-10 | DB không có cột ngôn ngữ |
| Ai reset `AiCallsUsed` khi tới `QuotaResetAt`? | Quota | Giả định: job của Backend |
| Thêm `Superseded` vào CHECK `ScheduleProposal.Status`; cập nhật entity cho cột `Warnings` | FR-10 | Đọc DB 2026-10-13: CHECK chỉ có `Pending`, `Approved`, `PartlyApproved`, `Rejected`. Hiếu chọn: Backend thêm |
| Thêm unique (`UserId`) WHERE `Status = 'Active'` cho `UserSubscription` | FR-10, Quota | DB chưa chặn 2 gói `Active`; AI tạm lấy dòng `StartAt` mới nhất |
| Đơn vị tiền của `AIUsageLog.Cost` (USD hay VND)? | FR-10, Quota | AI tính từ config giá token |
| Đổi mặc định `UserDomainProfile.KBias` thành `1.300` | FR-17 | |
| Ý định của cột `ConfidenceTier` (AI đang tính theo nguồn T_act) | FR-17 | Hỏi người thiết kế ERD |
| Cập nhật script/ERD cho 2 cột Hiếu đã thêm: `ScheduleProposal.Warnings`, `FocusSession.RemainingMinutes`; ghi `RemainingMinutes` ở FR-16 | FR-10 | |
| Ai đổi slot `Proposed` → đã duyệt và ghi `ScheduleChangeLog`? | FR-11 | Giả định: Backend |
| Quota: DB chỉ có 1 bộ đếm `AiCallsUsed`, luật cần đếm riêng 3 loại | Quota, #13 | |
| Đồng ý cách hiểu buffer (khoảng nghỉ riêng, không ghi lên lịch) | FR-10 | |
| Role: không chặn Admin theo role; quyền gọi AI dựa vào gói + quota | — | Hiếu đã quyết, chỉ báo |
| Refresh token: token sống 60', Focus mode dài hơn → Stop bị 401 | Backend + Frontend | Đưa vào tab Problems |

## Chờ quyết định (team / giảng viên)

| Câu hỏi | Liên quan | Hiện đang dùng |
|---|---|---|
| Giảng viên duyệt Python + service riêng? | #8 | Python |
| K ban đầu lấy từ onboarding hay cố định 1.3? | #10 | 1.3 |
| Ngưỡng outlier | #12 | 5 (ngoài [1/5, 5] thì bỏ qua) |
| Quota đọc tài liệu theo từng gói | #13 | — |
| Chọn model LLM + model embedding (kích thước vector; ERD đang 1536) | — | Chưa chọn |
| Database doc lệch với schema Supabase | #19 | Tin DB thật |

## Cập nhật Google Doc

- α = 0.2, K ban đầu = 1.3 (nếu Doc còn ghi 0.3 / 1.25).

## Đã chốt

| Ngày | Câu hỏi | Kết quả |
|---|---|---|
| 2026-10-07 | JWT | HS256, user ID trong `sub`, iss `METANOIA`, aud `METANOIA.Client` |
| 2026-10-11 | Đường gọi | Frontend → Backend → AI |
| 2026-10-12 | DB dev riêng? | Không — làm thẳng trên Supabase, xóa dữ liệu test trước khi công bố |
