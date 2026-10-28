# FIX — Đồng bộ quota và K với tài liệu

Trạng thái: Đã duyệt · Branch: `fix/quota-sync-docs` · Cập nhật: 2026-10-28

<!-- Thiết kế trên Claude web (2026-10-28). Claude Code chỉ được tick checkbox ở mục 0 và 4.
⚠️ = cần báo Backend/team; danh sách đầy đủ ở docs/open-questions.md.
Hiếu chốt: chỉ giữ hạn mức theo gói, theo kỳ (BR-86); bỏ giới hạn 50 lượt/ngày. -->

## 0. Mục tiêu

- **Làm gì:** Sửa 3 chỗ nhỏ để code khớp tài liệu (SRS v5.2, BR-86):
  1. Bỏ giới hạn **50 lượt AI / ngày**. Chỉ còn hạn mức của gói trong kỳ (`AiCallLimit` / `AiCallsUsed`).
  2. Gói đã qua `EndAt` thì **không còn tính là gói đang dùng**, kể cả khi Backend chưa kịp đổi `Status` sang `Expired`.
  3. Sửa comment cũ về K ban đầu: K = 1.3 cố định, không lấy từ onboarding.
- **Ai gọi, khi nào:** Không thêm endpoint. Ảnh hưởng 3 chỗ gọi LLM có kiểm quota:
  FR-08a (chat task), FR-08b-1 (chat dự án), FR-10 (đề xuất lịch).
- **Tiêu chí xong:**
  - [ ] AC1 — Không còn chỗ nào trong `src/`, `tests/`, `.env.example`, `CLAUDE.md`, `docs/` nhắc tới giới hạn theo ngày
    (`quota_daily_max`, `QUOTA_DAILY_MAX`, `daily_max`, `count_charged_calls`, `"daily limit reached"`, "50 lượt / ngày").
  - [ ] AC2 — User còn lượt trong gói → gọi AI được, **dù hôm nay đã dùng hơn 50 lượt**.
  - [ ] AC3 — User hết lượt trong gói → vẫn bị chặn như cũ (lỗi `QuotaExceededError`, API trả 429, không gọi LLM).
  - [ ] AC4 — Gói có `EndAt` ≤ bây giờ → coi như không có gói đang dùng (`NoSubscriptionError` → 409: chat trả `no_active_plan`, schedule trả "No active subscription").
    Gói có `EndAt` trống (Free) hoặc `EndAt` > bây giờ → dùng bình thường.
  - [ ] AC5 — Toàn bộ test cũ còn lại vẫn xanh; `ruff check` và `ruff format --check` sạch.
- **Ngoài phạm vi:**
  - Đổi cách khóa khi hết lượt (FR-10 vẫn chặn hẳn, đúng FR-02).
  - Luật "auto-timeout xác nhận mà không sửa giờ thì không học K" — code hiện đúng NFR-17, chưa đổi.
  - Đếm quota riêng từng loại (chat / xếp lịch / tài liệu) — tài liệu cũ có nhắc nhưng BR-86 chỉ còn 1 hạn mức chung.
  - Sửa mặc định DB `UserDomainProfile.KBias` (1.250) — là việc của Backend ⚠️.

## 1. Bên ngoài — nói chuyện với ai

### API
Không đổi endpoint, không đổi request / response. Chỉ bớt 1 trường hợp ra lỗi 429 (hết 50 lượt/ngày).

| Method | Endpoint | Thay đổi |
|---|---|---|
| POST | `/chat/tasks/{id}/messages` | Không còn 429 vì giới hạn ngày |
| POST | `/chat/conversations/{id}/messages` | Không còn 429 vì giới hạn ngày |
| POST | `/schedule/propose` | Không còn 429 vì giới hạn ngày |

### Database (chi tiết bảng: `docs/database.md`)
- Đọc: `UserSubscription` (thêm điều kiện `EndAt`), `SubscriptionPlan` — như cũ.
- Ghi: không đổi (`AiCallsUsed`, `AIUsageLog` vẫn ghi như cũ).
- Không còn đọc `AIUsageLog` để đếm lượt theo ngày. `AIUsageLog` vẫn ghi đủ để theo dõi chi phí (NFR-20).

