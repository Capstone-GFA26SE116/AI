# K-SiP — Kiểm chứng việc học K_bias trên dataset SiP

Trạng thái: Nháp · Branch: `research/k-convergence-sip` · Cập nhật: 2026-10-29

<!-- Không phải FR mới: đây là bài kiểm chứng offline cho FR-17. Không đụng DB, không LLM, không quota. -->

## 0. Mục tiêu

- **Làm gì (1–2 câu):** Phát lại (replay) các task thật trong dataset SiP qua **đúng hàm K của FR-17** (`domain/kbias.py`), theo thứ tự thời gian. Đo xem giờ đề xuất `T_est × K` có đoán sát giờ thực tế hơn việc tin nguyên `T_est` không, và K cần bao nhiêu task để ổn định.
- **Câu hỏi cần trả lời** (thay cho câu "K có hội tụ về tốc độ thật không", vì SiP không có "tốc độ thật"):
  - **Q1 – Hội tụ:** Sau bao nhiêu task thì K bám sát mức lệch dài hạn của từng người (theo từng loại việc)?
  - **Q2 – Có ích không:** So với tin nguyên ước lượng (Level 1) và các cách đơn giản khác, K giúp đoán **tổng giờ của một nhóm task** sát hơn bao nhiêu?
- **Ai chạy, khi nào:** Hiếu chạy tay bằng một lệnh, lấy kết quả (CSV + biểu đồ + `results.md`) đưa vào báo cáo và slide.
- **Tiêu chí xong:**
  - [ ] AC1: Bước lọc dữ liệu in ra đúng "phễu" ở §1.3 (12.299 dòng → 7.176 task sạch → 24 cặp có ≥ 10 task theo `Category`). Lệch số → dừng, báo Hiếu.
  - [ ] AC2: Replay gọi `update_k_bias`, `skip_reason`, `proposed_minutes` của `domain/kbias.py`. Không chép lại công thức K.
  - [ ] AC3: Không nhìn trước tương lai: dự đoán task thứ n chỉ dùng K học từ task 1…n−1 (test T5).
  - [ ] AC4: Chạy đủ mọi phương pháp ở §2.3 và mọi chỉ số ở §2.4. Báo **tất cả**, không bỏ cái nào xấu.
  - [ ] AC5: Ra `results/` gồm các CSV ở §2.6, 3 biểu đồ PNG, và `results.md` viết **bằng tiếng Anh** (luật của giảng viên hướng dẫn: mọi tài liệu nộp phải bằng tiếng Anh).
  - [ ] AC6: Chạy lại 2 lần ra cùng con số (không có ngẫu nhiên).
  - [ ] AC7: `uv run pytest -q` và `uv run ruff check .` sạch.
- **Ngoài phạm vi:** Đổi công thức K của production; chạy trên dữ liệu user thật trong Supabase; thử nghiệm 2 tuần với người dùng thật (đã bỏ); buffer `Δt` (SiP không có khái niệm nghỉ giữa task); productive hours.

## 1. Bên ngoài — nói chuyện với ai

### API
Không có. Chỉ là script dòng lệnh.

### Database
- Đọc: không.
- Ghi: không. **Tuyệt đối không import `infrastructure/` hay `api/`.**

