# TTS Pronunciation (Piper) — Design

**Date:** 2026-05-19
**Status:** Draft (awaiting user review)
**Branch:** new `feat/tts-pronunciation` (separate from `feat/tag-backfill`)

## Problem

Anki cards in the user's English deck don't have audio. Hearing the word
pronounced is critical for language learning — both for native-listener
calibration and for the user's own speaking practice. Buying TTS API
credits or recording manually is expensive in money or time. A local
neural TTS (Piper) can generate competent audio offline.

Concretely: for every card in the **user-selected deck**, generate an
MP3 of `card.front` via Piper, store it in `collection.media/`, and
append a `[sound:filename.mp3]` reference to the card's `sound` field
so Anki plays it on review. Sync the audio up to AnkiWeb so the mobile
client gets it too.

## Goals

1. **Pre-generated audio:** every selected-deck card with an empty `sound`
   field gets a Piper-generated MP3 written to that field on a daily
   background pass — quiz-time is silent on the TTS path.
2. **User-controlled deck scope:** both the new TTS job and the existing
   tag-backfill respect the `/decks` selection stored in `UserPrefsStore`.
   No selection → skip both jobs with a logged warning.
3. **Idempotency:** if `sound` is non-empty (user-recorded or previously
   generated), skip. Re-runs cost nothing for already-tagged cards.
4. **AnkiWeb propagation:** media sync turned on so the mobile client
   receives the audio without extra steps.
5. **Manual trigger:** `/tts` Telegram command shares the same code path
   as the daily job.
6. **Independent of tag-backfill:** TTS runs at 04:00, tag-backfill at
   03:00. Separate locks. They don't block each other.

## Non-goals

- Multiple voices / multi-language. EN-only for v1 (en_US-libritts-high).
- Voice playback inside the Telegram conversation. Audio lives in Anki only.
- Re-generation / `--force` mode. Out-of-scope; can be added later.
- Audio for non-EN decks. The deck-scoping enforces "user explicitly
  picked an EN deck".

## Architecture

### New module: `src/tts/generator.py` — `TTSGenerator`

Owns Piper invocation and per-card audio I/O. Mirrors `CardTagger`'s shape.

```python
class TTSGenerator:
    def __init__(
        self,
        anki: AnkiClient,
        piper_model_path: str,       # /data/piper/en_US-libritts-high.onnx
        media_dir: str,              # /data/anki/collection.media
        voice_name: str = "en_US-libritts-high",
    ): ...

    @property
    def is_running(self) -> bool: ...

    async def generate(self, card: CardData) -> TtsResult:
        """Per-card. Idempotent: skip if card.sound is non-empty.
        Otherwise synthesise WAV→MP3, write to media_dir/sensei_<card_id>.mp3,
        and set `sound` field to '[sound:sensei_<card_id>.mp3]'."""

    async def generate_all(self, deck: str) -> TtsBatchStats:
        """Batch over all cards in `deck`. Raises BatchAlreadyRunningError
        if a batch is already in flight (separate lock from CardTagger)."""
```

Dataclasses:

```python
@dataclass
class TtsResult:
    generated: bool       # MP3 was newly written
    skipped: bool         # sound field already non-empty
    failed: bool          # piper / media / field write failed

@dataclass
class TtsBatchStats:
    cards_scanned: int = 0
    generated: int = 0
    skipped: int = 0
    tts_failures: int = 0   # piper synth failed
    write_failures: int = 0 # media or field write failed
```

Throttling: **none**. Piper is local CPU, no rate limit. The natural
`await` between each card is enough to let quiz handlers interleave.

### New module: `src/tts/backfill.py` — orchestration

Mirrors `src/word_service/backfill.py`. Thin pipeline so trigger sites
(daily job + `/tts` handler) stay declarative:

```python
@dataclass
class TtsBackfillResult:
    stats: TtsBatchStats
    sync_ok: bool

async def run_tts_backfill(
    generator: TTSGenerator, syncer: AnkiSyncer, deck: str
) -> TtsBackfillResult:
    stats = await generator.generate_all(deck)
    sync_ok = await syncer.try_sync("after tts")
    return TtsBackfillResult(stats=stats, sync_ok=sync_ok)
```

Single sync after the batch (vs two for tag-backfill, which has two
distinct passes). Failure mode same as tag-backfill: sync failure is
captured as a flag, not raised.

### Modified: `src/anki/client.py`

