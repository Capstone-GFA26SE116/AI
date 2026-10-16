# CLAUDE.md — METANOIA AI Service

Rules for Claude Code (and any teammate) working in the `AI` repo.

## 1. What this repo is

METANOIA (capstone, FPT University HCM, 09/2026 – 03/2027): an AI assistant that helps
freelancers plan their work. It learns how much each user under-estimates time and
corrects future schedules.

This repo is the **AI service** (Python), deployed separately from the Backend
(ASP.NET Core). Both share one Supabase database and the same JWT.

- Owner: Hieu (team leader). Other members build Backend and Frontend.
- Python instead of C# was **approved by the supervisor (2026-10-14)**. Still keep core logic
  framework-independent.

**Where information lives — read in this order; if two disagree, STOP and tell Hieu:**
1. `plans/FR-xx.md` — the approved plan for the function being built.
2. `docs/` — product facts: `functional-requirements.md`, `database.md`, `open-questions.md`.
3. This file — rules that apply to every task.

The team Google Doc is for humans only; Hieu copies decided facts into `docs/` or `plans/`.

## 2. Tech stack

- Python 3.12, package manager **uv**
- FastAPI + uvicorn (API), Pydantic + pydantic-settings (validation, config from `.env`)
- PyJWT (verify Backend tokens, HS256)
- SQLAlchemy 2 async + asyncpg (Supabase Postgres) — raw SQL with `text()`, no ORM models
- pytest + httpx (tests), ruff (lint/format)
- pgvector (FR-19): vectors go in as text `"[…]"` + `CAST(… AS vector)`, no extra library
- Planned: LLM providers (OpenAI + Google)
- LLM routing: Free plan → cheapest model; paid plans → cheap model ~90% of calls,
  stronger model ~10% "hard" calls. If one provider fails, switch to the other
  (planned after FR-08a; FR-08a and FR-10 use Gemini only, one model from config).
  Models come from **config, never hard-coded**. Embedding model: `gemini-embedding-2`,
  1536 dimensions (FR-19).

## 3. Commands

```bash
uv sync                                            # install from uv.lock
uv run pytest -q                                   # tests
uv run ruff check .                                # lint
uv run ruff format .                               # format
uv run uvicorn metanoia_ai.api.main:app --reload   # run API → http://127.0.0.1:8000/docs
```

- **Ask Hieu before adding any library.** Then `uv add <name>` (or `--dev`) and commit
  `pyproject.toml` + `uv.lock` together; give the reason in the PR.
- On Hieu's Mac the virtual env lives outside the repo (`UV_PROJECT_ENVIRONMENT`,
  because of iCloud). Do not create `.venv` in the repo or change this.

## 4. Folder structure (layered, like Clean Architecture)

```
src/metanoia_ai/
  config.py              # Settings from .env; bad/missing value → app won't start
  domain/                # pure rules & formulas. No DB, no web, no LLM.
    models.py            #   shared types (enums, small dataclasses)
    <topic>.py           #   e.g. kbias.py, actual_time.py
  application/           # one use case = one user action; outside world only via ports
    ports/               #   interfaces (typing.Protocol), e.g. LearningRepository
    use_cases/           #   e.g. record_task_completion.py
  infrastructure/        # real implementations of ports
    db/                  #   engine.py (connection pool) + <x>_repository.py (SQL)
  api/                   # HTTP edge
    main.py              #   FastAPI app, startup/shutdown, include routers
    auth.py              #   JWT → user_id
    deps.py              #   wiring: build use cases with real repos + config
    routes/              #   one file per feature, e.g. learning.py
tests/unit/              # mirrors src/ (domain/, application/, api/, infrastructure/)
  application/fakes.py   #   in-memory fake repos — application tests never touch the DB
plans/                   # one plan per function (format: plans/_TEMPLATE.md)
docs/                    # product facts (FR list, database, open questions)
```

**Dependency rule:** `api → application → domain`; `infrastructure` implements
`application/ports`. `domain` imports nothing from other layers. Only `api/deps.py`
knows which real implementation is used.

**Adding a new function — follow the FR-17 pattern:**
domain rules → port → use case (+ fake repo tests) → SQL repository → route → wire in `deps.py`.

## 5. Core domain rules (do not change without asking Hieu)

**Task types**
- **Flexible** — duration depends on the user (coding, writing). Has deadline + T_est. Measured and learned from.
- **Fixed** — time set by others (meetings). Blocked time only: never moved, **never learned from**.

**Formulas** (values from config; defaults below)
- `T_proposed = T_est × K_bias + buffer` (buffer = break after a task, from onboarding)
- `K_new = α × (T_act / T_est) + (1 − α) × K_old` — per user, per work domain
- α = 0.2 (`KBIAS_ALPHA`, DB allows (0, 0.30]) · initial K = 1.3 (`KBIAS_INITIAL`) ·
  outlier ratio = 5 (`KBIAS_OUTLIER_RATIO`) · K always clamped to **[0.5, 3.0]**
- Example: T_est 60, K 1.3 → T_proposed 78. Done in 90 → K_new = 0.2×1.5 + 0.8×1.3 = 1.34.

