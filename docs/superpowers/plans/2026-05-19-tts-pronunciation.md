# TTS Pronunciation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate Piper TTS audio for every card in the user-selected deck whose `sound` field is empty, set `[sound:sensei_<card_id>.mp3]` on the card, and sync the MP3 up to AnkiWeb. New `/tts` Telegram command + a daily JobQueue at 04:00 both share the same pipeline.

**Architecture:** New `src/tts/` module containing `TTSGenerator` (per-card and per-batch tag I/O) and `run_tts_backfill` (orchestration). Retrofits the existing tag-backfill (`classify_local_all`, `classify_register_all`, `run_full_backfill`) to accept a `deck` parameter — both new TTS and existing tag jobs read the deck from `UserPrefsStore` for the first allowed user. Mirrors the structure of `src/word_service/card_tagger.py` + `src/word_service/backfill.py`.

**Tech Stack:** Python 3.13, `piper-tts` (PyPI, brings ONNX runtime), `lameenc` (PyPI, pure-Python WAV→MP3), existing `python-telegram-bot[job-queue]==22.7`, SQLModel.

**Spec:** `docs/superpowers/specs/2026-05-19-tts-pronunciation-design.md`

---

## File Structure

**New:**
- `src/tts/__init__.py` — package marker (empty)
- `src/tts/generator.py` — `TTSGenerator` class, `TtsResult` + `TtsBatchStats` dataclasses
- `src/tts/backfill.py` — `run_tts_backfill` orchestration + `TtsBackfillResult`
- `tests/tts/__init__.py` — package marker (empty)
- `tests/tts/test_generator.py` — TTSGenerator unit tests
- `tests/tts/test_backfill.py` — `run_tts_backfill` tests
- `tests/bot/test_tts_handler.py` — `/tts` handler tests

**Modified:**
- `pyproject.toml` — add `piper-tts`, `lameenc` deps
- `src/config.py` — `tts_daily_hour`, `piper_voice_path`, `piper_voice`, `anki_media_path`; validate hour range
- `src/anki/client.py` — `get_all_card_ids(deck)` filter; new `get_card_field` + `set_card_field`
- `src/anki/sync.py` — `sync_media=True`
- `src/word_service/card_tagger.py` — `deck=None` on `classify_local_all` / `classify_register_all`
- `src/word_service/backfill.py` — `deck=None` on `run_full_backfill`; thread through
- `src/bot/handlers.py` — `make_handlers(..., tagger, generator)`; `/retag` reads deck from prefs; new `/tts` handler; `_HELP_TEXT` adds `/tts`
- `src/bot/app.py` — `register_jobs` gains `generator`, `prefs_store`, `allowed_user_ids`, `retag_hour`, `tts_hour`; new `_daily_tts`; new `_resolve_daily_deck` helper; new `BotCommand("tts", ...)`
- `src/main.py` — build TTSGenerator; thread new args
- `Dockerfile` + `docker-compose.yml` — new env vars; volume mount for piper voice model
- `.env.example` — document new env vars

**Existing test files extended:**
- `tests/anki/test_client.py` — deck filter, `get_card_field`, `set_card_field`
- `tests/anki/test_sync.py` — verify sync_media=True is passed
- `tests/word_service/test_card_tagger.py` — `deck=` parameter cases
- `tests/word_service/test_backfill.py` — `deck=` thread-through
- `tests/bot/test_retag_handler.py` — deck-from-prefs path + reject-when-none
- `tests/bot/test_register_jobs.py` — _daily_tts + _resolve_daily_deck

---

## Task 1: `AnkiClient.get_all_card_ids` accepts deck filter

**Why first:** foundation for every later batch (both TTS and the retrofitted tag-backfill).

**Files:**
- Modify: `src/anki/client.py:84-86`
- Modify: `tests/anki/test_client.py`

- [ ] **Step 1: Append failing test**

Append to `tests/anki/test_client.py`:

```python
@pytest.mark.asyncio
async def test_get_all_card_ids_filters_by_deck_when_given():
    col = MagicMock()
    col.find_cards.return_value = [10, 20]
    client = _client_with_col(col)

    ids = await client.get_all_card_ids(deck="English")

    assert ids == [10, 20]
    col.find_cards.assert_called_once_with('deck:"English"')


@pytest.mark.asyncio
async def test_get_all_card_ids_unfiltered_when_deck_is_none():
    col = MagicMock()
    col.find_cards.return_value = [1, 2, 3]
    client = _client_with_col(col)

    ids = await client.get_all_card_ids()  # default deck=None

    assert ids == [1, 2, 3]
    col.find_cards.assert_called_once_with("")
```

- [ ] **Step 2: Run tests to verify the deck-filter test fails**

```
uv run pytest tests/anki/test_client.py -v -k "filters_by_deck or unfiltered_when_deck"
```

Expected: `test_get_all_card_ids_filters_by_deck_when_given` fails (current signature has no deck param); `test_get_all_card_ids_unfiltered_when_deck_is_none` passes (existing behavior).

- [ ] **Step 3: Modify the method to accept deck**

Replace `get_all_card_ids` in `src/anki/client.py`:

```python
    async def get_all_card_ids(self, deck: str | None = None) -> list[int]:
        """Returns ids of every card in the collection, optionally filtered by
        deck name. Cheap; one lock acquire."""
        query = f'deck:"{deck}"' if deck else ""
        return await self._run_locked(lambda col: list(col.find_cards(query)))
```

- [ ] **Step 4: Run tests, confirm both pass**

```
uv run pytest tests/anki/test_client.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Lint + format**

```
uv run ruff check src/anki/client.py tests/anki/test_client.py
uv run ruff format src/anki/client.py tests/anki/test_client.py
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/anki/client.py tests/anki/test_client.py
git commit -m "feat(anki): get_all_card_ids accepts optional deck filter"
```

---

## Task 2: `AnkiClient.get_card_field` + `set_card_field`

**Why:** TTS needs to read the current `sound` field (idempotency check) and write `[sound:...]` to it. Anki notes have model-defined field names; both methods accept a string name and resolve to the underlying index.

**Files:**
- Modify: `src/anki/client.py` (append two new methods)
- Modify: `tests/anki/test_client.py`

- [ ] **Step 1: Append failing tests**

```python
@pytest.mark.asyncio
async def test_get_card_field_returns_value_by_field_name():
    note = MagicMock()
    note.note_type.return_value = {"flds": [{"name": "front"}, {"name": "sound"}]}
    note.fields = ["hello", "[sound:hi.mp3]"]
    card = MagicMock()
    card.note.return_value = note
    col = MagicMock()
    col.get_card.return_value = card
    client = _client_with_col(col)

    assert await client.get_card_field(42, "sound") == "[sound:hi.mp3]"
    assert await client.get_card_field(42, "front") == "hello"


@pytest.mark.asyncio
async def test_get_card_field_returns_empty_when_field_absent():
    note = MagicMock()
    note.note_type.return_value = {"flds": [{"name": "front"}]}
    note.fields = ["hello"]
    card = MagicMock()
    card.note.return_value = note
    col = MagicMock()
    col.get_card.return_value = card
    client = _client_with_col(col)

    assert await client.get_card_field(42, "sound") == ""


@pytest.mark.asyncio
async def test_set_card_field_writes_value_and_persists_note():
    note = MagicMock()
    note.note_type.return_value = {"flds": [{"name": "front"}, {"name": "sound"}]}
    note.fields = ["hello", ""]
    card = MagicMock()
    card.note.return_value = note
    col = MagicMock()
    col.get_card.return_value = card
    client = _client_with_col(col)

    await client.set_card_field(42, "sound", "[sound:sensei_42.mp3]")

    assert note.fields[1] == "[sound:sensei_42.mp3]"
    col.update_note.assert_called_once_with(note)