### Dịch vụ khác
- Azure App Service: biến môi trường `QUOTA_DAILY_MAX` (nếu có) xóa đi cho gọn.
  Không xóa cũng không lỗi vì settings đang `extra="ignore"`.

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Giới hạn 50 lượt/ngày lệch BR-86 | Xóa hẳn | Hiếu chốt 27/10: chỉ giữ hạn mức theo gói, theo kỳ |
| 2 | `count_charged_calls` (port + SQL + fake) chỉ phục vụ giới hạn ngày | Xóa luôn | Không còn ai dùng; giữ lại là code chết |
| 3 | `GET_SUBSCRIPTION` chỉ kiểm `Status = 'Active'` | Thêm `AND (us."EndAt" IS NULL OR us."EndAt" > :now)` | Không phụ thuộc Backend đổi `Status` đúng giờ |
| 4 | Gói hết hạn mà Backend chưa tạo gói Free mới | Trả `NoSubscriptionError` như hiện có (409) | Đúng luật cũ; việc tạo Free mới là của Backend ⚠️ |

## 2. Bên trong — logic

### Code nằm ở đâu
- domain: `domain/kbias.py` (chỉ sửa comment).
- application:
  - `application/llm_chat.py` — `ChatQuota` (bỏ tham số `daily_max`, bỏ import `DAY`).
  - `application/use_cases/propose_schedule.py` — `_check_quota`, hằng `DAY`, docstring đầu file (dòng 3), docstring `QuotaExceededError`.
  - `application/use_cases/send_task_chat_message.py`, `send_project_chat_message.py` — bỏ tham số `quota_daily_max`.
  - `application/ports/quota_repository.py` — xóa `count_charged_calls`.
- infrastructure: `infrastructure/db/quota_repository.py` — xóa `COUNT_CHARGED_CALLS` + hàm; sửa `GET_SUBSCRIPTION`.
- api: `api/deps.py` — bỏ `quota_daily_max=...` (3 chỗ: khoảng dòng 145, 323, 414).
- config: `config.py` — xóa `quota_daily_max`; `.env.example` — xóa `QUOTA_DAILY_MAX=50`.

### Luồng kiểm quota sau khi sửa
1. Lấy gói đang dùng: `Status = 'Active'`, `StartAt ≤ now`, `EndAt` trống hoặc `> now`, mới nhất.
2. Không có → `NoSubscriptionError`.
3. `has_calls_left` = false → `QuotaExceededError("plan limit reached")`.
4. Gọi LLM → thành công thì `try_charge` (+1 có điều kiện) → ghi `AIUsageLog` như cũ.

### Luật & trường hợp đặc biệt
- `AiCallLimit` trống = không giới hạn — giữ nguyên.
- `try_charge` vẫn là 1 câu UPDATE có điều kiện → 2 request cùng lúc không vượt hạn mức. Không đụng tới.
- Embedding (FR-19), FR-09, FR-12 vẫn `quota_charged = false`, không kiểm quota — không đụng tới.

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | config | Settings không còn `quota_daily_max`; có `QUOTA_DAILY_MAX` trong env | App vẫn chạy (bị bỏ qua) | AC1 |
| T2 | application (`ChatQuota`) | Còn lượt gói, fake log có 60 lượt tính phí trong 24h | `check` trả gói, không lỗi | AC2 |
| T3 | application (`ChatQuota`) | `AiCallsUsed = AiCallLimit` | `QuotaExceededError("plan limit reached")` | AC3 |
| T4 | application (FR-10) | Như T2 cho `ProposeSchedule` | Đề xuất lịch chạy bình thường | AC2 |
| T5 | application (FR-10) | Như T3 cho `ProposeSchedule` | `QuotaExceededError`, không gọi LLM | AC3 |
| T6 | api | Chat / schedule khi hết lượt gói | 429 như cũ | AC3 |
| T7 | infrastructure (chạy thật, EXPLAIN / SELECT) | `GET_SUBSCRIPTION` với `EndAt` trong quá khứ, trống, tương lai | Quá khứ → không có dòng; trống / tương lai → có | AC4 |
| T8 | toàn bộ | `uv run pytest`, `ruff check`, `ruff format --check` | Xanh, sạch | AC5 |

