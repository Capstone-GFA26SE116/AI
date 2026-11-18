# LLM-fallback — Nhà cung cấp LLM dự phòng (Groq) khi Gemini lỗi

Trạng thái: Xong · chờ Hiếu thêm 3 biến lên Azure · Branch: `feature/llm-fallback` · Cập nhật: 2026-11-18

<!-- Hiếu chốt 2026-11-11: Groq; Hiếu tự tạo key ở console.groq.com và điền vào .env / Azure.
Claude Code soạn plan rồi làm liền. -->

## 0. Mục tiêu
- **Làm gì:** Mọi lời gọi LLM (chat task FR-08a, chat dự án FR-08b, lý do lịch FR-10, trợ lý FR-22)
  thử Gemini trước; Gemini lỗi (hết quota 429, 503, quá giờ) → gọi nhà cung cấp thứ hai (API kiểu OpenAI, mặc định Groq).
- **Tiêu chí xong:**
  - [x] AC1 — Chưa điền `FALLBACK_LLM_MODEL` + `FALLBACK_LLM_API_KEY` → hành vi như cũ (chỉ Gemini).
  - [x] AC2 — Gemini lỗi → câu trả lời của Groq; `AIUsageLog` ghi tên model thật đã trả lời.
  - [x] AC3 — Cả hai lỗi → một `LLMError` như cũ (người gọi vẫn retry / báo lỗi như trước).
  - [x] AC4 — Không thêm thư viện (dùng `httpx` có sẵn); key không xuất hiện trong log / lỗi.
  - [x] AC5 — Chạy thật với key Groq (2026-11-18, xem mục 5).
- **Ngoài phạm vi:** chọn model theo gói (Free / trả phí), embedding dự phòng (FR-19 vẫn chỉ Gemini).

## 1. Bên ngoài
- `.env` / Azure: `FALLBACK_LLM_BASE_URL` (mặc định `https://api.groq.com/openai/v1`), `FALLBACK_LLM_MODEL`
  (vd `llama-3.3-70b-versatile`), `FALLBACK_LLM_API_KEY`.
- API của service không đổi.

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | JSON Schema | Groq không nhận schema như Gemini → `response_format = json_object` + schema trong system message; người gọi vẫn kiểm bằng Pydantic | Đã có bước kiểm + retry |
| 2 | Chi phí | Ghi theo giá `LLM_*_PRICE_PER_1M` (giá Gemini); Groq free nên số này hơi cao | Đơn giản; đổi sau nếu dùng gói trả phí |
| 3 | Quota | Không đổi: 1 lượt cho 1 tin nhắn, dù trả lời bởi Gemini hay Groq | Người dùng không biết / không cần biết |
| 4 | Thời gian chờ | Groq dùng cùng thời gian chờ với từng tính năng | Tổng thời gian tối đa gấp đôi khi Gemini quá giờ; lỗi 429 trả về ngay nên thường nhanh |

## 2. Bên trong
- infrastructure `llm/openai_compatible_client.py` (`OpenAICompatibleClient`), `llm/fallback_client.py` (`FallbackLLMClient`).
- `config.py`: 3 biến + `fallback_llm()`; `api/deps.py`: `with_fallback()` cho 4 client LLM.

## 3. Test
| ID | Tầng | Tình huống | Mong đợi | AC |
|---|---|---|---|---|
| T1 | config | thiếu model / key / key rỗng | tắt | AC1 |
| T2 | infra | request đúng URL, header, `json_object`, schema; đọc text + token | đúng | AC2 |
| T3 | infra | 429 / 500 / không phải JSON / thiếu `choices` / lỗi mạng | `LLMError`, không lộ key | AC3, AC4 |
| T4 | infra | Gemini được → không gọi Groq; Gemini lỗi → Groq; cả hai lỗi → 1 lỗi | đúng | AC2, AC3 |

## 4. Các bước code
- [x] **1.** Client + wrapper + config + deps — T1–T4.
- [x] **2.** Hiếu tạo key Groq, điền `.env` + Azure App Settings, thử 1 tin nhắn chat khi tắt Gemini key. (`.env` máy dev xong, chạy thật đạt; Azure: Hiếu thêm tay vì máy dev không có Azure CLI.)

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do |
|---|---|---|
| 2026-11-12 | Tạo plan + code; `pytest` 1913 | Mục 4 trong danh sách của Hiếu |
| 2026-11-18 | Hiếu điền key Groq ở `.env`. `llama-3.3-70b-versatile` (gợi ý cũ) Groq **không còn phục vụ** → 404. Model chat còn lại: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`, `allam-2-7b` (ngữ cảnh 4k, bỏ). | Chạy thật bước 2 |
| 2026-11-18 | Đo 56 câu mẫu (router 20, lý do lịch 10, chia task 12, chat dự án 14), 1 lần gọi / câu, không thử lại, gặp 429 thì chờ 30 s rồi gọi lại. **Gemini** (`gemini-3.5-flash-lite`): đúng 41/56, 14 lỗi 503/504 (Gemini quá tải lúc đo), 1 sai luật; trung vị ~7,5 s. **gpt-oss-120b**: đúng 48/56 (router 20/20, lý do 7/10, chia task 11/12, dự án 10/14 — 3 lần không ra JSON); ~1,4–5 s. **qwen3.8-27b**: đúng 53/56 (router 20/20, lý do 10/10, chia task 12/12, dự án 11/14 — 2 lần vẫn 429 sau 3 lần chờ, 1 domain lạ); ~1–3 s. → Chọn **`qwen/qwen3.8-27b`**. Gói free Groq giới hạn token/phút chặt: 22 lần phải chờ 429 khi gọi liên tục; prompt dài (chat dự án, tài liệu đến ~20.000 token) dễ bị 429 khi nhiều người dùng cùng lúc ⚠️ | Hiếu hỏi "AI dự phòng có đủ mạnh không" |
| 2026-11-18 | AC5: Gemini key sai → `FallbackLLMClient` → `qwen/qwen3.8-27b` trả lời tiếng Việt trong 1,3 s. `.env` máy dev đổi `FALLBACK_LLM_MODEL=qwen/qwen3.8-27b`. | Chạy thật bước 2 |