@pytest.mark.asyncio
async def test_set_card_field_raises_keyerror_for_unknown_field():
    note = MagicMock()
    note.note_type.return_value = {"flds": [{"name": "front"}]}
    note.fields = ["hello"]
    card = MagicMock()
    card.note.return_value = note
    col = MagicMock()
    col.get_card.return_value = card
    client = _client_with_col(col)

    with pytest.raises(KeyError, match="sound"):
        await client.set_card_field(42, "sound", "x")
```

- [ ] **Step 2: Run tests, expect failure**

```
uv run pytest tests/anki/test_client.py -v -k "card_field"
```

Expected: 4 failures (`get_card_field` / `set_card_field` don't exist).

- [ ] **Step 3: Implement the methods**

Append to `src/anki/client.py` after `get_card`:

```python
    async def get_card_field(self, card_id: int, field_name: str) -> str:
        """Read a named field's value. Returns "" if the field is absent on
        this card's note type."""

        def fn(col):
            note = col.get_card(card_id).note()
            idx = self._field_index(note, field_name)
            if idx is None:
                return ""
            return note.fields[idx]

        return await self._run_locked(fn)

    async def set_card_field(self, card_id: int, field_name: str, value: str) -> None:
        """Write a named field's value. Raises KeyError if the field is absent
        on this card's note type."""

        def fn(col):
            note = col.get_card(card_id).note()
            idx = self._field_index(note, field_name)
            if idx is None:
                raise KeyError(field_name)
            note.fields[idx] = value
            col.update_note(note)

        await self._run_locked(fn)

    @staticmethod
    def _field_index(note, field_name: str) -> int | None:
        for i, f in enumerate(note.note_type()["flds"]):
            if f["name"] == field_name:
                return i
        return None
