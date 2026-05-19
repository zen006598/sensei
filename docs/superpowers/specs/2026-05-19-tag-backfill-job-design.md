# Tag Backfill Job — Design

**Date:** 2026-05-19
**Status:** Draft (awaiting user review)
**Branch:** `feat/word-service-and-hints`

## Problem

Today the bot classifies each due card lazily at quiz time. `QuizStateMachine._begin_card` runs three classifications:

- `frequency` — local `wordfreq` lookup, sub-ms, free
- `register` — Gemini LLM call (only on cache miss), the only expensive axis
- `is_academic` — local AWL set lookup, sub-ms, free

Cache lives in the Anki note as `sensei:<value>` tags. After the first quiz on a card, subsequent quizzes are 100% cache hits — no LLM call. The first quiz on an untagged card pays a few seconds of LLM latency before the question appears.

The user wants to **pre-warm the cache for the whole collection** on a daily schedule plus an on-demand Telegram command, so quiz time is always a cache hit.

## Goals

1. Idempotent batch job that scans every card in the local collection and adds any missing `sensei:*` tag (frequency / register / academic).
2. **Separation of concerns**: all tag read/write logic moves out of `QuizStateMachine` into a new `CardTagger` service. State machine reverts to "quiz flow & mastery rules only" per CLAUDE.md.
3. **Local-pass and LLM-pass are two independent batch operations.** Frequency (`wordfreq`) and academic (AWL set lookup) are both sub-ms local lookups; register is a Gemini call. They are exposed as two separate public methods on `CardTagger` and triggered sequentially by the caller (daily-job / `/retag`) with an `async_sync()` between them. A failure or interruption of the register batch must not roll back the local batch.
4. Daily schedule, configurable hour, runs inside the bot process (no extra container, no host cron).
5. Manual `/retag` Telegram command sharing the same code path.
6. **Chat must stay responsive while a batch runs.** Quantified below in the Concurrency section, not just promised.
7. The batch must not collide with itself: only one batch (daily or manual) at a time.

## Non-goals

- Re-classifying cards that already have a tag (the user explicitly chose "fill-only, no overwrite" — matches existing quiz-time behavior).
- Progress streaming. Only "started" and "done" messages on `/retag`.
- New tag types. The job classifies the same three axes the quiz flow uses today.
- Multi-user / per-user scheduling. The bot is single-user.

## Architecture

### New: `src/word_service/card_tagger.py` — `CardTagger`

Extracts the cache-check / persist-tag plumbing currently embedded as private helpers in `QuizStateMachine` (`_classify_frequency`, `_classify_register`, `_classify_academic`, `_cached`, `_persist_tag`, lines 363–408). After this refactor, `QuizStateMachine` has zero knowledge of `sensei:*` tag names, the `TAG_PREFIX` constant, or the frequency/register/academic vocabularies — those move into the tagger module.

Two classification axes, sharply separated by cost:

- **Local axes** (sync, sub-ms, free): frequency via `wordfreq.zipf_frequency`, academic via AWL set membership.
- **LLM axis** (async, ~1 s, costs tokens): register via Gemini.

Public API:

```python
class CardTagger:
    def __init__(self, anki: AnkiClient, classifier: WordClassifier): ...

    # --- Quiz hot path ---
    async def classify(self, card: CardData) -> tuple[str, str | None]:
        """Returns (frequency, register). Applies all 3 axes if missing.
        Identical behavior to today's QuizStateMachine._classify_* trio."""

    # --- Per-card helpers, also used internally by the batch methods ---
    async def classify_local(self, card: CardData) -> LocalDelta:
        """Frequency + academic only. No LLM. Writes any missing local tags."""

    async def classify_register(self, card: CardData) -> RegisterDelta:
        """Register only. Reads cache; on miss calls Gemini; on LLM failure
        returns failed=True without writing."""

    # --- Two independent batch entry points ---
    async def classify_local_all(self) -> LocalBatchStats:
        """Iterates every card id in the collection, calls classify_local
        per card, accumulates a LocalBatchStats. Per-card try/except so a
        single failure doesn't abort. Holds _batch_lock; raises
        BatchAlreadyRunningError on re-entry."""

    async def classify_register_all(self) -> RegisterBatchStats:
        """Iterates every card id in the collection, calls classify_register
        per card. Same lock + error-handling pattern."""

    @property
    def is_running(self) -> bool: ...   # _batch_lock.locked()
```

