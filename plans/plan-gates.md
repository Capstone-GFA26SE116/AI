# Chặn tính năng AI theo gói (Model Business)

Trạng thái: Xong · Branch: `feature/plan-gates` · Cập nhật: 2026-11-14

<!-- Hiếu chốt 2026-11-14 (câu 6 "Xác định các gói chính → sửa code AI giới hạn theo từng gói"):
số liệu gói theo tab Model Business của Metanoia; gói Free không chat AI; trợ lý 3D chỉ gói cao nhất;
giữ tên gói tiếng Anh trong DB (Free / Pro / Premium). Claude Code soạn plan rồi làm liền. -->

## 0. Mục tiêu
- **Làm gì:** AI service chặn tính năng theo gói, đúng tab Model Business.
- **Tiêu chí xong:**
  - [x] AC1 — Gói Free (giá 0) gửi tin chat task (FR-08a) hoặc chat dự án (FR-08b) → 403
        `{"code": "plan_feature_locked", "feature": "chat"}`, không gọi LLM, không trừ lượt.
  - [x] AC2 — Trợ lý 3D (FR-22): chỉ gói có tên trong `ASSISTANT_PLANS` (mặc định `Premium`).
        Gói khác → 403 `feature: "assistant"`, kể cả câu chọn tại chỗ (FR-22b). Không có gói → 409 như cũ.
  - [x] AC3 — `PRODUCTIVE_LEARNING_PLANS` mặc định `Premium` (trước là `Cao cấp`, không khớp tên trong DB
        nên chưa user nào được học giờ hiệu quả).
  - [x] AC4 — Toàn bộ test qua.
- **Ngoài phạm vi:** số liệu từng gói (giá, lượt AI, số dự án, dung lượng) nằm trong bảng `SubscriptionPlan`
  do Backend/Admin sửa. Xếp lịch (FR-10) và tóm tắt Hôm nay vẫn mở cho mọi gói.

## 1. Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Nhận ra gói Free thế nào | `Price = 0` (như luật K cho gói Free) | Không phụ thuộc tên; Admin đổi tên không làm hỏng. |
| 2 | Nhận ra gói có trợ lý | Theo tên trong config `ASSISTANT_PLANS` | Giống `PRODUCTIVE_LEARNING_PLANS`; gói Pro và Premium đều trả phí nên không dùng giá được. |
| 3 | Mã lỗi | 403 + `plan_feature_locked` | Khác 429 (hết lượt) để app hiện "Nâng cấp gói" thay vì "Chờ kỳ sau". ⚠️ báo FE/Mobile |
| 4 | Câu chọn tại chỗ của trợ lý (không tốn lượt) có bị chặn không | Có | Model Business: trợ lý là tính năng của gói cao nhất, không phải chỉ phần LLM. |

## 2. Code nằm ở đâu
- application: `ports/quota_repository.Subscription.plan_name`; `llm_chat.ChatQuota.check_paid`;
  `use_cases/propose_schedule.PlanFeatureError`; `assistant_turn._plan`
- infrastructure: `db/quota_repository.GET_SUBSCRIPTION` đọc thêm `sp."Name"`
- api: `routes/chat.py`, `project_chat.py`, `assistant.py` trả 403; `deps.py` truyền `ASSISTANT_PLANS`
- config: `assistant_plans`, `productive_learning_plans`
