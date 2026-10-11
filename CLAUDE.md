# CLAUDE.md — METANOIA AI Service

Guidance for Claude (and any teammate) working in the `AI` repo.

## 1. What this repo is

METANOIA is a capstone project (FPT University HCM, 09/2026 – 03/2027): an AI assistant that helps **freelancers** manage projects and plan their work. It learns how much each user under-estimates time and corrects future schedules.

This repo is the **AI service**. It runs as a **separate service**, deployed independently from the Backend (ASP.NET Core). Both share the same Supabase database and the same login token (JWT).

- Owner: Hieu (team leader). Other members build Backend and Frontend.
- Language: **Python**. The original proposal said C# / Semantic Kernel; the change is **waiting for the supervisor's approval**. Keep core logic independent of any framework.
- **Source of truth:** the team Google Doc "Metanoia" (tabs: What, In/Out Scope, Function - Non-Functional, Model Business, Problems, Issue, Log...). It is written in Vietnamese for the team. If this file and the doc disagree, the doc wins — tell Hieu about the mismatch.
- Anything sent to the supervisor must be in **English**, in our own words.

## 2. Tech stack

- Python 3.12, package manager **uv**
- FastAPI + uvicorn (web API), Pydantic (validation, incl. LLM JSON output)
- pytest (tests), ruff (lint/format)
- Planned: SQLAlchemy + asyncpg + pgvector (Supabase Postgres)
- LLM routing (from the Model Business tab): Free plan → cheapest model; paid plans → cheap model for ~90% of calls, stronger model for ~10% "hard" calls. Two providers (OpenAI + Google); if one fails, switch to the other. Models are chosen by **config, never hard-coded**. Embedding model: not chosen yet.

## 3. Commands

```bash
uv sync                       # install everything from pyproject.toml / uv.lock
uv run pytest -q              # run tests
uv run ruff check .           # lint
uv run ruff format .          # format
uv run uvicorn metanoia_ai.api.main:app --reload   # run the API (once main.py exists)
```

Add a library with `uv add <name>` (or `uv add --dev <name>`) and commit `pyproject.toml` + `uv.lock` together.

## 4. Folder structure (layered, like Clean Architecture)

```
src/metanoia_ai/
  domain/          # pure rules and formulas. No DB, no LLM, no web.
  application/     # use cases + interfaces (LLMClient, EmbeddingClient, MemoryStore, CalendarReader ...)
  infrastructure/  # real implementations: database, LLM providers, embeddings
  api/             # FastAPI routes, JWT check, request/response schemas
tests/unit/        # fast tests, mainly for domain/
```

Dependency rule: `api → application → domain` and `infrastructure → application → domain`. **domain imports nothing from the other layers.**

## 5. Core domain rules (do not change without asking Hieu)

**Task types**
- **Flexible**: duration depends on the user (coding, writing, design). Needs deadline + T_est. The system measures and learns from these.
- **Fixed**: time set by someone else (meetings, calls). Blocked time only: never moved, **never used for learning**.