Two independent methods, **not** a combined `classify_all()`. The caller (daily-job or `/retag` handler) orchestrates the sequence `classify_local_all → sync → classify_register_all → sync`. Reasons:

- The sync between them gets local tags up to AnkiWeb fast, even if the register batch later fails or is interrupted.
- Each method can be invoked alone — e.g. tests, future cron variants, or a manual one-shot.
- Sequencing is visible at the call site rather than hidden inside the tagger.

Both methods share **one** `_batch_lock`: only one batch (local OR register OR /retag in progress) runs at a time. They are "independent" in the sense of being separately invocable, not concurrent.

Stats dataclasses (no elapsed timings):

```python
@dataclass
class LocalBatchStats:
    cards_scanned: int
    frequency_added: int
    academic_added: int
    write_failures: int

@dataclass
class RegisterBatchStats:
    cards_scanned: int
    register_added: int
    register_failures: int   # LLM call failed
    write_failures: int      # Anki write blew up
```

Per-card try/except wraps each call inside the batch loops; a single card failure logs at WARNING and moves on. `BatchAlreadyRunningError` lives at module top level (public, no `_` prefix) so `bot/handlers.py` can `except` it.

### Modified: `src/agent/state_machine.py`

- Remove `_classify_frequency`, `_classify_register`, `_classify_academic`, `_cached`, `_persist_tag` (lines 363–408).
- Remove the `FREQUENCIES / REGISTERS / ACADEMICS / TAG_PREFIX` imports from `word_classifier` — they move into `CardTagger`'s implementation.
- Constructor takes a new `tagger: CardTagger` dep, drops the direct `classifier` dep (the tagger holds it).
- `_begin_card` / `_next_question` collapse three calls into one:

  ```python
  frequency, register = await self._tagger.classify(card)
  ```

  (`is_academic` is no longer passed to `_agent.generate_question` today either; tagger writes the tag silently.)

### Modified: `src/anki/client.py`

Add `get_all_card_ids() -> list[int]` — returns the id list for every card in the collection via `col.find_cards("")`. One lock acquire. Cheap; the per-card `update_note` still happens through existing `update_card_tags` and takes the lock per write so quiz handlers can interleave.

(We do **not** open the collection once and stream — keeping the per-card open/close pattern means `collection_lock` is released between cards, so quiz handlers are never blocked for more than a single `update_note` write. Batch wall-time is dominated by the register LLM call, not by collection opens.)

### Modified: `src/anki/sync.py`

Add `try_sync(label: str = "") -> bool` method on `AnkiSyncer` — wraps `async_sync()` in a try/except, logs failures, returns `True`/`False`. The existing `async_sync()` is unchanged (still raises on failure, so quiz-flow callers in `_end_session` keep their original behavior).

```python
class AnkiSyncer:
    async def try_sync(self, label: str = "") -> bool:
        try:
            await self.async_sync()
            return True
        except Exception:
            logging.exception("sync failed %s", label)
            return False
```

Used by the daily job and `/retag` handler — both want "sync if you can, log if you can't, continue either way".

### Modified: `src/bot/handlers.py`

Add `retag_handler` (sketch):