Three additions:

- `get_all_card_ids(deck: str | None = None) -> list[int]` — currently
  `find_cards("")`. Add deck filter: `find_cards(f'deck:"{deck}"' if deck else "")`.
- `get_card_field(card_id: int, field_name: str) -> str` — read a named
  field from the card's note. Returns `""` if the field doesn't exist on
  the note's model.
- `set_card_field(card_id: int, field_name: str, value: str) -> None` —
  write a named field. Raises `KeyError` if the field doesn't exist on
  the note's model (signal that the deck/note-type isn't compatible).

Field names are resolved via `note.note_type()["flds"]` which maps name
to index. Anki's API uses indices internally; we layer name→index here.

### Modified: `src/anki/sync.py`

One-line change: `col.sync_collection(auth, sync_media=True)` (was
`False`). Media files in `collection.media/` now sync to AnkiWeb on
every `async_sync()` call. First sync after enabling will upload every
existing media file in the collection — single user, single time,
acceptable.

### Modified: `src/word_service/card_tagger.py`

Retrofit deck-scoping for the existing tag batches. Both
`classify_local_all` and `classify_register_all` gain a `deck: str | None = None`
parameter. Internally passed to `self._anki.get_all_card_ids(deck=deck)`.

`None` means "all cards" — preserves current behavior for any caller
that doesn't pass a deck. The daily-job caller (and `/retag` handler)
will start passing the user's selected deck after this change.

### Modified: `src/word_service/backfill.py`

`run_full_backfill(tagger, syncer, deck: str | None = None)` threads
`deck` through to both batch calls.

### Modified: `src/bot/app.py:register_jobs`

Signature grows:

```python
def register_jobs(
    app: Application,
    *,
    tagger: CardTagger,
    syncer: AnkiSyncer,
    generator: TTSGenerator,
    prefs_store: UserPrefsStore,
    allowed_user_ids: set[int],
    retag_hour: int,
    tts_hour: int,
) -> None:
    ...
```

Two callbacks registered:

```python
async def _daily_retag(context):
    deck = _resolve_daily_deck(prefs_store, allowed_user_ids)
    if deck is None:
        logging.warning("daily retag skipped: no deck selected")
        return
    try:
        result = await run_full_backfill(tagger, syncer, deck=deck)
        logging.info("daily retag done: %s", result)
    except BatchAlreadyRunningError:
        logging.warning("daily retag skipped: another batch already running")

async def _daily_tts(context):
    deck = _resolve_daily_deck(prefs_store, allowed_user_ids)
    if deck is None:
        logging.warning("daily tts skipped: no deck selected")
        return
    try:
        result = await run_tts_backfill(generator, syncer, deck=deck)
        logging.info("daily tts done: %s", result)
    except BatchAlreadyRunningError:
        logging.warning("daily tts skipped: another batch already running")

def _resolve_daily_deck(prefs, allowed):
    """Single-user bot: pull deck from the first allowed user. Returns
    None if no allowed users or no selection."""
    if not allowed:
        return None
    user_id = next(iter(allowed))
    return prefs.get_deck(user_id)
```

Both jobs registered with `time(hour=..., tzinfo=_LOCAL_TZ)`.

### Modified: `src/bot/handlers.py`

- `make_handlers` gains `generator: TTSGenerator` parameter.
- New `/tts` handler mirrors `/retag`:
  - Reads `prefs.get_deck(update.effective_user.id)`; rejects with
    "Pick a deck first via /decks" if `None`.
  - Rejects if `generator.is_running` with "TTS already running".
  - Otherwise replies "TTS started" and `asyncio.create_task(_run_tts(...))`.
- Existing `/retag` handler: also reads user's deck via prefs and passes
  to `run_full_backfill(..., deck=deck)`. Rejects with the same message
  if no deck selected.

`_HELP_TEXT` gains a `/tts` line.

### Modified: `src/bot/app.py:_BOT_COMMANDS`

Add `BotCommand("tts", "Generate pronunciation audio for selected deck")`.

### Modified: `src/main.py`

Wire `TTSGenerator` singleton; thread new args into `register_jobs`:

