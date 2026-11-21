# CI-tests — Run lint, tests and coverage in GitHub Actions

Trạng thái: Xong · Branch: `ci/run-tests` · Cập nhật: 2026-11-21

<!-- Hiếu đồng ý 2026-11-21: Register hứa "≥ 80% unit-test coverage for the core logic, checked
automatically in CI", nhưng CI chỉ deploy, không chạy test. Việc nhỏ, không đổi code sản phẩm. -->

## 0. Mục tiêu
- **Làm gì:** mỗi PR và mỗi lần deploy đều chạy `ruff check` + `pytest` với coverage; dưới 80% thì fail.
- **Tiêu chí xong:**
  - [x] AC1: `.github/workflows/tests.yml` chạy trên mọi pull request (và gọi lại được bằng `workflow_call`).
  - [x] AC2: workflow deploy `main_metanoia-ai-2.yml` chỉ build/deploy khi job test pass.
  - [x] AC3: chạy được trên bản checkout sạch, không `.env`, không database (đã thử 21/11: 2227 passed, coverage 95,85%).
  - [x] AC4: lưu `test-results.xml` (JUnit) và `coverage.xml` làm artifact để đưa số vào Report 5.
- **Ngoài phạm vi:** đổi code sản phẩm, thêm thư viện (pytest-cov đã có sẵn trong nhóm dev).

## 1. Số đo lúc làm (21/11/2026, main 0d8e97d)
| Phạm vi | Coverage |
|---|---|
| Toàn bộ `metanoia_ai` | 96% (9089 câu lệnh) |
| `domain` (logic cốt lõi) | 99% |
| `application` (use case) | 99% |
| Thấp nhất | `infrastructure/db/*_repository.py` 54–65% (SQL thật, unit test không chạm DB — đúng §8) |

## 4. Các bước
- [x] 1. Viết `tests.yml` (uv, Python 3.12, `uv sync --frozen`, ruff, pytest `--cov-fail-under=80`, upload artifact).
- [x] 2. Thêm job `test` (gọi `tests.yml`) và `needs: test` vào job `build` của workflow deploy.
- [x] 3. Thử trên worktree sạch không `.env`.

## 5. Nhật ký thay đổi
- 2026-11-21: tạo.
