# Gói Miễn phí cũng dùng K khi xếp lịch

Trạng thái: Xong · Branch: `feature/free-plan-uses-k` · Cập nhật: 2026-11-14

<!-- Hiếu chốt 2026-11-14 ("Sửa code"): SRS tab Model Business ghi gói Miễn phí ✅ "Học tốc độ làm việc
thật để xếp lịch sát hơn", nhưng code chỉ cho K hiện dạng gợi ý. Chọn sửa code cho khớp SRS.
Claude Code soạn plan rồi làm liền. -->

## 0. Mục tiêu
- **Làm gì:** Bỏ luật "Free: K_bias chỉ để gợi ý". Mọi gói đều nhân K vào thời gian (T_est × K).
- **Tiêu chí xong:**
  - [x] AC1 — FR-10 xếp lịch: gói Miễn phí dùng K của user (chưa có K → `KBIAS_INITIAL`), lưu `KBiasUsed`.
  - [x] AC2 — FR-12 Hôm nay: thời gian còn cần nhân K với mọi gói. Briefing không đọc gói nữa.
  - [x] AC3 — FR-08a / FR-08b-1: `adjusted_is_hint` luôn `false` (giữ trường để Web/Mobile không vỡ).
  - [x] AC4 — Test cũ về gói Free sửa theo luật mới; toàn bộ test qua.
- **Ngoài phạm vi:** Học giờ hiệu quả (FR-18) vẫn chỉ gói Cao cấp tự học. Quota không đổi.

## 1. Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Giữ hay bỏ trường `adjusted_is_hint` | Giữ, luôn `false` | Web/Mobile đang đọc trường này; bỏ thì vỡ API. Bản nháp cũ đã lưu `true` vẫn đọc được. |
| 2 | Briefing còn cần biết gói không | Không. Bỏ cột `PlanPrice` khỏi câu SQL | Chỉ dùng để tắt K; giờ không cần. |
| 3 | `KBiasUsed` của slot gói Free | Có giá trị như gói trả phí | Trước đây trống (`docs/database.md`). |

## 2. Code nằm ở đâu
- domain: `task_breakdown.draft_numbers`, `project_breakdown.project_numbers` (bỏ tham số `is_free_plan`)
- application: `propose_schedule._units`, `get_daily_briefing._candidate`, 2 use case chat;
  `ports/briefing_repository.BriefingData` (bỏ `is_free_plan`)
- infrastructure: `db/briefing_repository.GET_USER` (bỏ `PlanPrice`)

## 3. Thay cho
FR-08a #7, FR-08b-1 #8, FR-10 (dòng `k = None if subscription.is_free`), FR-12 #7–#8, FR-20 #12.