**Learning (FR-17)**
- T_est = the user's own `Task.EstimatedMinutes` (never the AI subtask sum).
  An estimate is the AI's when `EstimatedMinutes = AiEstimatedMinutes` (FR-17b); a changed number is the user's.
- T_act = sum of finished `FocusSession`s, rounded to minutes. Source recorded:
  `Timer` / `SelfReported` / `AutoTimeout` (weakest source wins).
- A task is learned **once** (first `Done`). Skip when: Fixed, not done, estimate set by the AI,
  no time, session still running, unconfirmed time, ratio outside [1/5, 5].
  FR-09 also leaves AI estimates out of the ratio and its average.
- Every update is logged in `KBiasUpdateLog` so any K can be recomputed.

**Scheduling (FR-10)**
- **Code places the slots; the LLM only writes the one-line reason.**
- Order: keep Fixed → Flexible by nearest deadline (tie: longer first) → size with
  T_proposed → prefer productive hours → warn if a deadline can't be met.
- Nothing reaches the calendar until the user approves.
- Task runs over its slot: never auto-move; extend if the next gap fits, else propose a
  re-plan of the rest of the day. No LLM, no quota.

**Plan rules**
- Free plan: K_bias is learned but only shown as a hint, not added to the schedule.
- Productive hours: all plans use onboarding answers; only the top plan also learns from timer data.

## 6. AI features this repo owns

Full text in `docs/functional-requirements.md`. Status: first line of each `plans/FR-xx.md`.

| FR | What | Plan |
|---|---|---|
| — | JWT check (shared by all endpoints) | `plans/auth-jwt.md` |
| FR-17 | Learn K_bias when a Flexible task is done | `plans/FR-17.md` |
| FR-17b | Do not learn K_bias from estimates set by the AI | `plans/FR-17b.md` |
| FR-10 | Schedule proposal (planner + one-line reason per slot) | `plans/FR-10.md` |
| — | Quota check + `AIUsageLog` (before every LLM call) | — |
| FR-08a | AI chat, task level: split a big task into subtasks, then Apply | `plans/FR-08a.md` |
| FR-08b | AI chat, project level: brief → milestones + tasks, documents, streaming | — |
| FR-09 | Similar past tasks: hybrid search (vector + full-text) + RRF. Free/busy time and workload are already in FR-10 | `plans/FR-09.md` |
| FR-19 | Long-term memory: embeddings in pgvector, built in the background | `plans/FR-19.md` |
| FR-12 | Daily "Today" briefing (text only) | — |
| FR-18 | Productive hours: onboarding answers, then timer data | — |

**Not this repo:** FR-11 schedule approval + writing to Google Calendar (Backend).
**Deferred:** FR-13 blind test. **Out of scope:** voice, Eisenhower matrix, training our own model.

## 7. Rules for LLM use

**Quota** (check BEFORE every LLM call)
- Counted per type: schedule runs, chat messages, document reads. Max 50 AI calls per user per day.
- Out of quota → block AI features only; manual features keep working.
- No LLM call → no quota (FR-17, overrun re-plan, pure planner work).

**Input**
- Documents go to the LLM **only when the user attaches them in chat** (from the project's
  library, FR-08). Extract text first, cap at **20,000 tokens**.
- Never send email, full name, OAuth tokens or client contact details (see §9).
- Similar task results (FR-09) return titles as the user wrote them. Mask them with
  `mask_contact` before they go into any LLM prompt.

**Output**
- LLM returns JSON → validate with a Pydantic schema **and** business rules
  (no overlap, before deadline, Fixed untouched). Invalid → retry up to 2 times, then a clear error.
- Every suggestion has a one-line, plain-language reason. Never show internal names
  (`K_bias`, `T_proposed`) to users.
- LLM provider down → planner and manual features still work (return slots without reasons).

**Operations**
- Log every LLM/embedding call in `AIUsageLog`: feature, model, tokens, cost, latency.
- Prompts live in separate versioned files; keep 10–20 sample inputs to re-run after each prompt change.
- Background work (embeddings): retry up to 3 times; a missing embedding never blocks saving a task.
- Targets: task chat (FR-08a) answer < 10 s, no streaming · project chat (FR-08b) first words < 3 s
  (streaming) · schedule proposal < 10 s · similar-task search < 100 ms.

## 8. Database rules

