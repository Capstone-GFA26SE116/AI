# K-SiP v2 — kết quả (plans/K-sip-v2.md)

Dữ liệu: 24 cặp (developer, Category), 7133 task, 11 developer. Xong sớm hơn ước tính 34.3%, đúng bằng 42.1%, trễ 23.6%.

Sai số = |dự đoán − thật| ÷ thật. Chỉ số chính: sai số tổng giờ của khối 10 task liên tiếp (trung vị). Tiêu chí đạt: C tốt hơn A ≥ 20% ở ≥ 6 developer.

## 1. Kết quả chung (Category, K₀ = 1,3, α = 0,2)

| Cách | Sai số tổng giờ khối 10 task (trung vị) ↓ | … (trung bình) ↓ | Sai số từng task (trung vị) ↓ | … (trung bình) ↓ | Task sai ≤ 25% ↑ |
|---|---|---|---|---|---|
| A — người dùng tự đoán | 23.2% | 38.3% | 16.7% | 90.4% | 57.5% |
| B — luôn × 1,3 | 36.9% | 60.9% | 30.0% | 135.1% | 14.1% |
| C — METANOIA | 19.1% | 34.2% | 24.3% | 96.0% | 50.7% |

C so với A: tổng giờ tốt hơn **17.6%** (trung vị), từng task tệ hơn **45.6%** (trung vị). 703 khối, 7133 task.

## 2. Theo từng developer (khối sau 0 task)

| Developer | A: sai số tổng giờ | C: sai số tổng giờ | C tốt hơn A | Đạt (≥ 20%) |
|---|---|---|---|---|
| 1 | 21.3% | 27.7% | -30.0% | ❌ |
| 13 | 21.8% | 17.7% | 18.7% | ❌ |
| 22 | 38.2% | 44.4% | -16.4% | ❌ |
| 24 | 41.3% | 68.3% | -65.3% | ❌ |
| 26 | 23.9% | 18.4% | 22.9% | ✅ |
| 42 | 14.5% | 13.6% | 6.1% | ❌ |
| 43 | 27.9% | 23.7% | 15.1% | ❌ |
| 50 | 60.8% | 64.6% | -6.1% | ❌ |
| 54 | 82.5% | 51.1% | 38.0% | ✅ |
| 58 | 25.8% | 20.0% | 22.5% | ✅ |
| 65 | 17.9% | 13.7% | 23.6% | ✅ |

**4/11 developer đạt.**

## 3. Tiêu chí: cần bao nhiêu task

| Chỉ tính khối | Developer đạt | Đạt tiêu chí (≥ 6) | Thời gian tương ứng (trung vị) |
|---|---|---|---|
| sau 0 task | 4/11 | ❌ | 0 ngày (24 cặp) |
| sau 10 task | 5/11 | ❌ | 229 ngày (24 cặp) |
| sau 20 task | 5/10 | ❌ | 297 ngày (22 cặp) |
| sau 50 task | 5/10 | ❌ | 427 ngày (17 cặp) |
| sau 100 task | 2/8 | ❌ | 569 ngày (13 cặp) |

**N nhỏ nhất đạt tiêu chí: không đạt ở mọi N.**

## 4. Sai số tổng giờ theo vị trí khối

| Cách | task 1-10 | task 11-20 | task 21-50 | task 51-100 | task >100 |
|---|---|---|---|---|---|
| A | 24.1% | 17.8% | 25.9% | 23.2% | 22.7% |
| B | 52.3% | 37.1% | 47.8% | 44.0% | 34.8% |
| C | 29.3% | 19.4% | 23.4% | 22.1% | 17.7% |
## 5. K₀ khác nhau (mức onboarding)

| K₀ | B: × K₀ không học | C: METANOIA | C ở 10 task đầu | C tốt hơn A | Developer đạt | K về gần K₀=1,3 (< 5%) sau task thứ |
|---|---|---|---|---|---|---|
| 1 | 23.2% | 18.5% | 20.6% | 20.3% | 4/11 | 10 |
| 1.1 | 25.8% | 18.6% | 21.5% | 19.7% | 4/11 | 8 |
| 1.25 | 34.6% | 18.9% | 26.1% | 18.2% | 4/11 | 1 |
| 1.3 | 36.9% | 19.1% | 29.3% | 17.6% | 4/11 | — |
| 1.5 | 54.6% | 19.1% | 42.1% | 17.3% | 4/11 | 8 |
| 1.8 | 84.1% | 19.7% | 61.1% | 15.1% | 4/11 | 12 |

A (người dùng tự đoán): 23.2%.

## 6. Bản chạy phụ

| Bản | Dữ liệu | A tổng giờ | C tổng giờ | C tốt hơn A | A từng task | C từng task | Developer đạt |
|---|---|---|---|---|---|---|---|
| Bản chính (Category) | 24 cặp · 7133 task | 23.2% | 19.1% | 17.6% | 16.7% | 24.3% | 4/11 |
| Bỏ task giờ thật = ước tính | 21 cặp · 4115 task | 28.1% | 24.9% | 11.4% | 47.8% | 49.7% | 4/11 |
| Loại việc hẹp (SubCategory) | 70 cặp · 6875 task | 19.6% | 16.5% | 16.0% | 16.7% | 25.0% | 3/11 |
| α = 0,1 | 24 cặp · 7133 task | 23.2% | 20.5% | 11.5% | 16.7% | 23.4% | 1/11 |
| α = 0,3 | 24 cặp · 7133 task | 23.2% | 18.2% | 21.4% | 16.7% | 25.7% | 6/11 |

*Nguồn dữ liệu: SiP dataset, Software in Partnership Ltd / Derek M. Jones, CC BY-NC-ND 4.0.*
