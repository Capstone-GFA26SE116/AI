# FR-17b — Không học K_bias từ ước lượng do AI đặt

Trạng thái: Đã duyệt · Branch: `feature/fr17b-estimate-source` · Cập nhật: 2026-10-16

<!-- Thiết kế trên Claude web (2026-10-16), tách ra từ FR-08b-1 #11. Claude Code chỉ được tick checkbox ở mục 0 và 4.
⚠️ = cần báo Backend/team; danh sách đầy đủ ở docs/open-questions.md. -->

## 0. Mục tiêu

- **Làm gì:** Từ FR-08b-1, AI tạo task kèm số phút **AI đoán**. K_bias đo độ lệch của **chính user**, nên task có
  ước lượng do AI đặt (`EstimatedMinutes = AiEstimatedMinutes`, xem #5) **không được dùng để học K**. Kết quả tổng hợp FR-09
  ("trung bình mất gấp X lần ước lượng") cũng bỏ các task này.
- **Phải làm trước FR-08b-1** (FR-08b-1 ghi `AiEstimatedMinutes`).
- **Ai gọi, khi nào:** không có endpoint mới. Đổi hành vi `POST /learning/task-completed` (FR-17) và `FindSimilarTasks` (FR-09).
- **Tiêu chí xong:**
  - [ ] AC1 — Task `Done` có ước lượng `AI` → không đổi K, không ghi `KBiasUpdateLog`, trả `skipped` với `reason = "ai_estimate"`.
  - [ ] AC2 — Task có ước lượng `User` → học như cũ (mọi test FR-17 cũ pass nguyên vẹn).
  - [ ] AC3 — Memory FR-19 vẫn được lưu cho task `AI` (vẫn có ích cho tìm task tương tự).
  - [ ] AC4 — FR-09: task `AI` vẫn xuất hiện trong kết quả, nhưng `ratio = null` và không tính vào `average_ratio`.
- **Ngoài phạm vi:** đo độ chính xác ước lượng của AI so với T_act (làm sau, cho báo cáo) · FR-10 (vẫn dùng T_est × K như mọi task).

## 1. Bên ngoài — nói chuyện với ai

### API
| Method | Endpoint | Thay đổi |
|---|---|---|
| POST | `/learning/task-completed` | Thêm giá trị `reason = "ai_estimate"` khi `skipped` |
| GET | `/memory/similar` | Mỗi item thêm `estimate_source` (`user` / `ai`); `ratio` trống khi `ai` |

### Database (chi tiết: `docs/database.md`)
- **Không đổi schema.** Dùng cột có sẵn `Task.AiEstimatedMinutes` int NULL, CHECK > 0 ⚠️ (đọc DB 2026-10-16: chưa task nào có giá trị).
  - Task cũ và task Backend tạo tay có `AiEstimatedMinutes` trống → `User` sẵn, Backend không cần sửa code.
- **Đọc:** `Task.EstimatedMinutes`, `Task.AiEstimatedMinutes` (FR-17 và FR-09). **Ghi:** không (FR-08b-1 mới ghi).

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Học từ số của ai | Chỉ học khi ước lượng là `User` | K phải đo độ lệch của user, không phải của Gemini. |
| 2 | Lưu nguồn ước lượng ở đâu | Dùng cột có sẵn `AiEstimatedMinutes`, không thêm cột `EstimateSource` ⚠️ (báo Backend: AI service ghi cột này, đừng dùng vào việc khác) | Không đổi schema (CLAUDE.md §8); giữ được số AI đoán kể cả khi user sửa. |
| 3 | User sửa ước lượng sau khi Apply | Backend **không cần làm gì**: số khác `AiEstimatedMinutes` → tự thành `User` | Không phụ thuộc Backend nhớ cập nhật. |
| 4 | Memory FR-19 | Vẫn lưu | Tìm task tương tự vẫn cần việc đã làm, kể cả khi số ước lượng do AI đặt. |
| 5 | Luật nguồn ước lượng | `AI` khi `AiEstimatedMinutes` có giá trị **và** = `EstimatedMinutes`; còn lại `User`. Hàm thuần ở domain `estimate_source(estimated, ai_estimated)` | User sửa về đúng số AI → coi là `AI`, chỉ mất 1 mẫu học (lỗi an toàn). |

## 2. Bên trong — logic

### Code nằm ở đâu
- **domain:** `models.py` — thêm `EstimateSource` (enum `USER`, `AI`), `estimate_source` (#5), `SkipReason.AI_ESTIMATE`; `similar_tasks.py` — `PastTask.estimate_source`, `ratio` / `summarize_similar` bỏ task `AI`.
- **application:** `record_task_completion.py` — kiểm nguồn ước lượng.
- **infrastructure:** `learning_repository.py`, `similar_task_repository.py` — đọc thêm `t."AiEstimatedMinutes"` (FR-09: kèm `t."EstimatedMinutes"`), gọi `estimate_source`.
- **api:** `routes/memory.py` — thêm `estimate_source`.

### Luật
- Thứ tự kiểm trong FR-17: `Fixed` → chưa `Done` → **`ai_estimate`** → thời gian thực (giữ nguyên các luật sau).
  Đặt trước phần đọc `FocusSession` để không tốn truy vấn.
- Chưa học task nào thì không ghi gì (như các lý do bỏ qua khác).

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | application | Task `Done`, `Flexible`, `AI`, có T_act hợp lệ | `skipped`, `ai_estimate`, K không đổi, không có `KBiasUpdateLog` | AC1 |
| T2 | application | Task `Fixed` và `AI` | `fixed_task` (thứ tự kiểm đúng) | AC1 |
| T3 | application | Task `User` | Học như cũ | AC2 |
| T4 | domain | `ratio` / `summarize_similar` có 1 task `AI` done + 2 task `User` done | `ratio` của task `AI` = null; trung bình chỉ từ 2 task `User` | AC4 |
| T5 | api | `/learning/task-completed` trả `ai_estimate`; `/memory/similar` có `estimate_source` | Đúng mẫu | AC1, AC4 |
| T6 | domain | `estimate_source`: AI trống · hai số bằng nhau · hai số khác nhau | `User` / `AI` / `User` | AC1, AC2 |

Test cũ (FR-08a, FR-09, FR-10, FR-17, FR-19) phải pass nguyên vẹn. Memory FR-19 (AC3): kiểm luồng lưu memory không đọc nguồn ước lượng.

## 4. Các bước code
*(Mỗi bước = 1 việc nhỏ + test của nó. Claude Code DỪNG sau mỗi bước.)*
- [x] **0.** Branch từ `main` mới nhất. **Chỉ đọc** DB: cột `Task.AiEstimatedMinutes` đúng kiểu / CHECK, đang dùng thế nào. Cập nhật `docs/database.md`.
- [ ] **1.** Domain: `EstimateSource`, `estimate_source`, `SkipReason.AI_ESTIMATE`, FR-09 `ratio` / `summarize_similar` — test T4, T6.
- [ ] **2.** FR-17: use case + repository đọc cột — test T1–T3.
- [ ] **3.** FR-09: repository + API thêm `estimate_source` — test T5.
- [ ] **4.** Cập nhật `CLAUDE.md` §5 (Learning: thêm "skip khi ước lượng do AI đặt"), `docs/open-questions.md` (⚠️ #2, #3). Wrap-up: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, tick AC, hỏi trước khi push / mở PR.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-10-16 | Tạo plan | Tách từ FR-08b-1 #11: thêm `Task.EstimateSource`, FR-17 bỏ qua ước lượng do AI đặt |
| 2026-10-16 | Dùng `AiEstimatedMinutes` thay cột mới `EstimateSource` (#2, #3, #5, T6, bước 0–2) | Bước 0: DB không có `EstimateSource` nhưng có sẵn `AiEstimatedMinutes` (chưa ai ghi). Hiếu chọn cách A: không đổi schema |
