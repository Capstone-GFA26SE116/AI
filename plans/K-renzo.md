# K-Renzo — Kiểm tra lại cơ chế học K (và α = 0,3) trên dữ liệu của một người

Trạng thái: Xong · Branch: `research/k-renzo` · Cập nhật: 2026-11-17

<!-- Hiếu chốt 2026-11-17: sau K-SiP v2 (α = 0,3 đạt 6/11 nhưng α được thấy SAU khi chạy), kiểm α = 0,3
trên một bộ dữ liệu khác. Mọi luật dưới đây viết TRƯỚC khi chạy trên Renzo. Không đổi code production. -->

## 0. Mục tiêu
- **Làm gì:** Chạy cùng cách đo của K-SiP v2 (`research/kbias_sip/design2.py`) trên Renzo Pomodoro:
  một kỹ sư phần mềm tự ước lượng và ghi lại mọi việc của mình suốt 10 năm (2009–2019). Gần với
  freelancer hơn SiP (một người, nhiều khách, tự ước lượng).
- **Câu hỏi:**
  1. Cơ chế học K (α = 0,2, K₀ = 1,3) có giúp sát tổng giờ hơn người tự đoán không, trên một bộ dữ liệu khác?
  2. α = 0,3 (thấy tốt trên SiP) có còn tốt hơn α = 0,2 trên dữ liệu mới không?
- **Luật quyết định (chốt trước khi chạy):**
  - α = 0,3 được đề xuất đưa vào production **chỉ khi** trên Renzo, bản chính: sai số tổng giờ của
    C(α = 0,3) **≤** C(α = 0,2) **và** cả hai tốt hơn A. Ngược lại giữ α = 0,2.
  - Mục tiêu 20%: C(α = 0,2) tốt hơn A ≥ 20% (sai số tổng giờ, trung vị khối 10 task).
- **Ngoài phạm vi:** đổi code production; dùng mô tả task (bộ dữ liệu chỉ có mã số).

## 1. Dữ liệu
- Nguồn: <https://github.com/Derek-Jones/renzo-pomodoro> (`all-tasks.csv`), Derek M. Jones. Repo không ghi giấy phép:
  **không commit dữ liệu**, chỉ dùng cho nghiên cứu, ghi nguồn. Script nhận đường dẫn qua `--data`.
- Cột: `X.words` (các nhãn `@…`), `DONE` (1 = xong), `date`, `estimate`, `actual` (đơn vị pomodoro = 25 phút).
- Nhãn đầu tiên phần lớn là **khách hàng / dự án** (@bbc, @uswitch…), không phải loại việc.

| Bước lọc | Lý do |
|---|---|
| Có cả estimate và actual | Cần đủ cặp số |
| `DONE = 1` | Như SiP: chỉ task đã xong |
| estimate > 0 và actual > 0 | actual = 0 là task không làm |
| Bỏ task có nhãn `@meeting` | Họp = task Cố định, METANOIA không học |
| Thứ tự: theo `date`, trùng ngày giữ thứ tự trong file | File đã sắp theo ngày |

## 2. Thiết kế (giống K-SiP v2)
- **A** tự đoán (k = 1) · **B** × K₀ không học · **C** METANOIA (hàm production, clamp, bỏ học task lệch > 5×).
- Sai số = |dự đoán − thật| ÷ thật. Chính: tổng giờ khối 10 task (trung vị). Phụ: từng task.
- **Bản chính:** một loại việc cho mọi task (một người dùng, một K) — trường hợp người dùng mới chỉ có 1 loại.
- **Bản phụ:** loại việc = nhãn đầu tiên (khách / dự án), chỉ cặp ≥ 10 task; báo số cặp C tốt hơn A ≥ 20%.
- Thêm: α = 0,1 / 0,2 / 0,3; K₀ 1,0 / 1,1 / 1,25 / 1,3 / 1,5 / 1,8; bỏ task actual = estimate.

## 3. Code
- `research/kbias_renzo/load.py`: đọc CSV, lọc theo §1, trả về `SipTask` (dùng lại kiểu của SiP:
  developer_id = 1, category = "all", sub_category = nhãn đầu tiên, giờ = pomodoro × 25 phút).
- `research/kbias_renzo/run.py`: ghi `research/kbias_renzo/results/results.md`.
- Lệnh: `uv run python -m research.kbias_renzo.run --data ~/data/renzo-pomodoro --out research/kbias_renzo/results`