```

- [ ] **Step 4: Run tests, confirm all pass**

```
uv run pytest tests/anki/test_client.py -v
```

Expected: 8 passed (4 prior + 4 new).

- [ ] **Step 5: Lint + format**

```
uv run ruff check src/anki/client.py tests/anki/test_client.py
uv run ruff format src/anki/client.py tests/anki/test_client.py
```

- [ ] **Step 6: Commit**

```bash
git add src/anki/client.py tests/anki/test_client.py
git commit -m "feat(anki): get_card_field / set_card_field for named-field access"
```

---

## Task 3: Enable media sync (`sync_media=True`)

**Files:**
- Modify: `src/anki/sync.py:35`
- Modify: `tests/anki/test_sync.py` (add coverage for the flag if reasonable)

- [ ] **Step 1: Change the flag**

In `src/anki/sync.py:35`, change:

```python
out = col.sync_collection(auth, sync_media=False)
```

to:

```python
out = col.sync_collection(auth, sync_media=True)
```

- [ ] **Step 2: Run existing sync tests (should still pass)**

```
uv run pytest tests/anki/test_sync.py -v
```

Expected: 3 passed. Existing tests mock `async_sync` not the underlying `sync()`, so they're insensitive to this flag.

- [ ] **Step 3: Lint + format**

```
uv run ruff check src/anki/sync.py
uv run ruff format src/anki/sync.py
```

- [ ] **Step 4: Commit**

```bash
git add src/anki/sync.py
git commit -m "feat(sync): enable media sync so generated audio reaches AnkiWeb"
```

---

## Task 4: Retrofit deck-scoping in `card_tagger` and `backfill`

**Why:** both new TTS and existing tag-backfill share the deck-scoping mechanism. Default `deck=None` preserves existing behavior for callers that don't pass a deck (no breakage), but daily-job and handler call sites will start passing the user's selection.

**Files:**
- Modify: `src/word_service/card_tagger.py`
- Modify: `src/word_service/backfill.py`
- Modify: `tests/word_service/test_card_tagger.py`
- Modify: `tests/word_service/test_backfill.py`

- [ ] **Step 1: Append a failing test on `classify_local_all`**

Append to `tests/word_service/test_card_tagger.py`:

```python
@pytest.mark.asyncio
async def test_classify_local_all_passes_deck_to_anki(monkeypatch):
    cards = {1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d")}
    tagger, anki, _ = _tagger_with_cards(cards)

    await tagger.classify_local_all(deck="English")

    anki.get_all_card_ids.assert_awaited_once_with(deck="English")


@pytest.mark.asyncio
async def test_classify_register_all_passes_deck_to_anki(monkeypatch):
    cards = {1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d")}
    tagger, anki, _ = _tagger_with_cards(cards)

    await tagger.classify_register_all(deck="English")

    anki.get_all_card_ids.assert_awaited_once_with(deck="English")
```

- [ ] **Step 2: Run tests, expect failure**

```
uv run pytest tests/word_service/test_card_tagger.py -v -k "passes_deck"
```

Expected: 2 failures (the calls inside `classify_*_all` currently invoke `get_all_card_ids()` with no args).

- [ ] **Step 3: Modify both batch methods**

In `src/word_service/card_tagger.py`, find `classify_local_all` and update the signature + the `get_all_card_ids` call:

```python
    async def classify_local_all(self, deck: str | None = None) -> LocalBatchStats:
        """Iterate every card in the collection (or just `deck` if given) and
        ensure frequency + academic tags are present. No LLM.
        Raises BatchAlreadyRunningError if another batch is in flight."""
        if self._batch_lock.locked():
            raise BatchAlreadyRunningError()
        stats = LocalBatchStats()
        async with self._batch_lock:
            card_ids = await self._anki.get_all_card_ids(deck=deck)
            stats.cards_scanned = len(card_ids)
            for card_id in card_ids:
                try:
                    card = await self._anki.get_card(card_id)
                    delta = await self.classify_local(card)
                    if delta.frequency_added:
                        stats.frequency_added += 1
                    if delta.academic_added:
                        stats.academic_added += 1
                except Exception:
                    logger.warning(
                        "classify_local_all: card %s failed; continuing",
                        card_id,
                        exc_info=True,
                    )
                    stats.write_failures += 1
        return stats
```

Same shape for `classify_register_all` — add `deck: str | None = None` to the signature and call `get_all_card_ids(deck=deck)`.

- [ ] **Step 4: Run tests, confirm pass**

```
uv run pytest tests/word_service/test_card_tagger.py -v
```

Expected: 28 passed (existing tests still pass + the 2 new ones).

- [ ] **Step 5: Update `run_full_backfill` to thread deck through**

Modify `src/word_service/backfill.py`:

```python
async def run_full_backfill(
    tagger: CardTagger, syncer: AnkiSyncer, deck: str | None = None
) -> BackfillResult:
    """Two-pass backfill: local → sync → register → sync."""
    local = await tagger.classify_local_all(deck=deck)
    local_sync_ok = await syncer.try_sync("after local pass")
    register = await tagger.classify_register_all(deck=deck)
    register_sync_ok = await syncer.try_sync("after register pass")
    return BackfillResult(
        local=local,
        register=register,
        local_sync_ok=local_sync_ok,
        register_sync_ok=register_sync_ok,
    )
```

- [ ] **Step 6: Append a failing test on `run_full_backfill`**

Append to `tests/word_service/test_backfill.py`:

```python
@pytest.mark.asyncio
async def test_run_full_backfill_threads_deck_to_batches():
    tagger = _tagger()
    syncer = _syncer()

    await run_full_backfill(tagger, syncer, deck="English")

    tagger.classify_local_all.assert_awaited_once_with(deck="English")
    tagger.classify_register_all.assert_awaited_once_with(deck="English")
```

- [ ] **Step 7: Run tests, confirm pass**

```
uv run pytest tests/word_service/test_backfill.py -v
```

Expected: 5 passed (4 prior + 1 new).

- [ ] **Step 8: Lint + format**

```
uv run ruff check src/word_service/ tests/word_service/
uv run ruff format src/word_service/ tests/word_service/
```

- [ ] **Step 9: Commit**

```bash
git add src/word_service/card_tagger.py src/word_service/backfill.py tests/word_service/test_card_tagger.py tests/word_service/test_backfill.py
git commit -m "feat(tagger): deck-scoping retrofit for tag-backfill"
```

---

## Task 5: New config + dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/config.py`
- Modify: `tests/test_config.py`
- Modify: `.env.example`

- [ ] **Step 1: Add deps to `pyproject.toml`**

Append to the `dependencies` list:

```
    "piper-tts>=1.2",
    "lameenc>=1.7",
```

- [ ] **Step 2: Install**

```
uv sync
```

Expected: both packages install. If `piper-tts` fails to install on Windows (it requires onnxruntime which sometimes lacks wheels for Python 3.14 preview), drop to Python 3.13 venv as noted in `pyproject.toml`'s `requires-python`.

- [ ] **Step 3: Add failing config tests**

Append to `tests/test_config.py`:

```python
def test_tts_daily_hour_default_is_4(base_env):
    base_env.delenv("TTS_DAILY_HOUR", raising=False)
    s = load_settings()
    assert s.tts_daily_hour == 4


def test_tts_daily_hour_rejects_out_of_range(base_env):
    base_env.setenv("TTS_DAILY_HOUR", "24")
    with pytest.raises(ValueError, match="TTS_DAILY_HOUR"):
        load_settings()


def test_piper_voice_path_default(base_env):
    base_env.delenv("PIPER_VOICE_PATH", raising=False)
    s = load_settings()
    assert s.piper_voice_path == "/data/piper/en_US-libritts-high.onnx"


def test_piper_voice_default(base_env):
    base_env.delenv("PIPER_VOICE", raising=False)
    s = load_settings()
    assert s.piper_voice == "en_US-libritts-high"


def test_anki_media_path_default(base_env):
    base_env.delenv("ANKI_MEDIA_PATH", raising=False)
    s = load_settings()
    assert s.anki_media_path == "/data/anki/collection.media"
```

- [ ] **Step 4: Run tests, expect failure**

```
uv run pytest tests/test_config.py -v -k "tts or piper or anki_media"
```

Expected: 5 failures (`tts_daily_hour`, `piper_voice_path`, etc. don't exist on `Settings`).

- [ ] **Step 5: Add new fields to `Settings` in `src/config.py`**

Modify the `Settings` dataclass — add five new fields:

```python
@dataclass
class Settings:
    telegram_token: str
    ankiweb_email: str
    ankiweb_password: str
    anki_collection_path: str
    db_path: str
    gemini_api_key: str
    gemini_model: str
    gemini_classify_model: str
    gemini_timeout_seconds: int
    scheduler_daily_hour: int
    tts_daily_hour: int
    piper_voice_path: str
    piper_voice: str
    anki_media_path: str
    max_cards_per_session: int
    allowed_user_ids: set[int]
    log_level: str
```

In `load_settings`, after the existing `scheduler_daily_hour` validation:

```python
    tts_daily_hour = int(os.environ.get("TTS_DAILY_HOUR", "4"))
    if not 0 <= tts_daily_hour <= 23:
        raise ValueError(
            f"TTS_DAILY_HOUR must be 0–23; got {tts_daily_hour}"
        )
```

In the `Settings(...)` constructor call, add the new entries:

```python
        tts_daily_hour=tts_daily_hour,
        piper_voice_path=os.environ.get(
            "PIPER_VOICE_PATH", "/data/piper/en_US-libritts-high.onnx"
        ),
        piper_voice=os.environ.get("PIPER_VOICE", "en_US-libritts-high"),
        anki_media_path=os.environ.get(
            "ANKI_MEDIA_PATH", "/data/anki/collection.media"
        ),
```

- [ ] **Step 6: Run tests, confirm pass**

```
uv run pytest tests/test_config.py -v
```

Expected: 9 passed (4 prior + 5 new).

- [ ] **Step 7: Update `.env.example`**

Add a section after the existing scheduler line:

```
# Hour-of-day (0–23) for the daily TTS pronunciation job
# TTS_DAILY_HOUR=4

# Piper voice model location and name. Voice file goes in a mounted
# volume; image stays slim. Match PIPER_VOICE to the file's basename.
# PIPER_VOICE_PATH=/data/piper/en_US-libritts-high.onnx
# PIPER_VOICE=en_US-libritts-high

# Anki media directory (where generated MP3s land). Default works with
# the existing anki_data volume mount.
# ANKI_MEDIA_PATH=/data/anki/collection.media
```

- [ ] **Step 8: Lint + format**

```
uv run ruff check src/config.py tests/test_config.py
uv run ruff format src/config.py tests/test_config.py
```

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock src/config.py tests/test_config.py .env.example
git commit -m "feat(config): add piper-tts + lameenc deps and TTS config fields"
```

---

## Task 6: `TTSGenerator` foundation + per-card `generate`

**Files:**
- Create: `src/tts/__init__.py` (empty)
- Create: `src/tts/generator.py`
- Create: `tests/tts/__init__.py` (empty)
- Create: `tests/tts/test_generator.py`

This task introduces the class scaffold, the dataclasses, and the per-card `generate` method. The batch entry point is added in Task 7.

- [ ] **Step 1: Create package markers**

Both `src/tts/__init__.py` and `tests/tts/__init__.py` as empty files. (For `tests/tts/__init__.py`: this directory uses no installed-package name that would shadow PyPI imports, so `__init__.py` is safe to create — unlike `tests/anki/` which we intentionally left without one.)

- [ ] **Step 2: Write failing tests**

Create `tests/tts/test_generator.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.anki.card_data import CardData
from src.tts.generator import (
    TTSGenerator,
    TtsBatchStats,
    TtsResult,
)
from src.word_service.card_tagger import BatchAlreadyRunningError


def _card(card_id: int = 1, front: str = "hello") -> CardData:
    return CardData(
        card_id=card_id, front=front, back="", tags=[], deck_name="d"
    )


def _generator(
    sound_field: str = "",
    field_writer=None,
    media_dir: str = "/tmp/media",
):
    anki = MagicMock()
    anki.get_card_field = AsyncMock(return_value=sound_field)
    anki.set_card_field = AsyncMock(side_effect=field_writer)
    gen = TTSGenerator(
        anki=anki,
        piper_model_path="/dev/null/voice.onnx",
        media_dir=media_dir,
        voice_name="en_US-libritts-high",
    )
    return gen, anki


@pytest.mark.asyncio
async def test_generate_skips_when_sound_field_already_set():
    gen, anki = _generator(sound_field="[sound:user_pronounce.mp3]")
    result = await gen.generate(_card())

    assert result == TtsResult(generated=False, skipped=True, failed=False)
    anki.set_card_field.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_synthesises_writes_media_and_sets_field(tmp_path):
    gen, anki = _generator(media_dir=str(tmp_path))

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        return_value=b"FAKE_MP3_BYTES",
    ):
        result = await gen.generate(_card(card_id=42, front="hello"))

    assert result == TtsResult(generated=True, skipped=False, failed=False)
    expected_path = tmp_path / "sensei_42.mp3"
    assert expected_path.exists()
    assert expected_path.read_bytes() == b"FAKE_MP3_BYTES"
    anki.set_card_field.assert_awaited_once_with(
        42, "sound", "[sound:sensei_42.mp3]"
    )


@pytest.mark.asyncio
async def test_generate_reports_failure_when_synthesis_raises(tmp_path):
    gen, anki = _generator(media_dir=str(tmp_path))

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        side_effect=RuntimeError("piper crashed"),
    ):
        result = await gen.generate(_card())

    assert result.generated is False
    assert result.failed is True
    anki.set_card_field.assert_not_awaited()
    assert not any(tmp_path.iterdir())  # no partial files left behind


@pytest.mark.asyncio
async def test_generate_reports_failure_when_field_write_raises(tmp_path):
    gen, anki = _generator(media_dir=str(tmp_path))
    anki.set_card_field = AsyncMock(side_effect=KeyError("sound"))

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        return_value=b"X",
    ):
        result = await gen.generate(_card())

    assert result.failed is True
