# K-SiP v2 — Bài test cơ chế tự học K, thiết kế lại (đo bằng %, tiêu chí theo developer)

Trạng thái: Xong · Branch: `research/k-sip-v2` · Cập nhật: 2026-11-17

<!-- Hiếu và Claude Code brainstorm lại từ đầu (2026-11-17), Hiếu duyệt từng mục. Thay cách trình bày
của K-convergence.md (đo bằng log, chỉ số chính chọn sau khi chạy thử). Dùng lại load.py, replay.py,
methods.py; không đổi code production. Ghi rõ trong báo cáo: thiết kế này viết sau một lần chạy thử. -->

## 0. Mục tiêu
- **Làm gì:** Chứng minh cơ chế tự học K (công thức + luật đi kèm) giúp dự đoán sát giờ thật hơn người
  dùng tự đoán, và cần bao nhiêu task / bao nhiêu ngày. Không test xếp lịch, timer, AI chat.
- **3 câu hỏi:** (1) hiệu quả hơn bao nhiêu %; (2) cần bao nhiêu task, bao nhiêu ngày; (3) K₀ sai
  (chọn sai mức onboarding) thì sau bao nhiêu task K tự sửa.
- **Tiêu chí xong:**
  - [x] AC1 — Cùng phễu lọc với K-convergence (24 cặp, 7.133 task, 11 developer).
  - [x] AC2 — So 3 cách: A người dùng tự đoán (k = 1), B nhân K₀ không học, C METANOIA (α 0,2, K₀ 1,3,
        clamp, bỏ học task lệch > 5×), dùng đúng hàm production.
  - [x] AC3 — Sai số = |dự đoán − thật| ÷ thật. Chính: tổng giờ mỗi khối 10 task liên tiếp. Phụ: từng task.
  - [x] AC4 — Tiêu chí chốt trước: C thấp hơn A ≥ 20% (trung vị khối của developer) ở ≥ 6/11 developer,
        báo N nhỏ nhất đạt (chỉ tính khối sau N task) và số ngày tương ứng.
  - [x] AC5 — Bản phụ: K₀ ∈ {1,0; 1,1; 1,25; 1,3; 1,5; 1,8}; bỏ task giờ thật = ước tính; SubCategory; α 0,1 / 0,3.
  - [x] AC6 — Không ngẫu nhiên; không commit dữ liệu SiP (CC BY-NC-ND 4.0).

## 1. Quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Đơn vị sai số | % (|dự đoán − thật| ÷ thật) thay cho log | Dễ giải thích với hội đồng. Log vẫn ở K-convergence |
| 2 | "Đạt" theo developer | Trung vị sai số khối của mọi cặp của developer đó | Developer có nhiều loại việc; không để 1 người nhiều task lấn át |
| 3 | "Sau N task" | Chỉ tính khối bắt đầu sau task N của mỗi cặp; N ∈ {0, 10, 20, 50, 100}. Mẫu số = developer còn khối | Trả lời "cần bao nhiêu task" |
| 4 | Đổi N task ra ngày | Trung vị (ngày ước lượng task N − ngày task 1) trên các cặp có ≥ N task | SiP chỉ có ngày ước lượng đủ cho mọi dòng |
| 5 | K₀ sai tự sửa sau bao lâu | Trung vị |K_n(K₀) − K_n(1,3)| ÷ K_n(1,3) theo n; báo n đầu tiên < 5% | So với mặc định; lý thuyết giảm (1 − α)ⁿ |
| 6 | Mức onboarding | 1,1 / 1,25 / 1,5 / 1,8 theo BE (`MeService.OverrunKBias`) | Sheet Vấn đề #15: chưa thống nhất với K₀ = 1,3 |

## 2. Code
- `research/kbias_sip/design2.py`: sai số %, khối, theo developer, tiêu chí, N → ngày, K₀ hội tụ.
- `research/kbias_sip/run2.py`: CLI, ghi `results/results_v2.md`.
- Lệnh: `uv run python -m research.kbias_sip.run2 --data ~/data/SiP_dataset --out research/kbias_sip/results`
