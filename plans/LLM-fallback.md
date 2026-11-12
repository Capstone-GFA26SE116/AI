# LLM-fallback — Nhà cung cấp LLM dự phòng (Groq) khi Gemini lỗi

Trạng thái: Xong (code) · chờ key · Branch: `feature/llm-fallback` · Cập nhật: 2026-11-12

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
  - [ ] AC5 — Chạy thật với key Groq (chờ Hiếu điền key).
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
- [ ] **2.** Hiếu tạo key Groq, điền `.env` + Azure App Settings, thử 1 tin nhắn chat khi tắt Gemini key.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do |
|---|---|---|
| 2026-11-12 | Tạo plan + code; `pytest` 1913 | Mục 4 trong danh sách của Hiếu |