```

- [ ] **Step 3: Run tests, expect failure**

```
uv run pytest tests/tts/test_generator.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.tts.generator'`.

- [ ] **Step 4: Create the module**

Create `src/tts/generator.py`:

```python
"""Piper TTS pronunciation generation.

`TTSGenerator` owns Piper invocation + per-card audio I/O. Two callers:

- Quiz-time `/tts` Telegram command (per-user trigger)
- Daily JobQueue at 04:00 (background trigger)

Both go through `generate_all(deck)`, which uses an instance-scoped
`_tts_lock` to serialize batches. The per-card `generate()` method is
also available for one-off use and does NOT touch the lock.

See docs/superpowers/specs/2026-05-19-tts-pronunciation-design.md."""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from src.anki.card_data import CardData
from src.anki.client import AnkiClient
from src.word_service.card_tagger import BatchAlreadyRunningError

logger = logging.getLogger(__name__)


@dataclass
class TtsResult:
    generated: bool   # MP3 newly written
    skipped: bool     # sound field already non-empty
    failed: bool      # piper / media / field write failed


@dataclass
class TtsBatchStats:
    cards_scanned: int = 0
    generated: int = 0
    skipped: int = 0
    tts_failures: int = 0   # piper synth failed
    write_failures: int = 0  # media or field write failed


def _synthesize_to_mp3_bytes(model_path: str, voice_name: str, text: str) -> bytes:
    """Production seam between TTSGenerator and the actual TTS toolchain.

    Synthesises `text` via Piper, encodes the PCM stream as MP3, returns bytes.
    Tests monkey-patch this function, so its signature is the stable contract.

    The body below targets piper-tts 1.x + lameenc. If the installed piper-tts
    has a different API (the package has churned across versions), adjust this
    body — tests don't depend on it."""
    from piper import PiperVoice  # type: ignore[import-untyped]
    import lameenc  # type: ignore[import-untyped]

    voice = PiperVoice.load(model_path)
    pcm = b"".join(
        chunk.audio_int16_bytes for chunk in voice.synthesize(text)
    )
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(96)
    encoder.set_in_sample_rate(voice.config.sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(2)
    return encoder.encode(pcm) + encoder.flush()


class TTSGenerator:
    def __init__(
        self,
        anki: AnkiClient,
        piper_model_path: str,
        media_dir: str,
        voice_name: str = "en_US-libritts-high",
    ):
        self._anki = anki
        self._model_path = piper_model_path
        self._media_dir = Path(media_dir)
        self._voice_name = voice_name
        self._tts_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._tts_lock.locked()

    async def generate(self, card: CardData) -> TtsResult:
        """Per-card. Idempotent: skips if `sound` field is non-empty."""
        existing = (await self._anki.get_card_field(card.card_id, "sound")).strip()
        if existing:
            return TtsResult(generated=False, skipped=True, failed=False)

        try:
            mp3_bytes = await asyncio.to_thread(
                _synthesize_to_mp3_bytes, self._model_path, self._voice_name, card.front
            )
        except Exception:
            logger.warning(
                "tts synthesis failed for card %s (front=%r)",
                card.card_id,
                card.front,
                exc_info=True,
            )
            return TtsResult(generated=False, skipped=False, failed=True)

        filename = f"sensei_{card.card_id}.mp3"
        media_path = self._media_dir / filename
        try:
            self._media_dir.mkdir(parents=True, exist_ok=True)
            media_path.write_bytes(mp3_bytes)
        except Exception:
            logger.warning(
                "tts media write failed for card %s (path=%s)",
                card.card_id,
                media_path,
                exc_info=True,
            )
            return TtsResult(generated=False, skipped=False, failed=True)

        try:
            await self._anki.set_card_field(
                card.card_id, "sound", f"[sound:{filename}]"
            )
        except Exception:
            logger.warning(
                "tts field write failed for card %s", card.card_id, exc_info=True
            )
            try:
                media_path.unlink(missing_ok=True)
            except OSError:
                pass
            return TtsResult(generated=False, skipped=False, failed=True)

        return TtsResult(generated=True, skipped=False, failed=False)
```

- [ ] **Step 5: Run tests, confirm pass**

```
uv run pytest tests/tts/test_generator.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Lint + format**

```
uv run ruff check src/tts/ tests/tts/
uv run ruff format src/tts/ tests/tts/
```

- [ ] **Step 7: Commit**

```bash
git add src/tts/__init__.py src/tts/generator.py tests/tts/__init__.py tests/tts/test_generator.py
git commit -m "feat(tts): add TTSGenerator with per-card generate"
```

---

## Task 7: `TTSGenerator.generate_all` batch

**Files:**
- Modify: `src/tts/generator.py` (add the batch method)
- Modify: `tests/tts/test_generator.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/tts/test_generator.py`:

```python
def _generator_with_cards(card_map: dict[int, CardData], sound_fields: dict[int, str] | None = None, media_dir: str = "/tmp/media"):
    sound_fields = sound_fields or {}
    anki = MagicMock()
    anki.get_all_card_ids = AsyncMock(return_value=list(card_map.keys()))
    anki.get_card = AsyncMock(side_effect=lambda cid: card_map[cid])
    anki.get_card_field = AsyncMock(
        side_effect=lambda cid, name: sound_fields.get(cid, "")
    )
    anki.set_card_field = AsyncMock()
    gen = TTSGenerator(
        anki=anki,
        piper_model_path="/dev/null/voice.onnx",
        media_dir=media_dir,
        voice_name="en_US-libritts-high",
    )
    return gen, anki


@pytest.mark.asyncio
async def test_generate_all_filters_by_deck_and_accumulates_stats(tmp_path):
    cards = {
        1: _card(card_id=1, front="alpha"),
        2: _card(card_id=2, front="beta"),
        3: _card(card_id=3, front="gamma"),
    }
    sounds = {2: "[sound:user_2.mp3]"}  # card 2 pre-tagged
    gen, anki = _generator_with_cards(cards, sound_fields=sounds, media_dir=str(tmp_path))

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        return_value=b"BYTES",
    ):
        stats = await gen.generate_all(deck="English")

    assert isinstance(stats, TtsBatchStats)
    assert stats.cards_scanned == 3
    assert stats.generated == 2  # 1 and 3
    assert stats.skipped == 1    # 2
    assert stats.tts_failures == 0
    assert stats.write_failures == 0
    anki.get_all_card_ids.assert_awaited_once_with(deck="English")


@pytest.mark.asyncio
async def test_generate_all_swallows_per_card_failures(tmp_path):
    cards = {1: _card(card_id=1), 2: _card(card_id=2)}
    gen, _ = _generator_with_cards(cards, media_dir=str(tmp_path))

    call_count = {"n": 0}

    def fake_synth(model, voice, text):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("piper crashed")
        return b"OK"

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        side_effect=fake_synth,
    ):
        stats = await gen.generate_all(deck="English")

    assert stats.cards_scanned == 2
    assert stats.generated == 1
    assert stats.tts_failures == 1


