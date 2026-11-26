# FR-10g — Lý do xếp lịch viết bằng code (không LLM, không tốn lượt)

Trạng thái: Xong · Branch: `feature/fr10g-template-reasons` · Cập nhật: 2026-11-26

<!-- Hiếu đồng ý 2026-11-26 ("Ok") sau khi xem số liệu 30 ngày: Schedule = 201/343 lần gọi LLM
(43%), ~22% lần gọi lỗi vì Gemini gói miễn phí (429 / 503). Claude Code soạn plan rồi làm liền. -->

## 0. Mục tiêu
- **Làm gì:** Lý do của từng slot FR-10 do code viết từ chính các "facts" đang gửi cho LLM (`SlotFacts`),
  theo đúng luật của prompt `schedule_reason_v4`. Bật bằng `SCHEDULE_REASONS=template` (mặc định); `llm` = như cũ.
- **Tiêu chí xong:**
  - [x] AC1 — Mỗi slot có lý do ≤ 2 câu, ≤ 250 ký tự, một dòng, "mình / bạn", không giờ cụ thể, không tên nội bộ
        (cùng danh sách cấm của `reason_writer.py`); vi / en.
  - [x] AC2 — Câu 1 = vì sao giờ này (họp / lịch Google / việc đã duyệt / việc gấp hơn / hết giờ làm / giờ tập trung /
        rải việc / sớm nhất / phần tiếp theo); câu 2 = thời gian dư (chỉ phần 1), hoặc lý do chia phần, hoặc hạn chót.
  - [x] AC3 — Lời văn đổi giữa các slot cùng loại (không mở đầu giống nhau liên tiếp).
  - [x] AC4 — Chế độ `template`: không gọi LLM, **không kiểm / không trừ lượt**, không ghi `AIUsageLog` (CLAUDE.md §7:
        không LLM thì không quota). User hết lượt vẫn xếp lịch được.
  - [x] AC5 — Chế độ `llm` không đổi; `pytest` xanh.
- **Ngoài phạm vi:** đổi planner · đổi API (response giữ nguyên) · bỏ prompt v4.

## 1. Quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Mặc định | `template` | 43% lần gọi LLM, hay lỗi gói miễn phí; facts đã đủ để viết câu |
| 2 | Gói Free / hết lượt | Vẫn xếp lịch có lý do | Xếp lịch là code (CLAUDE.md §5); chỉ LLM mới tốn lượt |
| 3 | "Bạn ước 60 phút" khi số do AI đoán | Câu mới nói "Dự kiến 60 phút" | Lỗi chữ thấy ở lần chạy thật 2026-11-26 |

## 2. Code
- domain: `schedule_reasons.py` (`template_reason`, `template_reasons`).
- application: `reason_writer.py` thêm `SlotReasonWriter` (Protocol, `uses_llm`) + `TemplateReasonWriter`;
  `propose_schedule.py` chỉ kiểm lượt khi `uses_llm`.
- api: `deps.py` `schedule_reason_writer(settings, llm_client)`. config: `SCHEDULE_REASONS`.

## 3. Test
- `tests/unit/domain/test_schedule_reasons.py`: câu mẫu vi / en, phần sau không nhắc thời gian dư, mẹo gói Free,
  hạn chót / chia phần, đổi lời văn, **mọi tổ hợp facts** (2 × 3 × 6 × 2 × 3 × 3 × 2 × 3) đúng AC1;
  use case hết lượt vẫn chạy, không trừ, không log; chọn writer theo config.
- Chạy thật (script tạm, 2026-11-26): user hết lượt (300/300), 3 task → 4 slot, vi + en đều có lý do;
  `AiCallsUsed` giữ 300, `AIUsageLog` 0 dòng, 1–1,6 s; xóa dữ liệu thử còn 0.

## 4. Các bước code
- [x] **1.** domain + test · [x] **2.** writer + use case + deps + config · [x] **3.** chạy thật · [x] **4.** docs

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do |
|---|---|---|
| 2026-11-26 | Tạo + làm xong | Giảm lần gọi LLM, tránh lỗi gói miễn phí |
