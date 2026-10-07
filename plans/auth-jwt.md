# Kiểm tra JWT (plumbing, dùng chung cho mọi FR)

Mọi API của AI service chỉ chạy khi có token hợp lệ do Backend phát. Lấy `user_id` từ claim `sub`. Token sai → 401.

## Thông tin từ Backend (đã đọc code `develop`, 2026-10-07)

- HS256, khóa = `Encoding.UTF8.GetBytes(Jwt:Key)` → Python dùng nguyên chuỗi, **không** giải mã base64.
- `iss = METANOIA`, `aud = METANOIA.Client`, `exp` = 60', không có `nbf`.
- Claims: `sub` (GUID), `email`, `name`, `role`, `jti`. AI chỉ dùng `sub`.
- Backend kiểm tra chữ ký + iss + aud + hạn, ClockSkew mặc định .NET = 5'.

## Quyết định đã chốt

| # | Vấn đề | Quyết định |
|---|---|---|
| 1 | Thư viện | `pyjwt` (verify token) + `pydantic-settings` (đọc `.env`, báo lỗi khi thiếu biến). |
| 2 | Lệch đồng hồ | Cho lệch 300 giây giống Backend, biến `JWT_LEEWAY_SECONDS` (mặc định 300). |
| 3 | Endpoint thử | `GET /health` (không cần token) + `GET /me` (cần token, trả `user_id`). |
| 4 | Git | 2 branch, 2 PR: `feature/kbias-domain` (docs + K_bias), rồi `feature/jwt-auth` tách từ đó. Trả lại `import metanoia_ai` trong smoke test. Hỏi Hiếu trước khi push. |
| 5 | Cảnh báo `httpx` (phát sinh ở bước 4) | Starlette 1.7.0 báo `httpx` với TestClient sắp bị bỏ, khuyên `httpx2`. Giữ `httpx`, để cảnh báo hiện. Kiểm tra lại khi nâng cấp FastAPI/Starlette. |
| 6 | Package chưa được cài (phát sinh ở bước 6) | Thêm `[build-system]` dùng `uv_build` vào `pyproject.toml` → `uv sync` cài `metanoia_ai` (editable), lệnh uvicorn trong CLAUDE.md chạy được. |
| 7 | macOS ẩn file `.pth` (Desktop đồng bộ iCloud) | Chỉ trên máy Hiếu: `UV_PROJECT_ENVIRONMENT=$HOME/.venvs/metanoia-ai` trong `~/.zshrc` → môi trường Python nằm ngoài Desktop. Không ảnh hưởng repo. |

## Cần Hiếu đồng ý thêm (phát sinh khi viết plan)

- **`httpx` (dev)**: `TestClient` của FastAPI cần thư viện này để test endpoint. Chỉ dùng cho test.
- **`asyncpg`**: đang nằm chung trong `pyproject.toml` / `uv.lock` chưa commit. Vì `uv add` sửa cùng 2 file đó, tách riêng rất rối → tạm gỡ (`uv remove asyncpg`), khi làm FR-10 thêm lại bằng `uv add asyncpg`.
- **`.env.example`**: dòng `DATABASE_URL=` (chưa commit) commit chung với các biến JWT — chỉ có tên, không có giá trị.

## Quy tắc an toàn

- Chỉ chấp nhận `HS256` (chặn kiểu tấn công đổi thuật toán, `alg=none`).
- Bắt buộc có `exp`, `iss`, `aud`, `sub`.
- Không log token, không đưa `email` / `name` đi đâu.
- Test dùng **chìa giả**, không đọc file `.env` thật.

## Các bước

- [x] **0. Dọn branch** (`feature/kbias-domain`, chưa push): trả lại import smoke test; gỡ `asyncpg`; commit `docs: add CLAUDE.md, FR spec and FR-10 plan`; commit `feat: add K_bias domain rules`; tạo `feature/jwt-auth`.
- [x] **1. Thư viện**: `uv add pyjwt pydantic-settings`, `uv add --dev httpx`; thêm `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE`, `JWT_LEEWAY_SECONDS` vào `.env.example`.
- [x] **2. `config.py`**: class `Settings` + `get_settings()` (đọc 1 lần, dùng lại).
  Test: đọc đúng giá trị; leeway mặc định 300; thiếu `JWT_SECRET` → lỗi.
- [x] **3. `api/auth.py` — `decode_user_id(token, settings) -> UUID`**: hàm thuần, không dính FastAPI.
  Test: hợp lệ → UUID; hết hạn → lỗi; hết hạn < 5' → vẫn nhận; sai chìa; sai iss; sai aud; thiếu `sub`; `sub` không phải GUID; thuật toán khác HS256; chuỗi rác.
- [x] **4. `api/auth.py` — `get_current_user_id`** (dependency FastAPI): đọc header `Authorization: Bearer ...`, gọi bước 3, lỗi → 401 kèm header `WWW-Authenticate: Bearer`.
  Test: không có header → 401; sai kiểu (`Basic ...`) → 401; token sai → 401; token đúng → user_id.
- [x] **5. `api/main.py`**: tạo app, `GET /health`, `GET /me`.
  Test: `/health` → 200 không cần token; `/me` không token → 401; `/me` token đúng → `{"user_id": ...}`.
- [x] **6. Thử tay với token thật** (Hiếu làm, mình hướng dẫn): lấy token từ Backend, chạy `uv run uvicorn metanoia_ai.api.main:app --reload`, gọi `/me` ở `/docs` → thấy đúng UserId.
- [x] **7. Wrap-up**: tick các bước, gợi ý commit, **hỏi Hiếu** rồi push 2 branch và mở 2 PR (ghi lý do thêm thư viện trong PR).

## File sẽ tạo / sửa

- `src/metanoia_ai/config.py`, `src/metanoia_ai/api/auth.py`, `src/metanoia_ai/api/main.py`
- `tests/unit/test_config.py`, `tests/unit/api/test_auth.py`, `tests/unit/api/test_main.py`
- `pyproject.toml`, `uv.lock`, `.env.example`