@pytest.mark.asyncio
async def test_generate_all_raises_when_batch_already_running(tmp_path):
    cards = {1: _card(card_id=1)}
    gen, _ = _generator_with_cards(cards, media_dir=str(tmp_path))

    async def slow_synth(model, voice, text):
        await asyncio.sleep(0.01)
        return b"X"

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        new=AsyncMock(side_effect=slow_synth),
    ):
        # Use asyncio.to_thread inside generate(), so the patch above doesn't
        # actually pause inside the lock — instead, hold via an explicit lock
        # acquire.
        await gen._tts_lock.acquire()
        try:
            with pytest.raises(BatchAlreadyRunningError):
                await gen.generate_all(deck="English")
        finally:
            gen._tts_lock.release()
```

- [ ] **Step 2: Run tests, expect failure**

```
uv run pytest tests/tts/test_generator.py -v -k "generate_all"
```

Expected: 3 failures (`generate_all` does not exist).

- [ ] **Step 3: Implement `generate_all`**

Append to the `TTSGenerator` class in `src/tts/generator.py`:

```python
    async def generate_all(self, deck: str) -> TtsBatchStats:
        """Iterate every card in `deck`, ensure sound field is set.
        Raises BatchAlreadyRunningError if another batch is in flight."""
        if self._tts_lock.locked():
            raise BatchAlreadyRunningError()
        stats = TtsBatchStats()
        async with self._tts_lock:
            card_ids = await self._anki.get_all_card_ids(deck=deck)
            stats.cards_scanned = len(card_ids)
            for card_id in card_ids:
                try:
                    card = await self._anki.get_card(card_id)
                    result = await self.generate(card)
                except Exception:
                    logger.warning(
                        "generate_all: card %s failed before generate; continuing",
                        card_id,
                        exc_info=True,
                    )
                    stats.write_failures += 1
                    continue
                if result.generated:
                    stats.generated += 1
                if result.skipped:
                    stats.skipped += 1
                if result.failed:
                    # `generate` returns failed for both synth and write
                    # failures; lump them together as tts_failures for now.
                    stats.tts_failures += 1
        return stats
```

- [ ] **Step 4: Run tests, confirm pass**

```
uv run pytest tests/tts/test_generator.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Lint + format**

```
uv run ruff check src/tts/ tests/tts/
uv run ruff format src/tts/ tests/tts/
```

- [ ] **Step 6: Commit**

```bash
git add src/tts/generator.py tests/tts/test_generator.py
git commit -m "feat(tts): add TTSGenerator.generate_all batch entry"
```

---

## Task 8: `run_tts_backfill` orchestration

**Files:**
- Create: `src/tts/backfill.py`
- Create: `tests/tts/test_backfill.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tts/test_backfill.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tts.backfill import TtsBackfillResult, run_tts_backfill
from src.tts.generator import TtsBatchStats
from src.word_service.card_tagger import BatchAlreadyRunningError


def _gen(stats=None, raises=None):
    g = MagicMock()
    if raises is not None:
        g.generate_all = AsyncMock(side_effect=raises)
    else:
        g.generate_all = AsyncMock(
            return_value=stats or TtsBatchStats(cards_scanned=1)
        )
    return g


def _syncer(try_sync_result=True):
    s = MagicMock()
    s.try_sync = AsyncMock(return_value=try_sync_result)
    return s


@pytest.mark.asyncio
async def test_run_tts_backfill_happy_path_returns_result():
    gen = _gen(TtsBatchStats(cards_scanned=5, generated=3, skipped=2))
    syncer = _syncer()

    result = await run_tts_backfill(gen, syncer, deck="English")

    assert isinstance(result, TtsBackfillResult)
    assert result.stats.generated == 3
    assert result.sync_ok is True
    gen.generate_all.assert_awaited_once_with(deck="English")


@pytest.mark.asyncio
async def test_run_tts_backfill_reflects_sync_failure_without_raising():
    gen = _gen()
    syncer = _syncer(try_sync_result=False)

    result = await run_tts_backfill(gen, syncer, deck="English")

    assert result.sync_ok is False


@pytest.mark.asyncio
async def test_run_tts_backfill_propagates_batch_already_running():
    gen = _gen(raises=BatchAlreadyRunningError())
    syncer = _syncer()

    with pytest.raises(BatchAlreadyRunningError):
        await run_tts_backfill(gen, syncer, deck="English")

    syncer.try_sync.assert_not_awaited()
```

- [ ] **Step 2: Run tests, expect failure**

```
uv run pytest tests/tts/test_backfill.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.tts.backfill'`.

- [ ] **Step 3: Create the module**

Create `src/tts/backfill.py`:

```python
"""TTS backfill orchestration: generate_all → sync.

Mirrors `src/word_service/backfill.py`. Trigger sites (the daily PTB
JobQueue and the `/tts` command handler) wrap this with their own
presentation; the pipeline only sequences generate + sync."""

from dataclasses import dataclass

from src.anki.sync import AnkiSyncer
from src.tts.generator import TTSGenerator, TtsBatchStats


@dataclass
class TtsBackfillResult:
    stats: TtsBatchStats
    sync_ok: bool


async def run_tts_backfill(
    generator: TTSGenerator, syncer: AnkiSyncer, deck: str
) -> TtsBackfillResult:
    """Single-pass backfill: generate_all → sync.

    Raises BatchAlreadyRunningError if a TTS batch is already in flight.
    A sync failure is absorbed by AnkiSyncer.try_sync and surfaced as
    sync_ok=False — it does not raise."""
    stats = await generator.generate_all(deck=deck)
    sync_ok = await syncer.try_sync("after tts")
    return TtsBackfillResult(stats=stats, sync_ok=sync_ok)
```

- [ ] **Step 4: Run tests, confirm pass**

```
uv run pytest tests/tts/test_backfill.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Lint + format**

```
uv run ruff check src/tts/ tests/tts/
uv run ruff format src/tts/ tests/tts/
```

- [ ] **Step 6: Commit**

```bash
git add src/tts/backfill.py tests/tts/test_backfill.py
git commit -m "feat(tts): add run_tts_backfill orchestration"
```

---

## Task 9: `/tts` handler + `/retag` deck retrofit

**Files:**
- Modify: `src/bot/handlers.py`
- Create: `tests/bot/test_tts_handler.py`
- Modify: `tests/bot/test_retag_handler.py`

- [ ] **Step 1: Write failing tests for the new `/tts` handler**

Create `tests/bot/test_tts_handler.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers import make_handlers
from src.tts.generator import TtsBatchStats
from src.word_service.card_tagger import BatchAlreadyRunningError


def _build_handlers(generator, prefs=None, syncer=None, tagger=None):
    sm = MagicMock()
    sm.has_active_session = MagicMock(return_value=False)
    if syncer is None:
        syncer = MagicMock()
        syncer.try_sync = AsyncMock(return_value=True)
    anki = MagicMock()
    if prefs is None:
        prefs = MagicMock()
        prefs.get_deck = MagicMock(return_value="English")
    if tagger is None:
        tagger = MagicMock()
        type(tagger).is_running = property(lambda self: False)
    return (
        make_handlers(sm, syncer, anki, prefs, tagger, generator),
        syncer,
        prefs,
    )


def _update_and_ctx(user_id: int = 1):
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 555
    update.effective_user.id = user_id
    ctx = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return update, ctx


