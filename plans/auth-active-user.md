# auth-active-user — Chặn token của user đã xóa tài khoản

Trạng thái: Xong · Branch: `feature/auth-active-user` · Cập nhật: 2026-11-13

## 0. Mục tiêu
- **Làm gì (1–2 câu):** Sau khi JWT hợp lệ, kiểm tra user còn hoạt động (`User.DeletedAt IS NULL`).
  User đã `DELETE /api/me` → 401 ngay, giống `OnTokenValidated` của Backend (`Program.cs`, `IsActiveAsync`).
- **Ai gọi, khi nào:** mọi endpoint có `Depends(get_current_user_id)` (tất cả trừ `/health`).
- **Tiêu chí xong:**
  - [x] AC1: Token hợp lệ + user còn hoạt động → chạy như cũ.
  - [x] AC2: Token hợp lệ + user đã xóa (`DeletedAt` có giá trị) hoặc không có dòng `User` → 401 y hệt token sai (không nói lý do).
  - [x] AC3: Token sai / thiếu → 401 **không** chạm DB.
  - [x] AC4: Kiểm tra không để transaction mở → các repository gọi `session.begin()` sau đó vẫn chạy.
  - [x] AC5: DB lỗi khi kiểm tra → lỗi server (500), **không** đổi thành 401.
- **Ngoài phạm vi:** cache kết quả · chặn theo role · thu hồi token (blacklist).

## 1. Bên ngoài — nói chuyện với ai

### API
Không thêm endpoint. Hành vi 401 của mọi endpoint mở rộng thêm trường hợp user đã xóa.

### Database (chi tiết bảng: `docs/database.md`)
- Đọc: `User` (`Id`, `DeletedAt`) — 1 truy vấn theo khóa chính mỗi request.
- Ghi: không.

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Nghĩa "còn hoạt động" | `DeletedAt IS NULL`, giống hệt Backend | 1 luật cho cả 2 service |
| 2 | DB lỗi lúc kiểm tra | Để lỗi đi lên (500) | 401 làm app đăng xuất user khi chỉ là DB chập chờn |
| 3 | Cache | Không | Backend cũng không cache; truy vấn theo PK rất nhẹ |

## 2. Bên trong — logic

### Code nằm ở đâu
- application: `ports/account_repository.py` — `AccountRepository.is_active(user_id)`
- infrastructure: `db/account_repository.py` — `SqlAccountRepository`
- api: `deps.py` — `get_account_repository`; `auth.py` — `get_current_user_id` thành `async`, gọi thêm `is_active`

### Luồng xử lý
1. Không có header → 401.
2. Giải mã JWT (như cũ). Sai → 401.
3. `is_active(user_id)`. Sai → 401. Đúng → trả `user_id`.

### Luật & trường hợp đặc biệt
- Dùng chung session của request. Nếu kiểm tra tự mở transaction (autobegin) thì `rollback` ngay sau khi đọc,
  để repository sau gọi `session.begin()` không bị lỗi "transaction already begun".

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | api | Token đúng + user còn hoạt động | 200, đúng user_id | AC1 |
| T2 | api | Token đúng + user đã xóa / không tồn tại | 401 cùng body, header như token sai | AC2 |
| T3 | api | Token sai / thiếu | 401, repository không được gọi | AC3 |
| T4 | infrastructure | SQL lọc `"Id"` + `"DeletedAt" IS NULL`; có dòng → True, không dòng → False | đúng | AC1, AC2 |
| T5 | infrastructure | Session chưa có transaction → rollback sau khi đọc; đã có sẵn → không rollback | đúng | AC4 |
| T6 | api | Repository ném lỗi | 500, không phải 401 | AC5 |
| T7 | thật | Đọc DB thật: UUID ngẫu nhiên → False; 1 user còn hoạt động → True (chỉ SELECT) | đúng | AC1, AC2 |

## 4. Các bước code
- [x] **1.** Port + SQL repository — file: `ports/account_repository.py`, `infrastructure/db/account_repository.py` — test: T4, T5.
- [x] **2.** `get_current_user_id` gọi `is_active` + wiring `deps.py` + fake cho test API — test: T1, T2, T3, T6.
- [x] **3.** Chạy thật (chỉ đọc) — test: T7.
- [x] **4.** Docs: `CLAUDE.md` §9, `docs/database.md` (`User.DeletedAt`), tick `plans/auth-jwt.md`.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-11-13 | Tạo plan | Mục "Cải thiện sau" của `plans/auth-jwt.md` |
| 2026-11-13 | Chạy thật (chỉ SELECT): UUID ngẫu nhiên → False, 1 user còn hoạt động → True, `begin()` ngay sau vẫn chạy. DB chưa có user đã xóa. Từ máy dev: lần đầu ~300 ms (mở kết nối), sau đó trung vị 174 ms / tối đa 263 ms. `EXPLAIN`: Seq Scan vì bảng nhỏ (cost 1.21), có `User_pkey` | Bước 3 |
| 2026-11-13 | Test API dùng `tests/unit/api/conftest.py` (autouse): mọi user coi như còn hoạt động, không chạm DB | Bước 2 |