```python
generator = TTSGenerator(
    anki_client,
    piper_model_path=settings.piper_voice_path,
    media_dir=settings.anki_media_path,
    voice_name=settings.piper_voice,
)
handlers = make_handlers(state_machine, anki_syncer, anki_client, prefs_store, tagger, generator)
app = build_app(...)
register_jobs(
    app,
    tagger=tagger,
    syncer=anki_syncer,
    generator=generator,
    prefs_store=prefs_store,
    allowed_user_ids=settings.allowed_user_ids,
    retag_hour=settings.scheduler_daily_hour,
    tts_hour=settings.tts_daily_hour,
)
```

### Modified: `src/config.py`

New settings:

- `tts_daily_hour: int` from `TTS_DAILY_HOUR` (default `4`, range 0–23)
- `piper_voice_path: str` from `PIPER_VOICE_PATH` (default `/data/piper/en_US-libritts-high.onnx`)
- `piper_voice: str` from `PIPER_VOICE` (default `"en_US-libritts-high"`)
- `anki_media_path: str` from `ANKI_MEDIA_PATH` (default
  `/data/anki/collection.media`, derived parent of `anki_collection_path`)

### Modified: `Dockerfile` + `docker-compose.yml`

- `pyproject.toml`: `dependencies` add `piper-tts`, `lameenc` (or
  `pydub`, decision in implementation plan based on which is lighter).
- `docker-compose.yml`:
  - new env vars `TTS_DAILY_HOUR=4`, `PIPER_VOICE_PATH=/data/piper/en_US-libritts-high.onnx`
  - new volume mount `piper_models:/data/piper:ro` for the voice ONNX
- `Dockerfile` stays slim (no apt changes — `piper-tts` is pure-Python +
  onnxruntime via PyPI).

**Voice model deployment:** the `.onnx` and `.onnx.json` files (~115MB
combined for libritts-high) are NOT bundled in the image. User downloads
once to the `piper_models` named volume via a one-shot helper command
(documented in deploy notes). Image stays small; voice swappable later
by replacing the volume contents.

## Concurrency & locking

Two independent `asyncio.Lock`s, both at the singleton level:

- `CardTagger._batch_lock` — guards tag-backfill batches (`classify_local_all`,
  `classify_register_all`). Already exists.
- `TTSGenerator._tts_lock` — new, guards TTS batches.

The two locks are deliberately independent. Tag-backfill at 03:00 and TTS
at 04:00 don't overlap by schedule, but they CAN both run concurrently if
03:00 retag runs long (>1 hour) into 04:00 TTS slot. Both still serialize
at the lower `collection_lock` (per-card Anki I/O), so the Anki collection
is never accessed concurrently — just two batches making interleaved
single-card writes.

Quiz handlers continue to use `tagger.classify(card)` per-card (no batch
lock involvement) and are not blocked by either batch beyond per-card
`collection_lock` contention.

## Idempotency

The per-card check on the `sound` field is the only idempotency mechanism:

```python
if (await self._anki.get_card_field(card_id, "sound")).strip():
    return TtsResult(generated=False, skipped=True, failed=False)
```

If `sound` is non-empty AT ALL, skip — covers both "user recorded their
own", "sensei generated previously", and "user pasted something else".
The user explicitly chose this conservative behavior over a more-specific
"only skip if [sound:sensei_" check.

Media file naming: `sensei_<card_id>.mp3`. The `card_id` is Anki's
internal integer id, guaranteed unique per note. Collision-free even
across decks.

## Error handling

Per-card errors are isolated; the batch continues. No single failure
aborts the rest.

| Failure | Where caught | Stat updated |
|---|---|---|
| `sound` field doesn't exist on this card's note type | inside `generate(card)` | `tts_failures` (logged once per deck-mismatch run) |
| Piper synth raises (model load, bad input) | inside `generate(card)` | `tts_failures` |
| MP3 encode raises | inside `generate(card)` | `tts_failures` |
| Disk write to `collection.media/` raises | inside `generate(card)` | `write_failures` |
| `set_card_field` Anki write raises | inside `generate(card)` | `write_failures` |
| AnkiWeb sync fails at end of batch | `try_sync` returns False | reported on `TtsBackfillResult.sync_ok=False` |
| Voice model file missing at startup | `TTSGenerator.__init__` raises | bubbles up; bot fails to boot (deploy error, not transient) |
| Batch re-entry | `generate_all` raises `BatchAlreadyRunningError` | `_daily_tts` / `_run_tts` catches and logs |

## Edge cases

- **Empty deck:** `get_all_card_ids("English")` returns `[]`,
  `generate_all` returns zero-valued `TtsBatchStats`. No-op, no error.