@pytest.mark.asyncio
async def test_tts_replies_already_running_when_generator_is_running():
    generator = MagicMock()
    type(generator).is_running = property(lambda self: True)
    handlers, _, _ = _build_handlers(generator)
    update, ctx = _update_and_ctx()

    await handlers["tts"](update, ctx)

    update.message.reply_text.assert_awaited_once()
    assert "already running" in update.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_tts_replies_pick_deck_when_no_deck_selected():
    generator = MagicMock()
    type(generator).is_running = property(lambda self: False)
    prefs = MagicMock()
    prefs.get_deck = MagicMock(return_value=None)
    handlers, _, _ = _build_handlers(generator, prefs=prefs)
    update, ctx = _update_and_ctx()

    await handlers["tts"](update, ctx)

    update.message.reply_text.assert_awaited_once()
    assert "decks" in update.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_tts_runs_pipeline_and_reports_stats():
    generator = MagicMock()
    type(generator).is_running = property(lambda self: False)
    generator.generate_all = AsyncMock(
        return_value=TtsBatchStats(cards_scanned=10, generated=8, skipped=2)
    )
    handlers, syncer, _ = _build_handlers(generator)
    update, ctx = _update_and_ctx()

    await handlers["tts"](update, ctx)

    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        await t

    generator.generate_all.assert_awaited_once_with(deck="English")
    syncer.try_sync.assert_awaited_once()
    body = ctx.bot.send_message.await_args.kwargs.get("text") or ctx.bot.send_message.await_args.args[1]
    assert "generated=8" in body and "skipped=2" in body
```

Append to `tests/bot/test_retag_handler.py` (add deck-from-prefs path):

```python
@pytest.mark.asyncio
async def test_retag_replies_pick_deck_when_no_deck_selected():
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: False)
    syncer = MagicMock()
    syncer.try_sync = AsyncMock(return_value=True)
    prefs = MagicMock()
    prefs.get_deck = MagicMock(return_value=None)
    sm = MagicMock()
    sm.has_active_session = MagicMock(return_value=False)
    anki = MagicMock()
    generator = MagicMock()
    type(generator).is_running = property(lambda self: False)
    handlers = make_handlers(sm, syncer, anki, prefs, tagger, generator)

    update, ctx = _update_and_ctx()
    await handlers["retag"](update, ctx)

    update.message.reply_text.assert_awaited_once()
    assert "decks" in update.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_retag_passes_user_selected_deck_to_full_backfill():
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: False)
    tagger.classify_local_all = AsyncMock(return_value=LocalBatchStats(cards_scanned=1))
    tagger.classify_register_all = AsyncMock(return_value=RegisterBatchStats(cards_scanned=1))
    syncer = MagicMock()
    syncer.try_sync = AsyncMock(return_value=True)
    prefs = MagicMock()
    prefs.get_deck = MagicMock(return_value="English")
    sm = MagicMock()
    sm.has_active_session = MagicMock(return_value=False)
    anki = MagicMock()
    generator = MagicMock()
    type(generator).is_running = property(lambda self: False)
    handlers = make_handlers(sm, syncer, anki, prefs, tagger, generator)

    update, ctx = _update_and_ctx()
    await handlers["retag"](update, ctx)

    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        await t

    tagger.classify_local_all.assert_awaited_once_with(deck="English")
    tagger.classify_register_all.assert_awaited_once_with(deck="English")
    prefs.get_deck.assert_called_once_with(1)
```

(The retag test file already imports the relevant fixtures via `_build_handlers`. If those helpers' signatures need updating, do so in this step.)

Modify the existing `_build_handlers` in `tests/bot/test_retag_handler.py` to also accept and pass a `generator` parameter (default to a non-running mock).

- [ ] **Step 2: Run tests, expect failures**

```
uv run pytest tests/bot/ -v
```

Expected: previous retag tests fail (signature change), new tests fail (`/tts` doesn't exist).

- [ ] **Step 3: Modify `src/bot/handlers.py` — signature + retag deck retrofit + new /tts**

Update imports:

```python
from src.tts.backfill import run_tts_backfill
from src.tts.generator import TTSGenerator
```

Change `make_handlers` signature:

```python
def make_handlers(
    sm: QuizStateMachine,
    syncer: AnkiSyncer,
    anki: AnkiClient,
    prefs: UserPrefsStore,
    tagger: CardTagger,
    generator: TTSGenerator,
) -> dict:
```

Update `_HELP_TEXT`:

```python
    _HELP_TEXT = (
        "Commands:\n"
        "/quiz — Start a review session\n"
        "/sync — Sync with AnkiWeb\n"
        "/decks — Choose which deck to study\n"
        "/mode — Choose card mode (due / new / both)\n"
        "/status — Check how many cards are due\n"
        "/retag — Backfill missing sensei:* tags on the selected deck\n"
        "/tts — Generate pronunciation audio for the selected deck\n"
        "/stop — End current session\n"
        "/help — Show this help message\n"
    )
```

Replace the existing `retag_command` to read deck from prefs and reject if None:

```python
    async def retag_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if tagger.is_running:
            await update.message.reply_text(
                "A retag job is already running — try again later."
            )
            return
        deck = prefs.get_deck(update.effective_user.id)
        if deck is None:
            await update.message.reply_text(
                "Pick a deck first via /decks before running /retag."
            )
            return
        await update.message.reply_text(
            f"Retag started on '{deck}', will notify when done."
        )
        asyncio.create_task(
            _run_retag(update.effective_chat.id, context.bot, deck)
        )

    async def _run_retag(chat_id: int, bot, deck: str) -> None:
        try:
            result = await run_full_backfill(tagger, syncer, deck=deck)
            await bot.send_message(chat_id, text=_format_stats(result))
        except BatchAlreadyRunningError:
            await bot.send_message(
                chat_id, text="A retag job is already running — try again later."
            )
        except Exception as e:
            logger.exception("retag failed")
            await bot.send_message(chat_id, text=f"Retag failed: {e}")
```

Add the new `tts_command` next to `retag_command`:

```python
    async def tts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if generator.is_running:
            await update.message.reply_text(
                "A TTS job is already running — try again later."
            )
            return
        deck = prefs.get_deck(update.effective_user.id)
        if deck is None:
            await update.message.reply_text(
                "Pick a deck first via /decks before running /tts."
            )
            return
        await update.message.reply_text(
            f"TTS started on '{deck}', will notify when done."
        )
        asyncio.create_task(
            _run_tts(update.effective_chat.id, context.bot, deck)
        )

    async def _run_tts(chat_id: int, bot, deck: str) -> None:
        try:
            result = await run_tts_backfill(generator, syncer, deck=deck)
            await bot.send_message(chat_id, text=_format_tts_stats(result))
        except BatchAlreadyRunningError:
            await bot.send_message(
                chat_id, text="A TTS job is already running — try again later."
            )
        except Exception as e:
            logger.exception("tts failed")
            await bot.send_message(chat_id, text=f"TTS failed: {e}")
```

Add `_format_tts_stats` near `_format_stats` (module level):

```python
def _format_tts_stats(result) -> str:
    """Format TtsBackfillResult for Telegram."""
    s = result.stats
    lines = [
        f"Done. Scanned {s.cards_scanned} card(s).",
        f"generated={s.generated} skipped={s.skipped}"
        f" tts_failures={s.tts_failures} write_failures={s.write_failures}.",
    ]
    if not result.sync_ok:
        lines.append("Sync: FAILED")
    return "\n".join(lines)
```

Register `tts_command` in the returned dict:

```python
        "tts": tts_command,
