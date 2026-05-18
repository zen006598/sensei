# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Sensei is a Telegram bot that quizzes you on cards due in your Anki collection. AnkiWeb is the source of truth: the bot syncs from AnkiWeb at the start of each session into a local `collection.anki2` working copy, uses Google Gemini to generate questions and grade answers, then syncs review results back to AnkiWeb when the session ends. Runs headless in Docker on a self-hosted Linux server.

See `PLAN.md` for the original design doc — note that the implementation has since diverged in several places (see "Implementation vs. PLAN.md" below).

## Tooling and commands

Python 3.13+, managed with `uv`. There is no Makefile; run commands directly:

- Install deps: `uv sync --all-groups`
- Lint: `uv run ruff check src/ tests/`
- Format check / format: `uv run ruff format --check src/ tests/` / `uv run ruff format src/ tests/`
- Security audit: `uv run pip-audit`
- Tests: `uv run pytest -q`
- Single test: `uv run pytest tests/agent/test_state_machine.py::test_start_returns_question -q`
- Run the bot locally: `uv run python -m src.main` (needs `.env` populated — copy from `.env.example`)
- Build container locally: `docker build -t sensei .`
- The published image is `ghcr.io/zen006598/sensei:latest`; production uses `docker-compose.yml` which references that tag.

`pytest` is configured with `asyncio_mode = "auto"` — `async def test_…` functions run without an explicit `@pytest.mark.asyncio` marker.

`main.py` at the repo root is a leftover `uv init` stub — the real entrypoint is `src/main.py` / `python -m src.main`.

## Architecture

The bot is a four-layer pipeline. Each `/quiz` message flows: Telegram → `bot/handlers.py` → `agent/state_machine.py` → (`agent/gemini_agent.py` for LLM calls **and** `anki/client.py` for card data) → reply back to Telegram.

### Layers

- **`src/main.py`** wires every component once at startup, creates the SQLite engine for `ConversationSession` / `ErrorRecord` / `UserPrefs`, and registers all PTB handlers. Single-process, single polling loop.
- **`src/bot/`** — PTB handlers and inline/reply keyboards. Handlers gate on `ALLOWED_USER_IDS` and call into the state machine; they never touch Anki or Gemini directly.
- **`src/agent/state_machine.py`** — `QuizStateMachine` is the orchestrator. It owns the current quiz session, runs all Gemini and Anki calls, persists session/error rows, and drives the per-card question sequence.
- **`src/agent/gemini_agent.py`** — Thin wrapper around `google-genai`. Every call uses **function-calling tools** (`QUIZ_TOOL`, `JUDGE_TOOL`, `FREQ_TOOL`, `REGISTER_TOOL` in `agent/tools.py`) with `tool_config=ANY`, so the model is forced to return structured args instead of free text. To change a question/judge schema, edit `agent/tools.py`.
- **`src/anki/client.py`** opens `collection.anki2` per operation via a contextmanager (open → use → close) and serialises **all** Anki access through a class-level `asyncio.Lock` (`AnkiClient._collection_lock`). `state_machine._run_anki()` is the only correct way to call Anki methods — it takes the lock and runs the sync call in an executor.
- **`src/anki/sync.py`** — `AnkiSyncer.sync()` talks to AnkiWeb via the `anki` package's internal sync API. Full-download responses go through a **local HTTP proxy** (`_full_download_via_proxy`) that re-compresses zstd payloads with a content size header — workaround for an Anki Rust-backend bug. Don't remove this proxy without verifying full sync still works.
- **`src/db/`** — SQLModel tables (`ConversationSession`, `ErrorRecord`, `UserPrefs`) on SQLite. `UserPrefsStore` is per-user; the two session tables are global, keyed by `card_id`.

### Quiz flow (the part that's not obvious from the code)

Per Anki card, the state machine generates and grades questions in a small loop until the card is "mastered" or skipped:

1. **First touch of a card**: classify and tag it with `sensei:common|rare|obsolete` (frequency) and `sensei:formal|informal|slang|literary|neutral` (register). These tags are written back to the Anki note so future runs skip the classification call.
2. **Rare/obsolete cards**: only `fill_in_blank` is asked, one correct answer ends the session.
3. **Common cards**: must answer correctly on **two** question types — either `fill_in_blank` or `spelling`, **plus** `sentence`. `_is_mastered()` enforces this.
4. **Grading** uses `evaluate_answer` which returns one of `correct | semantic_correct | grammar_error | vocab_error | wrong`. Each outcome routes to a different next-question type via `forced_type` (see `_handle_judgment`). Spelling questions have a case-insensitive exact-match shortcut before calling Gemini.
5. **Session end**: `_end_session` answers the Anki card with ease 4 (perfect first try), 3 (perfect after retries), or 1 (skipped/stopped), generates a Gemini summary, persists it, then triggers an AnkiWeb sync. `dont_know` is the fast path — no summary, sync runs in a background task.

### Single-active-session invariant

`QuizStateMachine._active` is a single `_ActiveSession | None`, **not** a dict keyed by user. The bot supports only one quiz at a time across the whole process. `ALLOWED_USER_IDS` is the only thing preventing concurrent users from clobbering each other's session — keep it configured. (This is one of the divergences from `PLAN.md`, which described a per-user dict.)

## Configuration

All config flows through `src/config.py` → `Settings` dataclass, loaded from environment (via `python-dotenv`).

Required: `TELEGRAM_TOKEN`, `ANKIWEB_EMAIL`, `ANKIWEB_PASSWORD`, `GEMINI_API_KEY`.

Common optional: `GEMINI_MODEL` (default `gemini-2.5-flash-lite`), `ANKI_COLLECTION_PATH` (default `./data/anki/collection.anki2`), `DB_PATH` (default `./data/sensei.db`), `ALLOWED_USER_IDS` (comma-separated; empty = allow all), `LOG_LEVEL` (default `INFO` locally, `ERROR` in production via compose).

## Deployment

Push to `main` → GitHub Actions (`.github/workflows/cd.yml`) runs CI, builds and pushes to `ghcr.io/<repo>:latest`, then SSHes to the production host **over Tailscale** (port 2222) to write a fresh `.env`, `docker compose pull`, `docker compose down --remove-orphans`, `docker compose up -d`. The compose file mounts named volumes `anki_data` (collection) and `sensei_db` (preferences/session SQLite).

CI (`ci.yml`) runs on every non-`main` branch and PRs to `main`: lint, format check, `pip-audit`, pytest. The CD pipeline re-runs the same CI steps before building.

## Implementation vs. PLAN.md

`PLAN.md` is the original design. Notable drift:

- No `quiz/engine.py`, `quiz/scorer.py`, or `gemini/client.py` — those concerns are merged into `agent/state_machine.py` and `agent/gemini_agent.py`.
- `gemini_agent.py` uses the newer `google-genai` SDK with function-calling tools (not `google-generativeai` with JSON-mode prompts).
- State machine is single-active-session, not per-user dict.
- Question types are `fill_in_blank | spelling | sentence` (the PLAN's `QuizType` enum is gone; question type is just a string field).
- SQLModel/SQLite was added later for per-card learning history (`ConversationSession`, `ErrorRecord`) and per-user prefs (`UserPrefs`).

When the plan and the code disagree, the code wins. Update `PLAN.md` only if explicitly asked.