**Setup (Hieu's decision, 2026-10-12):** we develop directly on the shared Supabase
project (no separate dev DB). Free plan = **no backup**: a wrong write or delete is permanent.
All test data is wiped before public launch.

**Ownership**
- The Backend owns the schema. Never run migrations, CREATE/ALTER/DROP from this repo.
- Need a schema change? STOP → tell Hieu (he agrees with the Backend first).
- This service writes only to the tables listed as "AI writes" in `docs/database.md`.
  Everything else is read-only.

**Every query**
- Filtered by the owner's user ID (`Task` has no `UserId` → join `Project.UserId`).
  Only exception: the FR-19 startup scan lists waiting memories of all users
  (memory ID + user ID only, ≤ 100 rows); every later step filters by that row's user.
- Raw SQL with `text()`; quote names (`"KBias"`) — Postgres is case-sensitive with quotes.
- Convert DB strings (`Timer`, `Flexible`…) ↔ domain enums only in `infrastructure/`.

**Testing against the real DB**
- Unit tests never touch the DB (use fakes).
- Read-only checks (SELECT on `information_schema`, `EXPLAIN` without running) are always OK.
- A real run (writes rows) only as a planned step and after Hieu says OK. Create test data
  with a script, then delete exactly those rows and show that 0 remain (like FR-17 step 12).
- Never DELETE/UPDATE rows you did not create. Never TRUNCATE or DROP.

## 9. Security and privacy

- Never commit `.env` or any key/password. Only `.env.example` (names, no values) goes to Git.
- Secrets are `SecretStr` in `config.py`; never print or log them (incl. `DATABASE_URL`).
- JWT (details: `plans/auth-jwt.md`): HS256 only; require `exp`, `iss`, `aud`, `sub`;
  user ID = `sub`. Every endpoint except `/health` needs a valid token.
  Never log tokens; on failure return a plain 401 without the reason.
- Tests use fake keys and never read the real `.env`.
- Never send email, full name, OAuth tokens or client contact details to the LLM.

## 10. Code conventions

- Type hints everywhere. Pydantic models at the edges (API body, config, LLM output).
- Domain types: `@dataclass(frozen=True)` and `StrEnum`. Ports: `typing.Protocol`.
- Anything that waits (DB, LLM, HTTP) is `async`.
- Durations in **minutes**. Datetimes always **timezone-aware**. K_bias rounded to 3 decimals (DB numeric(5,3)).
- Small functions, plain names, short English docstrings and comments.
- Every function comes with its tests in the same step. ≥ 80% coverage for core logic.
- Before stopping a step: `uv run pytest -q` and `uv run ruff check .` both pass.

## 11. Git

- One plan (FR) = one branch = one PR into `main`. Branch from the latest `main`: `feature/frXX-<short-name>`.
- Small jobs with no plan (typo, docs, plan status) may be committed straight to `main`
  after Hieu says OK.
- PRs always target **`main`** (PR #2 was merged into the wrong branch once).
- Commit messages: short, English, imperative (`feat: add K_bias EMA update`).
- Before any commit: `git status` — no `.env`, no secrets, no `.venv`, no `__pycache__`.
- Commit after each step Hieu approves ("tiếp"). Push and open a PR only after Hieu says OK.
- PR description in English (for teammates): what, why, new libraries, what the Backend must do.

## 12. How to work with Hieu

- **Nói chuyện bằng tiếng Việt.** Code, comments, commits and PRs in English.
- Hieu knows Java and is learning Python: explain in simple words and compare with Java when useful.
- Keep answers short. Do not create documents that were not asked for.
- Check whether something already exists before creating it.
- Say clearly when something is an assumption or not yet agreed with the team.

## 13. Workflow: build from an approved plan

Hieu designs each function on Claude web and writes `plans/FR-xx.md` (format: `plans/_TEMPLATE.md`).
**Your job: build exactly that plan, one step at a time. You do not design.**
No approved plan → do not write feature code.
Small jobs with no plan (typo, rename, docs): say what you will change, wait for OK.

**Phase A — CHECK (no code)**
1. Read the plan and the existing code it touches.
2. Summarize in ≤ 5 lines: what you will build, which files, which tests.
3. List anything unclear, missing or conflicting (plan vs. code vs. `docs/` vs. this file).
   **Do not fill gaps yourself.**
4. STOP. Wait for "BẮT ĐẦU".

**Phase B — BUILD (one step of plan §4 at a time)**
1. List the files you will create or change. Touch nothing else.
2. Write the code + the tests named for this step (plan §3).
3. Run `uv run pytest -q` and `uv run ruff check .`.
4. Explain briefly in Vietnamese: what each part does, why, and Python-vs-Java where useful.
5. STOP. On "tiếp": tick the step in plan §4, commit, then start the next step.

**Phase C — WRAP-UP**
- Check every acceptance criterion in plan §0: pass / fail, and tick the ones that pass.
- List what the Backend/team must do (for `docs/open-questions.md`).
- Ask before pushing and opening the PR.

**The only edits you may make to a plan:** ticking checkboxes in §0 and §4.

**When something goes wrong** (bug, failing test, plan doesn't work in practice):
- STOP. Do not redesign, and do not change behavior, API, DB use or tests on your own.
  Typo-level fixes inside the current step are OK.
- Write this report so Hieu can paste it into Claude web:

```
## Bug report — FR-xx, bước N
- Hiện tượng:
- Lỗi / log (rút gọn):
- Nguyên nhân nghi ngờ:
- Mục plan bị ảnh hưởng: (0–4)
- Hướng sửa: 2–3 lựa chọn, ưu/nhược mỗi cái
```

- Wait until Hieu updates the plan (§5 change log), re-read it, then continue.

## 14. Open questions

See `docs/open-questions.md`. Never assume the answer to an open question — ask Hieu.