```python
async def retag_handler(update, context):
    if tagger.is_running:
        await update.message.reply_text("A retag job is already running — try again later.")
        return
    await update.message.reply_text("Retag started, will notify when done.")
    asyncio.create_task(_run_retag(update.effective_chat.id, context.bot))

async def _run_retag(chat_id, bot):
    try:
        local = await tagger.classify_local_all()
        sync1_ok = await anki_syncer.try_sync("after local pass")
        register = await tagger.classify_register_all()
        sync2_ok = await anki_syncer.try_sync("after register pass")
        await bot.send_message(
            chat_id, _format_stats(local, register, sync1_ok, sync2_ok)
        )
    except BatchAlreadyRunningError:
        # Backstop in case is_running raced with another caller. Single-threaded
        # asyncio means this is currently impossible, but cheap to guard.
        await bot.send_message(chat_id, "A retag job is already running — try again later.")
    except Exception as e:
        # Unexpected exception inside a batch method — programming bug, not transient.
        logging.exception("retag failed")
        await bot.send_message(chat_id, f"Retag failed: {e}")
```

`CardTagger.is_running` is `return self._batch_lock.locked()` — read-only check, doesn't acquire.

`_format_stats(local, register, sync1_ok, sync2_ok)` produces a single message, e.g.:
`Done. Local: frequency={X} academic={Z}. Register: added={Y} llm_failures={R}. Write failures: {W}. Sync: local=OK register=FAILED`
The sync line is only included if at least one sync failed; on the all-OK path it's omitted to keep the message tight.

`make_handlers` signature gains a `tagger: CardTagger` parameter; the existing `anki_syncer` parameter is reused for the two `try_sync()` calls (defined in the `src/anki/sync.py` section above).

### Modified: `src/bot/app.py`

Register `CommandHandler("retag", retag_handler)`. Add `BotCommand("retag", "Backfill missing sensei:* tags on all cards")` to the menu.

### Modified: `src/main.py`

Composition wiring:

```python
tagger = CardTagger(anki_client, classifier)
state_machine = QuizStateMachine(..., tagger, ...)   # drops `classifier` arg
handlers = make_handlers(state_machine, anki_syncer, anki_client, prefs_store, tagger)
app = build_app(...)

# Daily job: local pass → sync → register pass → sync. Sync failures are logged
# and skipped, not raised, so a transient network blip doesn't abort the register batch.
async def _daily_retag(context):
    try:
        local = await tagger.classify_local_all()
        await anki_syncer.try_sync("after local pass")
        register = await tagger.classify_register_all()
        await anki_syncer.try_sync("after register pass")
        logging.info("daily retag done: local=%s register=%s", local, register)
    except BatchAlreadyRunningError:
        logging.warning("daily retag skipped: another batch already running")

app.job_queue.run_daily(
    _daily_retag,
    time=time(hour=settings.scheduler_daily_hour),
    name="daily_retag",
)
```

`scheduler_daily_hour` default in `.env.example` changes from `8` to `3` (avoids morning quiz collision). Existing prod deployments must set `SCHEDULER_DAILY_HOUR=3` explicitly or accept the new default.

## Concurrency & locking

### Why a running batch does not freeze the chat

The bot runs on a single asyncio event loop. The batch must yield back to the loop frequently so Telegram updates keep being processed. Three properties guarantee this:

1. **Every per-card op contains `await` points.** `classify_local` does `await anki.update_card_tags(...)` which is `async with collection_lock: await loop.run_in_executor(...)` — two yield points per write. `classify_register` additionally `await`s `agent.classify_register(card)` (network) before the write. The event loop yields on every card.

2. **`collection_lock` is released between cards, not held for the whole batch.** Each card's tag write acquires, opens the collection, mutates, closes, releases. A concurrent quiz handler waits at most one card's write before its turn — quiz operations never queue behind the whole batch.

3. **Both triggers return to PTB instantly.** `/retag` replies "Retag started" then does `asyncio.create_task(_run_retag(...))` — handler returns immediately. The daily job uses `app.job_queue.run_daily(callback)` where `callback` is an async function whose own `await`s free the loop.

What we explicitly do **not** do:

- We do not run the batch in a thread or process. It's await-bound (LLM, I/O), not CPU-bound — threading would just add coordination cost.
- We do not insert `asyncio.sleep(0)` between cards as a "yield hint" — the natural `await`s already yield.
- We do not pause the register batch if a quiz becomes active. Fairness via `collection_lock` is enough; pausing would add state machinery for negligible gain.

### Two layers of locks

1. **`collection_lock`** (existing, module-global in `src/anki/_lock.py`) — per-Anki-operation lock. Batch and quiz handlers share it fairly; described above.
2. **`CardTagger._batch_lock`** (new, instance-level `asyncio.Lock`) — held for the duration of one `classify_local_all()` OR `classify_register_all()` call. Prevents any two batches from overlapping (daily-job's two passes, `/retag`'s two passes, or `/retag` arriving while daily-job is mid-flight all serialize via this lock). Each method checks `lock.locked()` and raises `BatchAlreadyRunningError` (public, exported from `card_tagger.py`) if held, otherwise enters `async with lock:`. Safe without TOCTOU because asyncio is single-threaded and there's no `await` between the check and the `async with`.

The per-card `classify()` path (quiz hot path) **does not** touch `_batch_lock`. Quiz flow keeps tagging individual cards normally even while a batch is mid-flight; the only shared coordination is `collection_lock`.

## Sync strategy

Daily-job and `/retag` follow the same sequence:

```
classify_local_all()  →  async_sync()  →  classify_register_all()  →  async_sync()
```

Two syncs, not one. The first sync pushes local tags to AnkiWeb the moment the cheap pass is done, so even if the register batch later fails, the user's collection on AnkiWeb already has frequency + academic tags. The second sync pushes register tags after the slow pass.

No pre-batch sync. Daily job runs at 03:00, well after the previous evening's quiz-end sync. `/retag` is on-demand; if the user wants a pre-sync, they can `/quiz` then `/stop` first.

**Quiz-time `classify()`:** unchanged. Tags written during a quiz are pushed by the existing `_end_session` sync.

## Error handling

Rule: **a single failure never aborts the rest**. Tags are local and idempotent, so a missed card or missed sync just gets retried on the next run.

- **`classify_local_all` per-card write failure** (Anki I/O exception): caught per card, logged at WARNING, `write_failures` incremented, batch continues to the next card.
- **`classify_register_all` per-card LLM failure:** caught inside `classify_register()`; returns `failed=True` without writing; `register_failures` incremented; batch continues.
- **`classify_register_all` per-card write failure:** same as local. `write_failures` is its own field on `RegisterBatchStats`.
- **AnkiWeb sync failure** (either `async_sync()`): caught at the call site, logged at ERROR, and the trigger (daily-job / `/retag`) records a sync-error flag and **continues** to the next step. Rationale: a network blip on AnkiWeb has no bearing on Gemini, so we still run the register batch; the missed tags are local and will be pushed by the next session-end sync anyway. The end-of-run message tells the user which syncs failed so they can decide whether to re-run.
- **Batch already running:** the first `classify_*_all()` call raises `BatchAlreadyRunningError` immediately. `/retag` catches it and replies "A retag job is already running — try again later." Daily job catches it, logs WARNING, and skips (next run 24h later).
- **Unexpected exception in a batch method itself** (not per-card): propagates up. `/retag` reports `Retag failed: <reason>`. Daily job logs ERROR. This is the only path that genuinely halts the run — a programming bug, not a transient failure.

## Edge cases

- **Empty collection:** `get_all_card_ids` returns `[]`; both `classify_local_all` and `classify_register_all` return zero-valued stats immediately.
- **Card has front but no back / both empty:** filtered out by `_card_to_data` returning a `CardData` we then skip via the same `if not (card.front or card.back)` rule used in `get_due_cards`. Decision: skip silently (matches quiz flow).
- **User runs `/retag` during quiz:** allowed. The active quiz card's tag writes go through `collection_lock`; the batch interleaves. No special handling.
- **Daily job fires while quiz is active:** allowed for the same reason.
- **Card has both old `common/rare/obsolete` and new register/academic tags:** untouched. The cache-check rule is per-axis — frequency is already cached, so no overwrite.
- **Process restart mid-batch:** no state is persisted; the next daily run / `/retag` resumes from scratch and skips already-tagged cards (idempotent).