### Dịch vụ khác
- Không gọi LLM, không gọi Backend.
- **Dữ liệu:** repo [Derek-Jones/SiP_dataset](https://github.com/Derek-Jones/SiP_dataset), 2 file `Sip-task-info.csv` và `est-act-dates.csv`. Bài mô tả: Jones & Cullum, *A conversation around the analysis of the SiP effort estimation dataset* (arXiv:1901.01621).

### 1.1 Cấu trúc dữ liệu (đã kiểm 2026-10-29)

`Sip-task-info.csv`: 12.299 dòng, 10.266 task, 22 developer, giai đoạn 2004–2014. Mã hóa **cp1252**: đọc bằng UTF-8 sẽ lỗi.

| Cột SiP | Dùng làm |
|---|---|
| `TaskNumber` | khóa, nối với file ngày |
| `DeveloperID` | **User** |
| `Category` (Development / Management / Operational) | **Domain** — bản chính |
| `SubCategory` (24 giá trị: Enhancement, Bug, Support, Testing…) | **Domain** — bản so sánh |
| `HoursEstimate` | **T_est** |
| `HoursActual` | **T_act** |
| `StatusCode` | lọc |

`est-act-dates.csv`: `TaskNumber, EstimateOn, StartedOn, CompletedOn`, định dạng `02-Nov-04`. `EstimateOn` có đủ cho mọi dòng; `CompletedOn` thiếu 2.586 dòng.

Ratio T_act/T_est không có đơn vị, nên SiP dùng giờ còn hàm K dùng phút cũng không sao.

### 1.2 Bản quyền

Giấy phép **CC BY-NC-ND 4.0**: được dùng cho nghiên cứu phi thương mại, phải ghi nguồn, không được phát hành bản đã sửa.
→ **Không commit CSV vào repo.** Script nhận đường dẫn qua `--data`; README hướng dẫn tải về. Mọi chỗ dùng số liệu (báo cáo, slide) phải ghi nguồn Software in Partnership Ltd / Derek M. Jones.

### 1.3 Phễu lọc dữ liệu (số kiểm được, dùng cho AC1)

| Bước | Còn lại |
|---|---|
| Toàn bộ dòng | 12.299 |
| Bỏ task có > 1 developer (1.384 task) | 8.882 task |
| Chỉ giữ `StatusCode` ∈ {`FINISHED`, `COMPLETED`, `RELEASED`} | 7.176 task |
| Cặp (developer, `Category`) | 39 cặp, trong đó **24 cặp có ≥ 10 task** (7.133 task) |
| Cặp (developer, `SubCategory`) | 163 cặp, trong đó 70 cặp có ≥ 10 task (6.875 task) |

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 1 | Task nhiều developer: `HoursEstimate` là của cả task, `HoursActual` = tổng giờ mọi người (đã kiểm: khớp 100%) | **Bỏ** task có > 1 developer | Không biết chia ước lượng cho từng người. METANOIA cũng là 1 người – 1 task |
| 2 | Có 8 giá trị `StatusCode` | Giữ `FINISHED` + `COMPLETED` + `RELEASED`. Bỏ `CHRONICLE` (đang làm dở, giờ thực tế chưa đủ), `CANCELLED`, `ESTIMATED`, `TEMPLATE`, `AUTHORISE` | Bài arXiv §2.2: vòng đời `… CHRONICLE → COMPLETED → RELEASED → FINISHED`, nên `RELEASED` là task đã xong (bước 1, §5) |
| 3 | Bản quyền ND | Không commit dữ liệu (§1.2) | Đúng giấy phép |
| 4 | SiP là developer công ty, không phải freelancer | Ghi rõ trong phần hạn chế của `results.md` | Thứ được kiểm là **thuật toán học thiên kiến của một cá nhân**. Chưa có dataset freelancer công khai nào có cặp ước lượng/thực tế |

## 2. Bên trong — logic

### Code nằm ở đâu
Không đặt trong `src/metanoia_ai/`, vì thư mục đó là code server được deploy lên Azure.
```
research/kbias_sip/
  README.md        # cách tải dữ liệu, cách chạy, ghi nguồn
  load.py          # đọc CSV (cp1252), nối ngày, lọc → list[SipTask]; in phễu §1.3
  methods.py       # các phương pháp §2.3 (pure functions)
  replay.py        # replay 1 cặp: dự đoán TRƯỚC, cập nhật SAU
  metrics.py       # chỉ số §2.4
  plots.py         # 3 biểu đồ
  run.py           # CLI: --data <thư mục SiP> --out <thư mục kết quả>
  results/         # sinh ra khi chạy; chỉ commit results.md + PNG, không commit CSV lớn
tests/unit/research/  # test T1–T12
```
- Import từ production: `from metanoia_ai.domain.kbias import update_k_bias, skip_reason, proposed_minutes, INITIAL_K_BIAS, DEFAULT_ALPHA` và `TaskType` trong `domain.models`.
- ⚠️ Thư viện mới, **chỉ ở nhóm dev**: `pandas`, `matplotlib` (Hiếu đã đồng ý 2026-10-29): `uv add --dev pandas matplotlib`.
- Thêm `"."` vào `pythonpath` trong `[tool.pytest.ini_options]` để test import được `research.kbias_sip`.
- Lệnh chạy: `uv run python -m research.kbias_sip.run --data ~/data/SiP_dataset --out research/kbias_sip/results`.

### 2.1 Luồng xử lý
1. `load.py` đọc 2 CSV, lọc theo §1.3 và in phễu. Lệch số với §1.3 → dừng.
2. Sắp xếp theo `EstimateOn`; trùng ngày thì theo `TaskNumber`.
3. Nhóm theo (developer, domain), với domain = `Category` (bản chính), rồi chạy lại toàn bộ với `SubCategory`.
4. Với mỗi cặp và mỗi phương pháp: replay theo §2.2.
5. Tính chỉ số §2.4 cho tổng thể và theo vị trí task (n).
6. Chạy lưới độ nhạy §2.5.
7. Ghi CSV, vẽ biểu đồ, viết `results.md`.

### 2.2 Replay một cặp (cốt lõi; luật cho mọi phương pháp)
```
state = trạng thái ban đầu của phương pháp
với mỗi task theo thứ tự thời gian (n = 1, 2, …):
    k   = hệ số hiện tại của phương pháp            # chỉ từ task 1..n-1
    dự đoán = T_est × k                              # proposed_minutes(t_est, k, buffer=0)
    ghi (cặp, n, T_est, T_act, k, dự đoán)          # ĐO TRƯỚC
    nếu skip_reason(FLEXIBLE, T_est, T_act) là None:
        state = cập nhật(state, T_act / T_est)       # HỌC SAU
```
- Task bị bỏ qua (ratio ngoài [1/5, 5]: 4,3% số task) **vẫn được dự đoán và tính sai số**, chỉ là không dùng để học. Production cũng vậy: user vẫn thấy giờ đề xuất cho task đó.
- K luôn bị clamp [0.5, 3.0] (hàm production đã làm sẵn).

### 2.3 Các phương pháp so sánh
| Mã | Tên | Hệ số k | Ý nghĩa |
|---|---|---|---|
| B0 | Raw | luôn 1.0 | Level 1: tin nguyên ước lượng của người dùng |
| B1 | Fixed 1.3 | luôn 1.3 | Chỉ nhân hằng số lấy từ nghiên cứu, không học |
| B2 | Mean | trung bình nhân (geometric mean) mọi ratio đã học; chưa có thì 1.3 | "Nhớ mọi thứ như nhau" |
| B3 | Last | ratio của task học gần nhất; chưa có thì 1.3 | "Chỉ nhớ lần gần nhất" |
| **M** | **METANOIA (EMA)** | `update_k_bias`, α = 0.2, K0 = 1.3 | Code production |
| X1 | EMA trên log ratio | `K = exp(L)`, `L ← α·ln(r) + (1−α)·L`, L0 = ln 1.3, clamp như M | **Thăm dò**, không phải production (xem #6) |

### 2.4 Chỉ số
Sai số của một dự đoán: `e = ln(dự đoán / T_act)`. Dương là đoán dư, âm là đoán thiếu, và đoán gấp đôi hay một nửa bị phạt như nhau.

| Chỉ số | Cách tính | Vai trò |
|---|---|---|
| **P1 — Sai số tổng giờ theo khối** | Chia task mỗi cặp thành khối 10 task liên tiếp; mỗi khối tính `|ln(Σ dự đoán / Σ T_act)|`; lấy trung vị trên mọi khối. Khối cuối < 10 task thì bỏ | **Chỉ số chính** (lý do ở #7) |
| S1 — Sai số từng task | trung vị và trung bình của `|e|` | phụ |
| S2 — Độ lệch | trung bình `e` (gần 0 là tốt) | phụ |
| S3 — PRED(25) | tỷ lệ task có `|dự đoán/T_act − 1| ≤ 0.25` | phụ, chỉ số quen thuộc trong ngành |
| **C1 — Hội tụ của K** | Mỗi cặp có mục tiêu `G` = trung bình nhân ratio của mọi task học được của cặp đó (nhìn cả quá khứ lẫn tương lai, **chỉ dùng để mô tả, không dùng để dự đoán**). Khoảng cách `d_n = |ln K_n − ln G|`. Báo trung vị `d_n` theo n, và **n nhỏ nhất mà từ đó trung vị d_n luôn ≤ 0.10** (≈ lệch 10%) | Trả lời Q1 |
| C1-lý thuyết | `0.2 × 0.8ⁿ`: tốc độ học lý tưởng khi ratio không đổi (ví dụ ratio luôn 1.5 thì sau 5 task K lệch < 5%) | So lý thuyết với thực tế |

Mọi chỉ số đều báo theo **nhóm vị trí n**: 1, 2, 3, 4–5, 6–10, 11–20, 21–50, 51–100, > 100. Chỉ tính các cặp có ≥ 10 task. Báo thêm trung vị theo từng developer để developer 58 (1.312 task, ~19% dữ liệu) không lấn át kết quả.

### 2.5 Độ nhạy
- α ∈ {0.1, 0.2, 0.3} × K0 ∈ {1.0, 1.3} cho M, tính P1 và S1.
- Domain = `Category` so với `SubCategory`.
- Bỏ các task có ratio đúng bằng 1 (xem #8).

### 2.6 Đầu ra
- `results/predictions.csv`: 1 dòng = (domain_mode, method, dev, domain, n, t_est, t_act, k, pred, used_for_learning).
- `results/summary.csv`: mỗi phương pháp × mỗi chỉ số × mỗi nhóm n.
- `results/sensitivity.csv`.
- `results/fig1_k_trajectory.png`: K của 4–6 cặp nhiều task nhất, kèm đường G của từng cặp và đường lý thuyết.
- `results/fig2_error_by_n.png`: P1 (hoặc S1) theo nhóm n, mỗi phương pháp một đường.
- `results/fig3_sensitivity.png`: bảng nhiệt α × K0.
- `results/results.md` (**tiếng Anh**): câu hỏi, dữ liệu và phễu, phương pháp, bảng kết quả, trả lời Q1/Q2, hạn chế, ghi nguồn.

### Luật & trường hợp đặc biệt
- Không có ngẫu nhiên (AC6).
- Cặp < 10 task vẫn được replay (ghi vào `predictions.csv`) nhưng không tính vào chỉ số.
- Hàm production ném lỗi khi `t_est ≤ 0`. SiP không có giá trị 0 (min 0.01), nhưng `load.py` vẫn phải lọc và đếm.

### Vấn đề & quyết định
| # | Vấn đề | Quyết định | Lý do |
|---|---|---|---|
| 5 | **Chạy thử 2026-10-29 cho thấy SiP ngược Planning Fallacy:** trung vị ratio = 1.0, trung bình nhân ≈ 0.88 (đa số làm **nhanh hơn hoặc đúng** ước lượng) | Giữ nguyên test, viết thẳng vào `results.md` | Đây là điểm mạnh nếu trình bày đúng: B1 (hằng số 1.3) **tệ nhất** (P1 ≈ 0.32 so với 0.19 của M), chứng minh không có hằng số chung nào đúng cho mọi người, phải học riêng. M bắt đầu từ 1.3 vẫn tự kéo K về đúng hướng |
| 6 | Chạy thử: EMA trên ratio bị các lần trễ lớn kéo K lên (trung bình cộng của ratio > trung bình nhân), nên độ lệch S2 của M cao hơn B0. X1 (EMA trên log) giảm độ lệch từ +0.17 → +0.07 | X1 chỉ là **phương án thăm dò**. **Không đổi production trong plan này** ⚠️ | Nếu bản chạy đầy đủ xác nhận X1 tốt hơn → mở câu hỏi riêng trong `docs/open-questions.md` cho FR-17 |
| 7 | Chạy thử: **theo từng task, B0 (Raw) thắng M** (trung vị \|e\| 0.155 so với 0.242), nhưng **theo tổng giờ của khối 10 task, M thắng B0** (0.186 so với 0.217) | Chọn P1 làm chỉ số chính, nhưng **báo đủ cả S1–S3** | METANOIA xếp **nhiều task vào giờ rảnh của một ngày/tuần**; sai tổng giờ mới làm vỡ lịch. ⚠️ Phải nói thật rằng chỉ số chính được chọn sau khi đã chạy thử, và đưa cả số từng task vào `results.md` để không bị coi là chọn số đẹp |
| 8 | 42% task có `T_act` **đúng bằng** `T_est`, nhiều khả năng do người làm ghi giờ theo ước lượng chứ không đo thật | Chạy thêm bản bỏ các task này (§2.5), ghi vào phần hạn chế | Những task này làm B0 trông "hoàn hảo". Đây cũng chính là lý do METANOIA đo T_act bằng timer thay vì để người dùng tự khai |
| 9 | Thứ tự thời gian: dùng `EstimateOn` (luôn có) chứ không dùng `CompletedOn` (thiếu 2.586 dòng) | Sắp theo `EstimateOn`, trùng ngày thì theo `TaskNumber` | Đơn giản hóa: giả định task ước lượng trước thì xong trước. 46% task trùng ngày với task khác nên nhiễu nhỏ. Ghi vào hạn chế |
| 10 | Bỏ buffer khi dự đoán | `buffer = 0` | SiP đo giờ làm, không tính giờ nghỉ |

## 3. Test
| ID | Tầng | Tình huống | Kết quả mong đợi | AC |
|---|---|---|---|---|
| T1 | load | CSV nhỏ có ký tự cp1252 (byte `0x92`) | Đọc được, không lỗi | AC1 |
| T2 | load | Task 2 developer, task `CANCELLED`, task `CHRONICLE` | Bị loại, phễu đếm đúng từng bước | AC1 |
| T3 | load | Hai task cùng `EstimateOn` | Sắp theo `TaskNumber` | AC6 |
| T4 | replay | Ratio luôn 1.5, M | `K_n` đúng bằng `1.5 − 0.2×0.8ⁿ` (sai số < 1e-9); từ task 5 trở đi K lệch 1.5 < 0.075 | AC2 |
| T5 | replay | Task thứ 3 có ratio rất khác | Dự đoán của task 3 dùng K **trước** khi học task 3 | AC3 |
| T6 | replay | Task ratio 10 (outlier) | Vẫn có dự đoán, `used_for_learning = false`, K không đổi | AC2 |
| T7 | replay | Ratio luôn 4 qua nhiều task | K không vượt 3.0 (clamp) | AC2 |
| T8 | methods | B0, B1, B2, B3, X1 trên dãy ratio tự tạo | Đúng giá trị tính tay | AC4 |
| T9 | metrics | P1 với 23 task | 2 khối (khối 3 task cuối bị bỏ), giá trị đúng tính tay | AC4 |
| T10 | metrics | C1: G đã biết, dãy K đã biết | `d_n` và "n hội tụ" đúng | AC4 |
| T11 | metrics | Cặp 1 task, cặp 9 task | Không tính vào chỉ số, không gây lỗi | AC4 |
| T12 | run | Chạy 2 lần trên CSV nhỏ | Hai `summary.csv` giống hệt nhau | AC6 |

## 4. Các bước code
*(Mỗi bước = 1 việc nhỏ + test của nó. Claude Code DỪNG sau mỗi bước.)*
- [x] **1.** Đọc bài arXiv 1901.01621, xác nhận nghĩa của `FINISHED` / `COMPLETED` / `CHRONICLE` / `RELEASED`, ghi kết quả vào §5. Chưa code. Nếu khác #2 → dừng hỏi Hiếu.
- [x] **2.** `uv add --dev pandas matplotlib`; thêm `"."` vào `pythonpath`; tạo `research/kbias_sip/README.md` (cách tải, cách chạy, ghi nguồn, giấy phép); thêm `research/kbias_sip/results/*.csv` vào `.gitignore`.
- [x] **3.** `load.py` + T1–T3. Chạy trên dữ liệu thật, so phễu với §1.3 (AC1).
- [ ] **4.** `methods.py` + `replay.py` + T4–T8.
- [ ] **5.** `metrics.py` + T9–T11.
- [ ] **6.** `run.py`: chạy bản chính (`Category`) và ghi CSV + T12. **Dừng, gửi Hiếu bảng `summary.csv` trước khi làm tiếp.**
- [ ] **7.** Độ nhạy §2.5.
- [ ] **8.** `plots.py`: 3 biểu đồ.
- [ ] **9.** Viết `results.md` (tiếng Anh): trả lời Q1, Q2, có phần hạn chế (#4, #5, #8, #9) và ghi nguồn. Nếu X1 thắng M rõ ràng → thêm dòng vào `docs/open-questions.md`.

## 5. Nhật ký thay đổi
| Ngày | Thay đổi | Lý do (bug / quyết định mới) |
|---|---|---|
| 2026-10-29 | Tạo plan. Bước 0 (kiểm dataset + chạy thử nhanh) làm trên Claude web | Hiếu chọn theo mọi đề xuất: câu hỏi "dự đoán sát hơn không", B0–B3, độ nhạy α/K0, `Category` + `SubCategory`, thư mục `research/`, pandas + matplotlib dev |
| 2026-10-29 | Bước 1: giữ `RELEASED`, phễu mới 7.176 task (§1.3, AC1, #2) | Bài arXiv §2.2: `RELEASED` nằm giữa `COMPLETED` và `FINISHED`. Số chạy thử ở #5–#7 tính trên bộ cũ (6.944), sẽ tính lại ở bước 6 |