```

- [ ] **Step 4: Run handler tests, confirm pass**

```
uv run pytest tests/bot/ -v
```

Expected: all previously passing tests still pass; the new retag-prefs tests pass; the 3 new /tts tests pass.

- [ ] **Step 5: Lint + format**

```
uv run ruff check src/bot/handlers.py tests/bot/
uv run ruff format src/bot/handlers.py tests/bot/
```

- [ ] **Step 6: Commit**

```bash
git add src/bot/handlers.py tests/bot/test_tts_handler.py tests/bot/test_retag_handler.py
git commit -m "feat(bot): add /tts command and retrofit /retag to use selected deck"
```

---

## Task 10: `register_jobs` adds `_daily_tts` + deck resolution

**Files:**
- Modify: `src/bot/app.py`
- Modify: `tests/bot/test_register_jobs.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/bot/test_register_jobs.py`:

```python
def _fake_prefs(deck=None):
    p = MagicMock()
    p.get_deck = MagicMock(return_value=deck)
    return p


def _fake_generator():
    from src.tts.generator import TtsBatchStats

    g = MagicMock()
    g.generate_all = AsyncMock(return_value=TtsBatchStats(cards_scanned=1))
    return g


def test_register_jobs_schedules_both_retag_and_tts():
    app = _fake_app()
    tagger, syncer = _tagger_and_syncer()
    generator = _fake_generator()
    prefs = _fake_prefs(deck="English")

    register_jobs(
        app,
        tagger=tagger,
        syncer=syncer,
        generator=generator,
        prefs_store=prefs,
        allowed_user_ids={1},
        retag_hour=3,
        tts_hour=4,
    )

    assert app.job_queue.run_daily.call_count == 2
    names = [
        call.kwargs["name"] for call in app.job_queue.run_daily.call_args_list
    ]
    assert "daily_retag" in names and "daily_tts" in names


def test_register_jobs_daily_tts_skips_when_no_deck_selected(caplog):
    app = _fake_app()
    tagger, syncer = _tagger_and_syncer()
    generator = _fake_generator()
    prefs = _fake_prefs(deck=None)

    register_jobs(
        app,
        tagger=tagger,
        syncer=syncer,
        generator=generator,
        prefs_store=prefs,
        allowed_user_ids={1},
        retag_hour=3,
        tts_hour=4,
    )

    # Pull out the daily_tts callback by name
    callbacks = {
        call.kwargs["name"]: call.args[0]
        for call in app.job_queue.run_daily.call_args_list
    }
    tts_cb = callbacks["daily_tts"]

    with caplog.at_level(logging.WARNING):
        asyncio.run(tts_cb(MagicMock()))

    assert "no deck selected" in caplog.text.lower()
    generator.generate_all.assert_not_awaited()


def test_register_jobs_daily_tts_runs_with_selected_deck():
    app = _fake_app()
    tagger, syncer = _tagger_and_syncer()
    generator = _fake_generator()
    prefs = _fake_prefs(deck="English")

    register_jobs(
        app,
        tagger=tagger,
        syncer=syncer,
        generator=generator,
        prefs_store=prefs,
        allowed_user_ids={1},
        retag_hour=3,
        tts_hour=4,
    )

    callbacks = {
        call.kwargs["name"]: call.args[0]
        for call in app.job_queue.run_daily.call_args_list
    }
    asyncio.run(callbacks["daily_tts"](MagicMock()))

    generator.generate_all.assert_awaited_once_with(deck="English")
```

Also update the existing `test_register_jobs_*` tests to pass the new required kwargs (`generator`, `prefs_store`, `allowed_user_ids`, `tts_hour`). The minimum change for older tests is: where they currently pass `daily_hour=3`, switch to `retag_hour=3, tts_hour=4` and add the three new args.

- [ ] **Step 2: Run tests, expect failures**

```
uv run pytest tests/bot/test_register_jobs.py -v
```

Expected: existing tests fail (signature changed), new tests fail (no `_daily_tts`).

- [ ] **Step 3: Update `src/bot/app.py:register_jobs`**

Add imports:

```python
from src.tts.backfill import run_tts_backfill
from src.tts.generator import TTSGenerator
from src.db.user_prefs_store import UserPrefsStore
```

Replace `register_jobs`:

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
    """Register PTB JobQueue jobs. Called by main.py after build_app."""

    def _resolve_daily_deck() -> str | None:
        """Single-user bot: pull deck from the first allowed user."""
        if not allowed_user_ids:
            return None
        user_id = next(iter(allowed_user_ids))
        return prefs_store.get_deck(user_id)

    async def _daily_retag(context: ContextTypes.DEFAULT_TYPE) -> None:
        deck = _resolve_daily_deck()
        if deck is None:
            logging.warning("daily retag skipped: no deck selected")
            return
        try:
            result = await run_full_backfill(tagger, syncer, deck=deck)
            logging.info("daily retag done on '%s': %s", deck, result)
        except BatchAlreadyRunningError:
            logging.warning("daily retag skipped: another batch already running")

    async def _daily_tts(context: ContextTypes.DEFAULT_TYPE) -> None:
        deck = _resolve_daily_deck()
        if deck is None:
            logging.warning("daily tts skipped: no deck selected")
            return
        try:
            result = await run_tts_backfill(generator, syncer, deck=deck)
            logging.info("daily tts done on '%s': %s", deck, result)
        except BatchAlreadyRunningError:
            logging.warning("daily tts skipped: another batch already running")

    app.job_queue.run_daily(
        _daily_retag,
        time=time(hour=retag_hour, tzinfo=_LOCAL_TZ),
        name="daily_retag",
    )
    app.job_queue.run_daily(
        _daily_tts,
        time=time(hour=tts_hour, tzinfo=_LOCAL_TZ),
        name="daily_tts",
    )
```

Also add the new BotCommand entry in `_BOT_COMMANDS`:

```python
    BotCommand("tts", "Generate pronunciation audio for selected deck"),
```

And register the handler in `build_app`, next to `/retag`:

```python
    app.add_handler(CommandHandler("tts", handlers["tts"], filters=user_filter))
```

- [ ] **Step 4: Run all bot tests, confirm pass**

```
uv run pytest tests/bot/ -v
```

Expected: all pass.

- [ ] **Step 5: Lint + format**

```
uv run ruff check src/bot/app.py tests/bot/test_register_jobs.py
uv run ruff format src/bot/app.py tests/bot/test_register_jobs.py
```

- [ ] **Step 6: Commit**

```bash
git add src/bot/app.py tests/bot/test_register_jobs.py
git commit -m "feat(jobs): register daily TTS job; deck source from UserPrefsStore"
```

---

## Task 11: `main.py` composition + Docker deployment

**Files:**
- Modify: `src/main.py`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Update `src/main.py`**

Add imports near the top:

```python
from src.tts.generator import TTSGenerator
```

Replace the wiring block (currently `tagger = CardTagger(...)` through `register_jobs(...)`):

```python
    tagger = CardTagger(anki_client, classifier)
    generator = TTSGenerator(
        anki=anki_client,
        piper_model_path=settings.piper_voice_path,
        media_dir=settings.anki_media_path,
        voice_name=settings.piper_voice,
    )
    state_machine = QuizStateMachine(
        anki_client,
        anki_syncer,
        agent,
        tagger,
        prefs_store,
        errors_store,
        sessions_store,
    )
    handlers = make_handlers(
        state_machine,
        anki_syncer,
        anki_client,
        prefs_store,
        tagger,
        generator,
    )
    app = build_app(settings.telegram_token, settings.allowed_user_ids, handlers)
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
    app.run_polling(allowed_updates=["message", "callback_query"])
```

- [ ] **Step 2: Update `Dockerfile`**

No apt changes needed (piper-tts is pure-Python). Add the piper voice directory to the chown'd paths so the non-root user can read:

```dockerfile
RUN mkdir -p /data/anki /data/db /data/piper /app/.uv-cache && \
    groupadd -r sensei && useradd -r -g sensei sensei && \
    chown -R sensei:sensei /app /data
```

- [ ] **Step 3: Update `docker-compose.yml`**

Add env vars to the `environment` block:

```yaml
      - TTS_DAILY_HOUR=4
      - PIPER_VOICE_PATH=/data/piper/en_US-libritts-high.onnx
      - PIPER_VOICE=en_US-libritts-high
      - ANKI_MEDIA_PATH=/data/anki/collection.media
```

Add a new named volume mount:

```yaml
    volumes:
      - anki_data:/data/anki
      - sensei_db:/data/db
      - piper_models:/data/piper:ro
```

And declare the new volume at the bottom:

```yaml
volumes:
  anki_data:
  sensei_db:
  piper_models:
```

- [ ] **Step 4: Sanity-check `python -m src.main` imports**

```
uv run python -c "import importlib; importlib.import_module('src.main')"
```

Expected: exit 0.

- [ ] **Step 5: Run the full test suite**

```
uv run pytest -q
```

Expected: existing pre-existing Windows tempfile flakes (~18) remain; everything else passes.

- [ ] **Step 6: Lint + format**

```
uv run ruff check src/main.py
uv run ruff format src/main.py
```

- [ ] **Step 7: Commit**

```bash
git add src/main.py Dockerfile docker-compose.yml
git commit -m "feat(deploy): wire TTSGenerator + piper_models volume mount"
```

---

## Task 12: Voice model deployment helper

**Why:** the voice ONNX file (~115MB) is NOT in the Docker image. The user needs a one-shot way to populate the `piper_models` volume after first deploy. A small `scripts/fetch-piper-voice.sh` doc-as-code script makes the procedure copy-pasteable.

**Files:**
- Create: `scripts/fetch-piper-voice.sh`

- [ ] **Step 1: Create the helper script**

Create `scripts/fetch-piper-voice.sh`:

```bash
#!/usr/bin/env bash
# Download the Piper voice model into the `piper_models` named volume.
# Run once after first deploy of the TTS feature.
#
# Usage:
#   ./scripts/fetch-piper-voice.sh                       # en_US-libritts-high
#   PIPER_VOICE=en_US-amy-medium ./scripts/fetch-piper-voice.sh
set -euo pipefail

VOICE="${PIPER_VOICE:-en_US-libritts-high}"
QUALITY="${VOICE##*-}"                  # "high", "medium", "low"
LOCALE="${VOICE%-*}"                    # "en_US-libritts"
LANG="${LOCALE%%_*}"                    # "en"
REGION_VOICE="${LOCALE#*_}"             # "US-libritts"
REGION="${REGION_VOICE%%-*}"            # "US"
SPEAKER="${REGION_VOICE#*-}"            # "libritts"

BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/${LANG}/${LANG}_${REGION}/${SPEAKER}/${QUALITY}"

echo "Fetching ${VOICE} from ${BASE} ..."

# Use the existing sensei service (curl is already installed in its image)
# to write into the piper_models volume. --no-deps avoids starting other
# services; --volume overrides the :ro mount mode from compose to allow
# writes during this one-off operation; --entrypoint runs sh instead of
# the bot's default CMD.
docker compose run --rm \
  --no-deps \
  --volume "piper_models:/data/piper:rw" \
  --entrypoint sh \
  sensei -c "
    curl -fL -o /data/piper/${VOICE}.onnx '${BASE}/${VOICE}.onnx'
    curl -fL -o /data/piper/${VOICE}.onnx.json '${BASE}/${VOICE}.onnx.json'
    ls -lh /data/piper/
  "

echo "Done. Restart sensei: docker compose up -d --force-recreate sensei"
```

Make it executable: `chmod +x scripts/fetch-piper-voice.sh`.

- [ ] **Step 2: Commit**

```bash
git add scripts/fetch-piper-voice.sh
git commit -m "feat(deploy): one-shot script to fetch piper voice into volume"
```

(No test for this — it's a deploy-tooling shell script. Validate by running it once on the production host as part of feature deployment.)

---

## Task 13: Final verification

**No code changes.** Confirm everything works as a whole before declaring done.

- [ ] **Step 1: Lint everything**

```
uv run ruff check src/ tests/
```

Expected: All checks passed.

- [ ] **Step 2: Format check**

```
uv run ruff format --check src/ tests/
```

Expected: All files already formatted.

- [ ] **Step 3: Run the full test suite**

```
uv run pytest -q
```

Expected: all functional tests pass. 18 pre-existing Windows-only `PermissionError: [WinError 32]` flakes in `tests/agent/test_state_machine.py` and `tests/db/` are unrelated to this branch.

- [ ] **Step 4: Security audit**

```
uv run pip-audit
```

Expected: no new advisories beyond the existing baseline. `piper-tts` brings `onnxruntime` as a transitive — both are reputable.

- [ ] **Step 5: Manual smoke test plan** (do not run automatically)

Document for the operator:

> 1. After deploying the new image: run `./scripts/fetch-piper-voice.sh` on the production host to populate the `piper_models` volume.
> 2. Restart sensei: `docker compose up -d --force-recreate sensei`.
> 3. In Telegram, run `/decks` and pick the English deck (if not already picked).
> 4. Run `/tts`. Expect "TTS started on 'English', will notify when done." followed (after ~minutes) by `Done. Scanned N card(s). generated=X skipped=Y tts_failures=Z write_failures=W.`
> 5. Open Anki desktop. Pick a card whose `sound` field was empty before. Confirm it now contains `[sound:sensei_<id>.mp3]` and that pressing the play button plays Piper's pronunciation.
> 6. Open AnkiMobile (or AnkiWeb). Confirm the same card plays audio after sync.
> 7. Wait until 04:00 local time. Confirm log line `daily tts done on 'English': ...` appears at `WARNING`-or-higher level (per the LOG_LEVEL bump in commit `2707ccc`). If nothing appears, check that a deck is selected (`/decks` was called for the allowed user at least once).

- [ ] **Step 6: NO commit needed** — this task is verification only.

---

## Self-review notes

This section is the planner's notes for the executor — read once, then discard.

- **Spec coverage:** every "Modified:" / "New:" file in the spec maps to a task above. New module → Tasks 6–8. AnkiClient additions → Tasks 1–2. Sync change → Task 3. Tag-backfill retrofit → Task 4. Config + deps → Task 5. Handler → Task 9. JobQueue + main.py → Tasks 10–11. Deploy tooling → Task 12.
- **Type/method consistency:** `TtsResult` / `TtsBatchStats` / `TtsBackfillResult`, `TTSGenerator`, `BatchAlreadyRunningError`, `run_tts_backfill`, `get_card_field`, `set_card_field`, `_synthesize_to_mp3_bytes`, `_resolve_daily_deck` — all names appear consistently across the plan.
- **Production seam for piper-tts:** `_synthesize_to_mp3_bytes(model_path, voice_name, text) -> bytes` is the only point of contact with `piper-tts` / `lameenc`. The function is `raise NotImplementedError` in Task 6 with a reference snippet in the docstring; the implementer wires it to the real packages when they install. Tests monkey-patch this function, so the implementation can land cleanly without breaking the test suite.
- **Order discipline:** Tasks 1–4 are backward-compatible (new params default to None / no behavior change for old callers). Task 9 is the first commit that REQUIRES a deck selection — the `/retag` UX changes from "run on all cards" to "ask user to pick a deck first". Spec calls this out; the executor should leave the deploy notes clear so the user knows to `/decks` after the feature lands.
- **Voice model deployment:** explicitly out-of-image. Task 12 provides the helper; the operator runs it once. No Dockerfile bloat.
