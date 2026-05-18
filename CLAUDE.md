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

- **`src/main.py`** — composition root only. Loads settings, runs `Path(db_path).parent.mkdir`, builds the SQLite engine + `create_all`, instantiates every collaborator (clients, stores, agent, classifier, state machine, handlers), hands the configured PTB `Application` off to `run_polling`. No business logic.
- **`src/bot/handlers.py`** — PTB handler functions, closing over the deps `make_handlers` receives. Auth gating via `ALLOWED_USER_IDS` filter. Handlers go through the state machine, `AnkiClient`, `AnkiSyncer`, or `UserPrefsStore` (all passed in via DI) — they never reach into Gemini or the DB directly.
- **`src/bot/app.py`** — `build_app(token, allowed_user_ids, handlers)` builds the PTB `Application`, sets the `BotCommand` menu, installs the `group=-1` auth gate for callback queries, registers every `CommandHandler` / `CallbackQueryHandler` / `MessageHandler`.
- **`src/agent/state_machine.py`** — `QuizStateMachine`: quiz flow & mastery rules only. Tracks the current `_ActiveSession`, drives question generation / grading / next-question routing. Delegates ALL persistence to stores and ALL Anki/Gemini I/O to collaborators. No `Session(engine)`, no `asyncio.Lock`, no Anki tag operations directly.
- **`src/agent/gemini_agent.py`** — `GeminiAgent`: thin wrapper around `google-genai`. Structured output uses `response_mime_type="application/json"` + `response_schema` (see `_structured` helper) — the schemas (`QUIZ_SCHEMA`, `JUDGE_SCHEMA`, `CLASSIFY_SCHEMA`) and their matching dataclasses (`QuizResult`, `JudgeResult`, `WordClassification`) live in `agent/schemas.py`. The session-summary call is the only free-text path. (Earlier versions used function-calling tools with `tool_config=ANY`; that was equivalent to "force JSON output" without the agentic-tool semantics, so it was simplified.)
- **`src/word_service/word_classifier.py`** — `WordClassifier`: pure classification, no I/O writes, no mutation of inputs.
  - `frequency(word) -> str`: `wordfreq.zipf_frequency` → `_bucket` returns common/rare/obsolete. Sub-ms, no LLM, sync. `zipf_fn` is constructor-injectable for tests.
  - `register(card) -> str`: reads existing `sensei:<register>` tag from `card.tags` if cached; on miss, calls `agent.classify_register`. Does NOT write the cache back — `QuizStateMachine._classify` does that, using the public `TAG_PREFIX` constant exposed from this module.
- **`src/anki/client.py`** — `AnkiClient`: async public API. Each method opens `collection.anki2` per operation (contextmanager: open → use → close), takes the shared lock and dispatches the blocking call to an executor inside `_run_locked`. Callers just `await anki.get_due_count()` — they don't see the lock or thread.
- **`src/anki/sync.py`** — `AnkiSyncer`: talks to AnkiWeb via the `anki` package's internal sync API. `async_sync()` also takes the shared lock (closes the race between a background `_summarize_and_sync` and a foreground `/quiz`). Full-download responses go through a **local HTTP proxy** (`_full_download_via_proxy`) that re-compresses zstd payloads with a content size header — workaround for an Anki Rust-backend bug. Don't remove this proxy without verifying full sync still works.
- **`src/anki/_lock.py`** — single shared `collection_lock = asyncio.Lock()`. Both `AnkiClient` and `AnkiSyncer` import it so every `Collection(path)` open serialises globally.
- **`src/db/`** — one file per class, file name = class name (snake_case). SQLModel tables: `conversation_session.py`, `error_record.py`, `user_prefs.py`. Stores: `conversation_session_store.py`, `error_record_store.py`, `user_prefs_store.py`. Every store's `__init__` takes a single `Engine`. The state machine never touches `Session(engine)` directly.
- **`src/anki/card_data.py`** — `CardData` `@dataclass` DTO returned by `AnkiClient`. In-memory transfer only, never persisted.

### Quiz flow (the part that's not obvious from the code)

Per Anki card, the state machine generates and grades questions in a small loop until the card is "mastered" or skipped:

1. **First touch of a card**: classify frequency via `wordfreq` (local, sub-ms) and ask Gemini to classify register, caching the register as `sensei:formal|informal|slang|literary|neutral` on the Anki note. Frequency is recomputed every call (free) — older `sensei:common|rare|obsolete` tags on production cards are dead data but harmless.
2. **Rare/obsolete cards**: only `fill_in_blank` is asked, one correct answer ends the session.
3. **Common cards**: must answer correctly on **two** question types — either `fill_in_blank` or `spelling`, **plus** `sentence`. `_is_mastered()` enforces this.
4. **Grading** uses `evaluate_answer` which returns one of `correct | semantic_correct | grammar_error | vocab_error | wrong`. Each outcome routes to a different next-question type via `forced_type` (see `_handle_judgment`). Spelling questions have a case-insensitive exact-match shortcut before calling Gemini.
5. **Session end**: `_end_session` answers the Anki card with ease 4 (perfect first try), 3 (perfect after retries), or 1 (skipped/stopped), generates a Gemini summary, persists it, then triggers an AnkiWeb sync. `dont_know` is the fast path — no summary, sync runs in a background task.

### Single-active-session invariant

`QuizStateMachine._active` is a single `_ActiveSession | None`, **not** a dict keyed by user. The bot supports only one quiz at a time across the whole process. `ALLOWED_USER_IDS` is the only thing preventing concurrent users from clobbering each other's session — keep it configured. (This is one of the divergences from `PLAN.md`, which described a per-user dict.)

In any path that ends a session (`skip`, `discard_current`, `_end_session`), clear `self._active = None` **before** any `await` — keep a local ref and operate on that. Otherwise a second handler can land on the event loop during the async work and observe the half-torn-down session as "still active".

## Conventions

The following rules govern where things live. Apply them when adding code and when reviewing changes.

### One file per class, file name = class name

No `models.py` grab-bags. `ConversationSession` lives in `conversation_session.py`, `UserPrefsStore` in `user_prefs_store.py`. snake_case file, PascalCase class.

### No pure pass-through wrappers

If a method body is just `return self._x.method(...)` with no added behaviour, **delete it** and have the caller hold the direct dependency. Wrapping is justified only when it adds: locking, executor dispatch, default-on-miss, upsert, error translation, or unifies inconsistent APIs. Pure forwarding is dead weight that grows the surface and obscures the real seams.

Examples we removed during the refactor: `QuizStateMachine.set_deck/get_deck/set_mode/get_mode` (handlers now take `UserPrefsStore` directly), `get_due_count/get_deck_names` (handlers now take `AnkiClient` directly), `get_due_count_sync` (was lock-bypass dead code).

### When to extract a `*Store`

Extract a store class when **any** of these holds:

- `Session(engine)` boilerplate is repeated across multiple call sites,
- the access pattern needs default-on-miss / upsert / "get or create",
- caller orchestration is complex enough that inline ORM details obscure the main logic.

Do NOT extract for a single caller doing one or two ad-hoc queries — that's premature abstraction. The rule isn't "if it's ORM then store"; it's "if the boilerplate or upsert logic is hurting the caller, store".

### All Anki access goes through `AnkiClient` async methods

Never construct `Collection(path)` directly outside `AnkiClient` (or `AnkiSyncer`, which is intentionally special). Never bypass `collection_lock`. If you find yourself wanting sync Anki access, you're writing code in the wrong layer — push the call up to a handler/state-machine method that can `await`.

### In-memory vs persistent are separate types

`@dataclass` for transfer objects (`CardData`, `_ActiveSession`, `SubmitResult`). `SQLModel(table=True)` for persisted rows. Never mix the two responsibilities in one class. `_ActiveSession` is in-memory; `ConversationSession` is its persistent counterpart — both can exist for the same quiz.

### Dependency injection via constructor, no module-level globals

`main.py` is the composition root — it builds every collaborator once and wires them in. Other modules accept their deps in `__init__` / function args. The only module-level singleton is `src/anki/_lock.py::collection_lock`, because the lock semantics genuinely need to be process-global.

## Configuration

All config flows through `src/config.py` → `Settings` dataclass, loaded from environment (via `python-dotenv`).

Required: `TELEGRAM_TOKEN`, `ANKIWEB_EMAIL`, `ANKIWEB_PASSWORD`, `GEMINI_API_KEY`.

Common optional: `GEMINI_MODEL` (default `gemini-2.5-flash-lite`; used for quiz/judge/summary), `GEMINI_CLASSIFY_MODEL` (default `gemini-3-flash-lite`; used only for register classification), `ANKI_COLLECTION_PATH` (default `./data/anki/collection.anki2`), `DB_PATH` (default `./data/sensei.db`), `ALLOWED_USER_IDS` (comma-separated; empty = allow all), `LOG_LEVEL` (default `INFO` locally, `ERROR` in production via compose).

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