## Testing

New tests:

- `tests/word_service/test_card_tagger.py` — unit tests with fake `AnkiClient` + fake `WordClassifier`:
  - `classify(card)` writes missing tags, skips present tags, handles register=None.
  - `classify_local(card)` writes frequency + academic, **does not** invoke the agent (asserted by spying on the fake agent).
  - `classify_register(card)` writes register, **does not** touch the local `zipf_fn` / AWL stubs.
  - `classify_local_all()` iterates all card ids, accumulates `LocalBatchStats`, swallows per-card exceptions, never calls the agent.
  - `classify_register_all()` iterates all card ids, accumulates `RegisterBatchStats`, swallows per-card LLM and write failures.
  - Both `classify_*_all()` methods share the same `_batch_lock`: running `classify_local_all()` and then immediately starting `classify_register_all()` (without awaiting the first) raises `BatchAlreadyRunningError`.
- `tests/bot/test_retag_handler.py` — handler dispatches to tagger, replies start + end messages, replies "already running" on `BatchAlreadyRunningError`, and when `anki_syncer.try_sync()` returns `False` the handler still calls `classify_register_all()` and the final message includes the sync-failed indicator.
- `tests/anki/test_sync.py` — `AnkiSyncer.try_sync()` returns `True` on success and `False` on any underlying exception, logging it; never re-raises.
- Existing `tests/agent/test_state_machine.py` — update to inject a fake `CardTagger` instead of a fake classifier; verify `_begin_card` calls `tagger.classify(card)` exactly once and uses its return values.

Manual smoke test:

- Set `SCHEDULER_DAILY_HOUR` to the current hour+1 in `.env`, run locally, wait, confirm log line "daily retag done: ...".
- Run `/retag` in Telegram, confirm start + end messages, check that previously-untagged cards now have `sensei:*` tags via Anki desktop or `sqlite3 collection.anki2`.

## Migration / deployment

- `pyproject.toml`: no new deps (`python-telegram-bot[job-queue]` already installed; brings APScheduler transitively).
- `.env.example`: the existing line is `# SCHEDULER_DAILY_HOUR=8` (commented out at line 22). Change to `# SCHEDULER_DAILY_HOUR=3` and update the surrounding comment to "hour-of-day (0–23) for the daily tag-backfill job". Code default in `src/config.py:54` changes from `"8"` to `"3"`.
- Production `.env`: set `SCHEDULER_DAILY_HOUR=3` (or leave to inherit the new default after deploy).
- First daily run after deploy will be expensive (every previously-untagged card needs one register LLM call). All runs afterwards are mostly cache hits unless new cards have been added.

## Open questions

None — all clarifying questions resolved in brainstorming (2026-05-19 session). Approved decisions:

- **Schedule:** 03:00 daily
- **Progress reporting:** start + end only; end message lists added-tag counts, no elapsed timings
- **Already-tagged cards:** fill-only, no overwrite (no `--force` flag in v1)
- **Separation:** all tag logic moves out of `QuizStateMachine` into `CardTagger`; state machine becomes tag-agnostic
- **Local vs LLM split:** two **independent** batch methods — `classify_local_all()` (frequency + academic) and `classify_register_all()` (register, LLM). Callers run them sequentially with a sync between: `local → sync → register → sync`
- **Background isolation:** batch yields to event loop at every `await`; `collection_lock` released per card so quiz handlers never wait more than one Anki write
- **Error tolerance:** no single failure aborts the batch — per-card errors increment counters and continue; sync failures are caught (`AnkiSyncer.try_sync`) and reported but don't stop subsequent passes
