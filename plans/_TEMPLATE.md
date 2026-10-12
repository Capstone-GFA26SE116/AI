# FR-xx — <Tên chức năng>

Trạng thái: Nháp / Đã duyệt / Đang code / Xong · Branch: `feature/<tên>` · Cập nhật: yyyy-mm-dd

<!-- Cách dùng: copy file này thành plans/FR-xx.md. Thiết kế trên Claude web (mục 0–4),
Hiếu duyệt, rồi mới đưa Claude Code. Claude Code chỉ được tick checkbox ở mục 0 và 4.
⚠️ = quyết định cần báo Backend/team; danh sách đầy đủ nằm ở docs/open-questions.md. -->

## 0. Mục tiêu
- **Làm gì (1–2 câu):**
- **Ai gọi, khi nào:**
- **Tiêu chí xong:**
  - [ ] AC1:
  - [ ] AC2:
- **Ngoài phạm vi:**

## 1. Bên ngoài — nói chuyện với ai

### API
| Method | Endpoint | Request | Response | Lỗi |
|---|---|---|---|---|

### Database (chi tiết bảng: `docs/database.md`)
- Đọc:
- Ghi:

### Dịch vụ khác (LLM, Backend, Google…)
-

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|

## 2. Bên trong — logic

### Code nằm ở đâu
- domain:
- application:
- infrastructure:
- api:

### Luồng xử lý
1.

### Luật & trường hợp đặc biệt
-

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|

## 4. Các bước code
*(Mỗi bước = 1 việc nhỏ + test của nó. Claude Code DỪNG sau mỗi bước.)*
- [ ] **1.** … — file: … — test: T1, T2
- [ ] **2.** …

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