- **Note type lacks `sound` field:** every card's `get_card_field` returns
  `""` (per the design — non-existent field is `""`), then
  `set_card_field` raises `KeyError`. Per-card try/except catches it as
  `tts_failures`. v1 reports the count via stats and leaves diagnosis
  to the user (deck-mismatch detection is out of scope).
- **User changes deck selection between batches:** each daily run reads
  fresh from prefs. The next run will operate on the new deck.
- **User has no deck selected at all:** daily jobs log warning and skip.
  `/tts` and `/retag` commands reply asking the user to `/decks` first.
- **Quiz running when batch fires:** allowed. Both compete at
  `collection_lock`, fair interleaving.
- **First batch with many cards:** `5000 cards × ~700ms (libritts-high
  CPU)` ≈ 1 hour. Acceptable for a 03:00–04:00 background slot.
- **First media sync uploads all existing media:** one-time cost when
  `sync_media=True` flips on. After that, incremental.

## Testing

New tests:

- `tests/tts/test_generator.py` — `TTSGenerator` with fake piper (returns
  fixed WAV bytes via `MagicMock(spec=...)`):
  - `generate(card)` writes file + sets field on cold card.
  - `generate(card)` skips when `sound` is non-empty.
  - `generate(card)` reports `tts_failures` on piper raise.
  - `generate_all(deck)` iterates only cards in that deck.
  - `generate_all(deck)` accumulates stats correctly.
  - `generate_all(deck)` raises `BatchAlreadyRunningError` on re-entry.

- `tests/tts/test_backfill.py` — `run_tts_backfill`:
  - happy path returns `TtsBackfillResult` with sync_ok=True.
  - sync failure reflected as `sync_ok=False`, doesn't raise.

- `tests/bot/test_tts_handler.py` — `/tts` handler:
  - Replies "TTS started" and spawns task.
  - Replies "already running" when `generator.is_running`.
  - Replies "pick a deck first" when no deck selected.

- `tests/bot/test_register_jobs.py` — update existing test to cover the
  new `_daily_tts` job + deck resolution from prefs.

- `tests/anki/test_client.py` — extend with tests for
  `get_all_card_ids(deck=...)` filter, `get_card_field`, `set_card_field`.

- `tests/word_service/test_card_tagger.py` — extend
  `classify_local_all` / `classify_register_all` tests with a deck=
  parameter case (assert `get_all_card_ids` was called with the right
  filter).

## Migration / deployment

- **First deploy** after this lands: the user needs to (1) download
  `en_US-libritts-high.onnx` + `.onnx.json` into the `piper_models`
  named volume; (2) ensure they've selected a deck via `/decks`.
  Without (1), the bot fails to boot (clear error). Without (2),
  daily jobs log a clear warning and skip.
- **First daily run** after deploy: scans the selected deck. Cards with
  empty `sound` get TTS. Cards with non-empty `sound` are skipped.
- **First sync** after `sync_media=True` flip: uploads every existing
  media file in `collection.media/`. One-time cost.
- **Existing tag-backfill behavior change:** the next daily-retag (and
  every manual `/retag`) reads the user's selected deck. If no deck is
  selected, the job is skipped. **The user must pick a deck via `/decks`
  before the next daily 03:00 run** or tagging stops running. Spec
  flags this prominently.

## Open questions

None — all clarifying questions resolved in brainstorming. Approved
decisions:

- **Target field:** existing `sound` field on the EN deck's note type
  (lowercase `sound`).
- **Voice:** `en_US-libritts-high` (~115MB). User-overridable via
  `PIPER_VOICE` and `PIPER_VOICE_PATH` env vars.
- **Packaging:** `piper-tts` from PyPI (pure-Python + onnxruntime).
- **MP3 encode:** preference for `lameenc` (pure-Python, smaller). Final
  pick deferred to implementation plan if it doesn't behave.
- **Trigger:** independent daily at 04:00 + `/tts` manual command.
- **Idempotency:** skip if `sound` field is non-empty (any content).
- **Media sync:** enable globally via `sync_media=True`.
- **Deck source:** `UserPrefsStore.get_deck()` for the first
  `ALLOWED_USER_IDS` member (single-user bot). Both new TTS job and
  existing tag-backfill respect this.
- **No deck selected:** daily jobs log warning and skip. Manual commands
  reply asking the user to `/decks` first.