**Formulas**
- `T_proposed = T_est × K_bias + buffer` (buffer = break between two tasks, from onboarding)
- K_bias update (EMA, per user per work domain): `K_new = α × (T_act / T_est) + (1 − α) × K_old`
- `α = 0.2` by default, read from config (experiments may try other values).
- Initial K_bias = 1.3. *(Open: may be seeded from the onboarding answer "how often do you overrun" — Problems #10.)*
- K_bias is always clamped to **[0.5, 3.0]**.
- Example: T_est = 60, K = 1.3, buffer = 0 → T_proposed = 78.

**Clean learning data — skip the update when:**
- the task is Fixed;
- T_act is `UNKNOWN` (user did not answer);
- T_act came from an auto-stopped timer the user has not confirmed;
- the ratio T_act / T_est is an outlier. *(Threshold open: proposed > 5× or < 1/5 — Problems #12.)*

**Where T_act comes from** (always store the source):
- `timer` — Start/Stop in Focus mode; a task can have several sessions, T_act = sum of sessions.
- `self_reported` — quick buttons "As planned / +15' / +30' / Other" when the user forgot the timer.
- `auto_timeout` — timer ran over 3× the planned time and stopped itself.

Every K_bias update is written to `KBiasUpdateLog` (old, new, α, T_est, T_act, source) so any value can be recomputed.

**Scheduling (who does what)**
- **Code places the slots; the LLM only explains.** The LLM never decides slot times.
- Order: (1) keep Fixed tasks as they are, (2) Flexible tasks by nearest deadline first (tie: longer T_proposed first), (3) size each slot with T_proposed, (4) prefer the user's productive hours, (5) warn when a deadline cannot be met.
- Nothing is written to the calendar until the user approves.

**Task runs over its slot** (Issue tab #1): never auto-move. Extend the slot if the next gap is enough; otherwise re-run the planner for the rest of the day (do not move Fixed tasks or tasks already started, keep order) and show a proposal for the user to approve. **No LLM call and no quota used** in this flow. Partial T_act is kept and summed before the K_bias update.

**Plan rules that affect AI logic** (Model Business tab)
- Free plan: K_bias **is still learned**, but only shown as a hint ("you usually need 30% more"), not added to the schedule.
- Productive hours: all plans use the onboarding answers; only the top plan also learns from timer data.

## 6. AI features this repo owns

- FR-08 AI chat: **project level** (read brief / attached document → ask clarifying questions → draft milestones + tasks with the user's estimate and the adjusted estimate) and **task level** (split a task > 2 h into subtasks). The user edits drafts; **Apply** creates the real records.
- FR-09 Context: free/busy time, current workload, similar past tasks (vector search + filters + prefer recent).
- FR-10 Schedule proposal (planner + validator + one-line reason per slot).
- FR-12 Daily briefing "Today": 3 priorities with reasons, deadlines at risk, warnings from similar past tasks. Text only.
- FR-17 K_bias learning. FR-18 productive-hours learning. FR-19 long-term memory (embeddings in pgvector, built in the background).
- Plumbing: quota check, `AIUsageLog`.

**Deferred:** FR-13 blind test of 3 personalization levels (research part). **Out of scope:** voice assistant, Eisenhower matrix, training our own model.

## 7. Rules for LLM use

- **Quota is counted per type**: schedule runs, chat messages, document reads. Max 50 AI calls per user per day. Check quota **before** calling the LLM. When quota is used up, block AI features only; manual features keep working.
- Documents: sent to the LLM **only when the user attaches them in chat**. Extract text first and cap at **20,000 tokens**.
- LLM output must be JSON validated by a Pydantic schema **and** business rules (no overlap, before deadline, Fixed tasks untouched). If invalid: retry up to 2 times, then return a clear error.
- If the LLM provider is down, the planner and manual features still work.
- Every suggestion has a one-line, plain-language reason. No internal names (`K_bias`, `T_proposed`) in user-facing text.
- Log every LLM/embedding call: feature, model, tokens, cost, latency.
- Keep prompts in separate versioned files. Keep 10–20 sample inputs to re-run after each prompt change.
- Targets: chat first words < 3 s (streaming), schedule proposal < 10 s, similar-task search < 100 ms.
- Background work (embeddings): retry up to 3 times; a missing embedding never blocks saving a task.

## 8. Database rules

- Supabase Postgres is shared with the Backend. **The Backend team owns the schema.** Do **not** run migrations or change tables from this repo.
- Proposed (not agreed yet — Problems #9): this service writes only to `KBiasUpdateLog`, `UserDomainProfile`, `TaskExecutionMemory`, `ChatConversation`, `ChatMessage`, `ScheduleProposal`, `AIUsageLog`, and the AI usage counters. Everything else is read-only here.
- Busy time comes from the synced `GoogleCalendarEvent` table. Writing events to Google on approval is the Backend's job.
- `ScheduleChangeLog.Source` must be set correctly (`MANUAL`, `AI`, `GOOGLE_SYNC`); slots placed by this service use `AI`.
- The Supabase project is **PRODUCTION**. Experiment on a separate dev database.
- Every query is filtered by the owner's user ID.

## 9. Security and privacy

- Never commit `.env` or any key/password. Only `.env.example` (names, no values) goes to Git.
- Never send email, full name, OAuth tokens or client contact details to the LLM.
- Validate the JWT on every endpoint; this service checks tokens, it does not issue them.

## 10. Code conventions

- Type hints everywhere; Pydantic models at the edges.
- Write each domain function together with its unit test. Target ≥ 80% coverage for core logic (K_bias update, T_proposed, ordering, quota check).
- Small functions, plain names, short English docstrings.
- Do not add a dependency without a reason; mention it in the PR.

## 11. Git workflow

- Never push directly to `main`. Branch as `feature/<short-name>`, push, open a Pull Request.
- Commit messages: short, imperative (`feat: add K_bias EMA update`, `test: skip Fixed tasks`).
- Check `git status` before committing: no secrets, no `.venv`, no `__pycache__`.

## 12. Open questions (do not assume the answer — see the Problems tab)

1. Supervisor approval for Python / separate service (#8).
2. Backend ↔ AI contract: which tables AI writes (#9). *Decided: JWT is HS256, user ID in `sub` (see `plans/auth-jwt.md`); calls go Frontend → Backend → AI (the Backend calls the AI service, e.g. when a Flexible task is done).*
3. Initial K_bias from onboarding or fixed 1.3 (#10).
4. Outlier threshold for learning data (#12).
5. Document-read quota per plan (#13).
6. Which LLM models and embedding model (vector size; ERD uses 1536).
7. Database doc vs Supabase schema mismatch (#19).

## 13. How to work with Hieu

- Hieu works on a Mac and is learning Python (knows Java). Explain in simple words, give **one step at a time**, and **check whether something already exists before creating it**.
- Keep answers short. Do not create extra documents unless asked.
- Say clearly when something is an assumption or not yet agreed with the team.
- Nói bằng tiếng việt

## 14. Specs
- `docs/functional-requirements.md` — FR list (Vietnamese, team version). Read the relevant FR before working on it.
- If the code needs something the spec doesn't say, ask Hieu — don't invent it.

## 15. Workflow: one FR at a time, think together before coding

Hieu must understand everything that gets built. Never jump ahead. Each phase ends with a STOP; wait for Hieu.

1. UNDERSTAND — Restate the FR in simple Vietnamese: what goes in, what comes out, which layer/files it touches, an example with real numbers. STOP: ask "Hiểu như vậy đúng chưa?"
2. ISSUES — List every unclear point, edge case, and conflict with this file or the spec. For each: 2–3 options, pros/cons, your recommendation. Discuss ONE issue at a time. Never decide alone. STOP after each issue.
3. PLAN — Write `plans/FR-xx.md`: decisions from phase 2, then numbered steps. Each step = one small function + its unit test. STOP: code nothing until Hieu types "DUYỆT".
4. BUILD — Do ONE step. Then explain: what the code does, why, and compare unfamiliar Python with Java. Run `uv run pytest -q` and `uv run ruff check .`. STOP: wait for "tiếp".
5. WRAP-UP — Summarize what was built, tick the steps in the plan file, suggest a commit message. Do not commit or push.

If a new problem appears during BUILD: stop, go back to phase 2 for that problem only.