Test cũ về "daily limit" (khoảng 14 chỗ trong 9 file, xem bước 0) → **xóa**, hoặc đổi thành T2 / T4 nếu test đó còn kiểm phần hạn mức gói.

## 4. Các bước code
*(Mỗi bước = 1 việc nhỏ + test của nó. Claude Code DỪNG sau mỗi bước.)*
- [x] **0.** Liệt kê mọi chỗ còn nhắc giới hạn ngày: `grep -rn -i "quota_daily_max\|QUOTA_DAILY\|daily_max\|count_charged_calls\|daily limit\|50 per day\|50/ngày\|50 lượt" src tests docs plans CLAUDE.md .env.example`.
  Báo Hiếu danh sách file + test nào sẽ xóa, test nào sẽ sửa. Không code.
- [x] **1.** Bỏ giới hạn ngày trong `ChatQuota` + 2 use case chat + `deps.py` (phần chat) — file: `llm_chat.py`, `send_task_chat_message.py`, `send_project_chat_message.py`, `api/deps.py` — test: T2, T3, sửa `test_llm_chat.py`, `test_send_task_chat_message.py`, `test_project_chat.py`, `test_chat.py`.
- [ ] **2.** Bỏ giới hạn ngày trong FR-10 — file: `propose_schedule.py` (`_check_quota`, `DAY`, docstring), `api/deps.py` (phần schedule) — test: T4, T5, T6, sửa `test_propose_schedule.py`, `test_fake_schedule.py`, `test_schedule.py`.
- [ ] **3.** Xóa `count_charged_calls` khỏi port, SQL repo, fake — file: `ports/quota_repository.py`, `infrastructure/db/quota_repository.py`, `tests/unit/application/fakes.py`.
- [ ] **4.** Xóa setting — file: `config.py`, `.env.example` — test: T1, sửa `test_config.py`.
- [ ] **5.** Thêm điều kiện `EndAt` vào `GET_SUBSCRIPTION` — file: `infrastructure/db/quota_repository.py` — test: T7 (chạy câu SELECT trên Supabase với 1 user test, ghi kết quả vào mục 5).
- [ ] **6.** Sửa comment K — file: `domain/kbias.py` dòng 11 → `INITIAL_K_BIAS = 1.3  # Fixed for every new user (Moløkken & Jørgensen, 2003); learned per user after that.`
- [ ] **7.** Chạy T8 (pytest + ruff) và `grep` của bước 0 lần nữa → phải rỗng (trừ dòng trong plan này).
- [ ] **8.** Docs:
  - `CLAUDE.md` §7 "Quota": thay 2 dòng đầu bằng
    `- One limit per plan per billing period (AiCallLimit / AiCallsUsed, BR-86). No daily limit.`
    `- A subscription past its EndAt is not active, even if Status is still Active.`
  - `docs/functional-requirements.md` FR-02: thay câu "Quota AI đếm riêng từng loại … 50 lượt AI mỗi ngày để chống spam." bằng
    "Mỗi gói có một hạn mức lượt AI trong một kỳ; hết kỳ thì đặt lại." và đổi "PayOS / VNPay" → "PayOS".
  - `plans/FR-10.md`: dòng #5 và luồng bước 1 bỏ phần "50/ngày"; bước 1 của mục 4 bỏ `QUOTA_DAILY_MAX`; thêm 1 dòng nhật ký trỏ về plan này.
  - `docs/open-questions.md` mục "Chờ Backend" thêm 2 dòng ⚠️:
    - Đổi mặc định DB `UserDomainProfile.KBias` từ 1.250 → 1.300 cho khớp tài liệu.
    - Khi gói trả phí hết `EndAt` mà không gia hạn: đổi `Status` → `Expired` và tạo gói Free mới (FR-02). AI đã tự bỏ qua gói quá `EndAt`.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-10-28 | Tạo plan | Đồng bộ code với BR-86 và SRS v5.2 sau khi merge FR-12. |
