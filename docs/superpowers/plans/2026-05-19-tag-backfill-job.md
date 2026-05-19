# Tag Backfill Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the lazy `sensei:*` tag classification from quiz-time into a daily background batch (plus a manual `/retag` command), so quiz handlers are always cache-hits.

**Architecture:** Extract a `CardTagger` service that owns all tag I/O. Expose two independent batch methods — `classify_local_all` (frequency + AWL academic, no LLM) and `classify_register_all` (register LLM). The daily PTB JobQueue and the `/retag` handler both orchestrate the sequence `local → sync → register → sync` via the same shared lock. Quiz flow keeps tagging individual cards via `CardTagger.classify(card)` and is not blocked by an in-flight batch.

**Tech Stack:** Python 3.13, asyncio, `python-telegram-bot[job-queue]==22.7` (PTB JobQueue is backed by APScheduler — already installed), `wordfreq`, `google-genai`, SQLModel for prefs, `anki` package for Collection I/O.

**Spec:** `docs/superpowers/specs/2026-05-19-tag-backfill-job-design.md`

---

## File Structure

**New:**
- `src/word_service/card_tagger.py` — `CardTagger` class, exceptions, dataclasses, tag-vocabulary constants (moved from `word_classifier.py`)
- `tests/word_service/test_card_tagger.py` — unit tests for `CardTagger`
- `tests/anki/__init__.py` — package init (directory does not exist yet)
- `tests/anki/test_sync.py` — `AnkiSyncer.try_sync` test
- `tests/anki/test_client.py` — `AnkiClient.get_all_card_ids` + `get_card` tests
- `tests/bot/__init__.py` — package init
- `tests/bot/test_retag_handler.py` — `/retag` handler tests

**Modified:**
- `src/word_service/word_classifier.py` — remove `TAG_PREFIX`, `FREQUENCIES`, `REGISTERS`, `ACADEMICS` constants (they move into `card_tagger.py`); the class itself is unchanged
- `src/anki/sync.py` — add `AnkiSyncer.try_sync(label)`
- `src/anki/client.py` — add `get_all_card_ids()` and `get_card(card_id)`
- `src/agent/state_machine.py` — drop tag helpers (`_cached`, `_persist_tag`, `_classify_frequency`, `_classify_register`, `_classify_academic`); drop the tag-vocab + `WordClassifier` imports; constructor takes `CardTagger` instead of `WordClassifier`; `_begin_card` / `_next_question` call `await self._tagger.classify(card)` once
- `src/bot/handlers.py` — add `retag_handler` + `_run_retag`; `make_handlers` gains a `tagger` parameter
- `src/bot/app.py` — register `CommandHandler("retag", ...)` and add `BotCommand` to the menu
- `src/main.py` — build `CardTagger`, pass it into `QuizStateMachine` and `make_handlers`, register the daily JobQueue
- `src/config.py` — default `SCHEDULER_DAILY_HOUR` 8 → 3
- `.env.example` — flip the commented default to 3 with an updated comment
- `tests/agent/test_state_machine.py` — `_setup()` injects a `CardTagger` instead of a `WordClassifier`

---

## Task 1: `AnkiSyncer.try_sync` — non-raising sync wrapper

**Why this first:** smallest isolated change, used by every later task that touches sync. Note: `AnkiSyncer.async_sync()` already returns a `SyncResult(success, message)` rather than raising — so `try_sync` reads `result.success`, it does not wrap a try/except (this differs from how the spec text describes it; the spec's intent is "non-raising sync", which is what we deliver).

**Files:**
- Create: `tests/anki/__init__.py` (empty)
- Create: `tests/anki/test_sync.py`
- Modify: `src/anki/sync.py`

- [ ] **Step 1: Create the test package init**

Create `tests/anki/__init__.py` as an empty file.

```bash
ls tests/anki/ 2>/dev/null || mkdir tests/anki
```

Write the empty `__init__.py`:

```python
```

(File is intentionally empty — exists only to mark the directory as a package.)

- [ ] **Step 2: Write the failing test**

Create `tests/anki/test_sync.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.anki.sync import AnkiSyncer, SyncResult


def _syncer_with_result(result: SyncResult) -> AnkiSyncer:
    syncer = AnkiSyncer.__new__(AnkiSyncer)
    syncer._collection_path = "/dev/null"
    syncer._email = ""
    syncer._password = ""
    syncer.async_sync = AsyncMock(return_value=result)  # type: ignore[method-assign]
    return syncer


@pytest.mark.asyncio
async def test_try_sync_returns_true_on_success():
    syncer = _syncer_with_result(SyncResult(success=True, message="Sync complete"))
    ok = await syncer.try_sync("after local pass")
    assert ok is True


@pytest.mark.asyncio
async def test_try_sync_returns_false_on_failure():
    syncer = _syncer_with_result(SyncResult(success=False, message="network error"))
    ok = await syncer.try_sync("after register pass")
    assert ok is False


@pytest.mark.asyncio
async def test_try_sync_swallows_unexpected_exception():
    syncer = AnkiSyncer.__new__(AnkiSyncer)
    syncer._collection_path = "/dev/null"
    syncer._email = ""
    syncer._password = ""
    syncer.async_sync = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    ok = await syncer.try_sync("boom case")
    assert ok is False
```

- [ ] **Step 3: Run the test to confirm it fails**

```
uv run pytest tests/anki/test_sync.py -v
```

Expected: three failures with `AttributeError: 'AnkiSyncer' object has no attribute 'try_sync'`.

- [ ] **Step 4: Implement `try_sync`**

Modify `src/anki/sync.py`. Add `logging` import at the top (it isn't currently imported), and add the method to the `AnkiSyncer` class right after `async_sync`:

```python
import logging
```

```python
    async def try_sync(self, label: str = "") -> bool:
        """Like async_sync but never raises. Returns True on success, False otherwise.
        For background batch use-cases where a sync failure must not abort the run."""
        try:
            result = await self.async_sync()
        except Exception:
            logging.exception("sync failed %s", label)
            return False
        if not result.success:
            logging.warning("sync failed %s: %s", label, result.message)
        return result.success
```

- [ ] **Step 5: Run the test to confirm it passes**

```
uv run pytest tests/anki/test_sync.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Lint + format**

```
uv run ruff check src/anki/sync.py tests/anki/test_sync.py
uv run ruff format src/anki/sync.py tests/anki/test_sync.py
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/anki/sync.py tests/anki/test_sync.py tests/anki/__init__.py
git commit -m "feat(sync): add AnkiSyncer.try_sync for non-raising background sync"
```

---

## Task 2: `AnkiClient.get_all_card_ids` and `get_card`

**Why:** the batch methods need to enumerate every card id, then fetch each as `CardData`. Currently `AnkiClient` only exposes `get_due_cards` (filtered) and writes (`update_card_tags`, `answer_card`).

**Files:**
- Create: `tests/anki/test_client.py`
- Modify: `src/anki/client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/anki/test_client.py`:

```python
from unittest.mock import MagicMock

import pytest

from src.anki.client import AnkiClient


def _client_with_col(col_mock: MagicMock) -> AnkiClient:
    client = AnkiClient("/dev/null")
    client._with_collection = lambda fn: fn(col_mock)  # bypass real Collection open

    async def _run_locked(fn):
        return client._with_collection(fn)

    client._run_locked = _run_locked  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_get_all_card_ids_returns_collection_ids():
    col = MagicMock()
    col.find_cards.return_value = [10, 20, 30]
    client = _client_with_col(col)

    ids = await client.get_all_card_ids()

    assert ids == [10, 20, 30]
    col.find_cards.assert_called_once_with("")


@pytest.mark.asyncio
async def test_get_card_returns_card_data_via_card_to_data():
    col = MagicMock()
    note = MagicMock()
    note.tags = ["sensei:formal"]
    note.fields = ["<b>hello</b>", "<i>greeting</i>"]
    card = MagicMock()
    card.did = 1
    card.note.return_value = note
    col.get_card.return_value = card
    col.decks.name.return_value = "Default"

    client = _client_with_col(col)
    data = await client.get_card(42)

    assert data.card_id == 42
    assert data.front == "hello"
    assert data.back == "greeting"
    assert data.tags == ["sensei:formal"]
    assert data.deck_name == "Default"
```

- [ ] **Step 2: Run the test to confirm it fails**

```
uv run pytest tests/anki/test_client.py -v
```

Expected: two failures with `AttributeError: 'AnkiClient' object has no attribute 'get_all_card_ids'` (and analogous for `get_card`).

- [ ] **Step 3: Implement the two methods**

Modify `src/anki/client.py`. Add after `get_due_count` (around line 82):

```python
    async def get_all_card_ids(self) -> list[int]:
        """Returns ids of every card in the collection. Cheap; one lock acquire."""
        return await self._run_locked(lambda col: list(col.find_cards("")))

    async def get_card(self, card_id: int) -> CardData:
        """Fetch a single card as CardData, regardless of due/new state."""
        return await self._run_locked(lambda col: self._card_to_data(col, card_id))
```

- [ ] **Step 4: Run the test to confirm it passes**

```
uv run pytest tests/anki/test_client.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Lint + format**

```
uv run ruff check src/anki/client.py tests/anki/test_client.py
uv run ruff format src/anki/client.py tests/anki/test_client.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/anki/client.py tests/anki/test_client.py
git commit -m "feat(anki): expose get_all_card_ids and get_card on AnkiClient"
```

---

## Task 3: `CardTagger` foundation — constants, exception, dataclasses, helpers

**Why:** lay the type/constant scaffold and the two private helpers (`_cached` / `_persist_tag`) so subsequent tasks have something to build on. Move the four tag-vocabulary constants out of `word_classifier.py` since `WordClassifier` itself never references them — they belong with the tagger.

**Files:**
- Create: `src/word_service/card_tagger.py`
- Create: `tests/word_service/test_card_tagger.py`
- Modify: `src/word_service/word_classifier.py` (remove constants)

- [ ] **Step 1: Confirm no live consumer of the constants outside state_machine + word_classifier**

```
uv run python -c "import subprocess, sys; sys.exit(subprocess.call(['grep','-rEn','TAG_PREFIX|FREQUENCIES|REGISTERS|ACADEMICS','src/','tests/']))"
```

Or just run Grep through the tool — confirm only `src/word_service/word_classifier.py`, `src/agent/state_machine.py`, and possibly tests reference these. If anything else shows up, stop and ask.

- [ ] **Step 2: Write the failing test for `_cached` + `_persist_tag`**

Create `tests/word_service/test_card_tagger.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.anki.card_data import CardData
from src.word_service.card_tagger import (
    ACADEMICS,
    CardTagger,
    FREQUENCIES,
    REGISTERS,
    TAG_PREFIX,
)


def _tagger():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    return CardTagger(anki, classifier), anki, classifier


def _card(tags=None) -> CardData:
    return CardData(
        card_id=1, front="run", back="走る", tags=list(tags or []), deck_name="EN"
    )


def test_constants_re_exported():
    assert TAG_PREFIX == "sensei:"
    assert "common" in FREQUENCIES
    assert "formal" in REGISTERS
    assert "academic" in ACADEMICS


def test_cached_returns_matching_value():
    tagger, _, _ = _tagger()
    card = _card(tags=["sensei:formal", "other"])
    assert tagger._cached(card, REGISTERS) == "formal"


def test_cached_returns_none_when_absent():
    tagger, _, _ = _tagger()
    card = _card(tags=["other"])
    assert tagger._cached(card, REGISTERS) is None


def test_cached_ignores_unrelated_sensei_tags():
    """sensei:common is a frequency tag, not a register tag."""
    tagger, _, _ = _tagger()
    card = _card(tags=["sensei:common"])
    assert tagger._cached(card, REGISTERS) is None


@pytest.mark.asyncio
async def test_persist_tag_writes_when_missing_and_updates_card():
    tagger, anki, _ = _tagger()
    card = _card(tags=[])
    await tagger._persist_tag(card, "formal")
    anki.update_card_tags.assert_awaited_once_with(1, ["sensei:formal"])
    assert "sensei:formal" in card.tags


@pytest.mark.asyncio
async def test_persist_tag_skips_when_already_present():
    tagger, anki, _ = _tagger()
    card = _card(tags=["sensei:formal"])
    await tagger._persist_tag(card, "formal")
    anki.update_card_tags.assert_not_awaited()
```

- [ ] **Step 3: Run the test to confirm it fails**

```
uv run pytest tests/word_service/test_card_tagger.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.word_service.card_tagger'`.

- [ ] **Step 4: Create the `CardTagger` module**

Create `src/word_service/card_tagger.py`:

```python
import asyncio
import logging
from dataclasses import dataclass

from src.anki.card_data import CardData
from src.anki.client import AnkiClient
from src.word_service.word_classifier import WordClassifier

logger = logging.getLogger(__name__)

TAG_PREFIX = "sensei:"
FREQUENCIES = frozenset({"common", "rare", "obsolete"})
REGISTERS = frozenset({"formal", "informal", "slang", "literary", "neutral"})
ACADEMICS = frozenset({"academic"})  # only the positive case is tagged


class BatchAlreadyRunningError(RuntimeError):
    """Raised when classify_local_all / classify_register_all is invoked while
    another batch is already in flight."""


@dataclass
class LocalDelta:
    frequency_added: bool
    academic_added: bool


@dataclass
class RegisterDelta:
    register_added: bool
    failed: bool   # LLM call failed; tag not written


@dataclass
class LocalBatchStats:
    cards_scanned: int = 0
    frequency_added: int = 0
    academic_added: int = 0
    write_failures: int = 0


@dataclass
class RegisterBatchStats:
    cards_scanned: int = 0
    register_added: int = 0
    register_failures: int = 0
    write_failures: int = 0


class CardTagger:
    """Owns all reads and writes of `sensei:*` tags on Anki cards.
    Quiz flow calls `classify(card)`; daily-job / /retag call
    `classify_local_all` and `classify_register_all`."""

    def __init__(self, anki: AnkiClient, classifier: WordClassifier):
        self._anki = anki
        self._classifier = classifier
        self._batch_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._batch_lock.locked()

    @staticmethod
    def _cached(card: CardData, values: frozenset[str]) -> str | None:
        """Return the `sensei:<value>` tag on `card.tags` whose value is in `values`, or None."""
        for tag in card.tags:
            if tag.startswith(TAG_PREFIX):
                value = tag.removeprefix(TAG_PREFIX)
                if value in values:
                    return value
        return None

    async def _persist_tag(self, card: CardData, value: str) -> None:
        """Write `sensei:<value>` to the note and reflect it in the in-memory `card.tags`."""
        tag = f"{TAG_PREFIX}{value}"
        if tag not in card.tags:
            await self._anki.update_card_tags(card.card_id, [tag])
            card.tags.append(tag)
```

- [ ] **Step 5: Run the helper tests**

```
uv run pytest tests/word_service/test_card_tagger.py -v
```

Expected: 6 passed.

- [ ] **Step 6: Remove the constants from `word_classifier.py`**

Modify `src/word_service/word_classifier.py`. Delete these four lines near the top:

```python
TAG_PREFIX = "sensei:"
FREQUENCIES = frozenset({"common", "rare", "obsolete"})
REGISTERS = frozenset({"formal", "informal", "slang", "literary", "neutral"})
ACADEMICS = frozenset({"academic"})  # only the positive case is tagged
```

(The `_bucket` function and `WordClassifier` class stay exactly as they are.)

- [ ] **Step 7: Run the existing word_classifier tests to make sure removing the constants didn't break them**

```
uv run pytest tests/word_service/test_word_classifier.py -v
```

Expected: 5 passed. (Those tests don't import the constants — they only use `WordClassifier`.)

- [ ] **Step 8: Run state_machine tests — they currently import the constants from word_classifier**

```
uv run pytest tests/agent/test_state_machine.py -v
```

Expected: this WILL fail with ImportError on the constants. That's fine — Task 5/6 fixes state_machine.py. Note the failure but proceed.

- [ ] **Step 9: Lint + format**

```
uv run ruff check src/word_service/ tests/word_service/test_card_tagger.py
uv run ruff format src/word_service/ tests/word_service/test_card_tagger.py
```

Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add src/word_service/card_tagger.py src/word_service/word_classifier.py tests/word_service/test_card_tagger.py
git commit -m "feat(tagger): add CardTagger foundation; move tag vocab out of word_classifier"
```

(State machine tests will be broken until Task 5; that's the natural consequence of moving the constants. It's a tiny window during a single planned refactor.)

---

## Task 4: `classify_local(card)` and `classify_register(card)` per-card methods

**Files:**
- Modify: `src/word_service/card_tagger.py`
- Modify: `tests/word_service/test_card_tagger.py`

- [ ] **Step 1: Append the failing tests**

Append to `tests/word_service/test_card_tagger.py`:

```python
@pytest.mark.asyncio
async def test_classify_local_writes_missing_frequency_and_academic_no_llm():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="common")
    classifier.is_academic = MagicMock(return_value=True)
    classifier.register = AsyncMock()  # must NOT be called
    tagger = CardTagger(anki, classifier)
    card = _card(tags=[])

    delta = await tagger.classify_local(card)

    assert delta.frequency_added is True
    assert delta.academic_added is True
    assert {"sensei:common", "sensei:academic"} <= set(card.tags)
    classifier.register.assert_not_awaited()


@pytest.mark.asyncio
async def test_classify_local_skips_already_tagged_axes():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="common")
    classifier.is_academic = MagicMock(return_value=False)
    tagger = CardTagger(anki, classifier)
    card = _card(tags=["sensei:rare"])  # frequency already cached as rare

    delta = await tagger.classify_local(card)

    assert delta.frequency_added is False
    assert delta.academic_added is False
    anki.update_card_tags.assert_not_awaited()


@pytest.mark.asyncio
async def test_classify_local_omits_academic_tag_when_negative():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="common")
    classifier.is_academic = MagicMock(return_value=False)
    tagger = CardTagger(anki, classifier)
    card = _card(tags=[])

    delta = await tagger.classify_local(card)

    assert delta.frequency_added is True
    assert delta.academic_added is False
    assert "sensei:common" in card.tags
    assert "sensei:academic" not in card.tags


@pytest.mark.asyncio
async def test_classify_register_writes_when_missing():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.register = AsyncMock(return_value="formal")
    tagger = CardTagger(anki, classifier)
    card = _card(tags=[])

    delta = await tagger.classify_register(card)

    assert delta.register_added is True
    assert delta.failed is False
    assert "sensei:formal" in card.tags


@pytest.mark.asyncio
async def test_classify_register_skips_when_cached():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.register = AsyncMock()
    tagger = CardTagger(anki, classifier)
    card = _card(tags=["sensei:informal"])

    delta = await tagger.classify_register(card)

    assert delta.register_added is False
    assert delta.failed is False
    classifier.register.assert_not_awaited()


@pytest.mark.asyncio
async def test_classify_register_records_failure_on_llm_none():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.register = AsyncMock(return_value=None)
    tagger = CardTagger(anki, classifier)
    card = _card(tags=[])

    delta = await tagger.classify_register(card)

    assert delta.register_added is False
    assert delta.failed is True
    anki.update_card_tags.assert_not_awaited()
```

- [ ] **Step 2: Run the tests to confirm they fail**

```
uv run pytest tests/word_service/test_card_tagger.py -v -k "classify_local or classify_register"
```

Expected: 6 failures — `AttributeError: 'CardTagger' object has no attribute 'classify_local'` etc.

- [ ] **Step 3: Implement the two methods**

Append to `src/word_service/card_tagger.py` inside the `CardTagger` class (after `_persist_tag`):

```python
    async def classify_local(self, card: CardData) -> LocalDelta:
        """Frequency + academic. No LLM. Idempotent: only writes missing tags."""
        frequency_added = False
        academic_added = False

        if self._cached(card, FREQUENCIES) is None:
            value = self._classifier.frequency(card.front)
            await self._persist_tag(card, value)
            frequency_added = True

        if self._cached(card, ACADEMICS) is None:
            if self._classifier.is_academic(card.front):
                await self._persist_tag(card, "academic")
                academic_added = True

        return LocalDelta(frequency_added=frequency_added, academic_added=academic_added)

    async def classify_register(self, card: CardData) -> RegisterDelta:
        """Register only. Calls the LLM through WordClassifier; on failure
        leaves the card untouched so a future run retries."""
        if self._cached(card, REGISTERS) is not None:
            return RegisterDelta(register_added=False, failed=False)

        value = await self._classifier.register(card)
        if value is None:
            return RegisterDelta(register_added=False, failed=True)

        await self._persist_tag(card, value)
        return RegisterDelta(register_added=True, failed=False)
```

- [ ] **Step 4: Run the tests to confirm they pass**

```
uv run pytest tests/word_service/test_card_tagger.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Lint + format**

```
uv run ruff check src/word_service/card_tagger.py tests/word_service/test_card_tagger.py
uv run ruff format src/word_service/card_tagger.py tests/word_service/test_card_tagger.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/word_service/card_tagger.py tests/word_service/test_card_tagger.py
git commit -m "feat(tagger): add classify_local + classify_register per-card methods"
```

---

## Task 5: `CardTagger.classify(card)` quiz hot path + state-machine refactor

**Why combined into one task:** introducing `classify` and converting `QuizStateMachine` to use it are tightly coupled. The state-machine tests have been broken since Task 3; this task fixes them.

**Files:**
- Modify: `src/word_service/card_tagger.py` — add `classify(card)`
- Modify: `tests/word_service/test_card_tagger.py` — test `classify(card)`
- Modify: `src/agent/state_machine.py` — switch to `CardTagger`, drop the tag helpers + constants imports + `WordClassifier` dep
- Modify: `tests/agent/test_state_machine.py` — update `_setup` to inject `CardTagger`

- [ ] **Step 1: Append the failing tests for `classify(card)`**

Append to `tests/word_service/test_card_tagger.py`:

```python
@pytest.mark.asyncio
async def test_classify_returns_frequency_and_register_on_cold_card():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="common")
    classifier.is_academic = MagicMock(return_value=False)
    classifier.register = AsyncMock(return_value="neutral")
    tagger = CardTagger(anki, classifier)
    card = _card(tags=[])

    frequency, register = await tagger.classify(card)

    assert frequency == "common"
    assert register == "neutral"
    assert "sensei:common" in card.tags
    assert "sensei:neutral" in card.tags


@pytest.mark.asyncio
async def test_classify_returns_none_register_when_llm_fails():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="rare")
    classifier.is_academic = MagicMock(return_value=False)
    classifier.register = AsyncMock(return_value=None)
    tagger = CardTagger(anki, classifier)
    card = _card(tags=[])

    frequency, register = await tagger.classify(card)

    assert frequency == "rare"
    assert register is None
    assert "sensei:rare" in card.tags
    assert all(not t.startswith("sensei:") or t == "sensei:rare" for t in card.tags)


@pytest.mark.asyncio
async def test_classify_uses_cache_for_all_three_axes():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock()
    classifier.is_academic = MagicMock()
    classifier.register = AsyncMock()
    tagger = CardTagger(anki, classifier)
    card = _card(tags=["sensei:common", "sensei:formal", "sensei:academic"])

    frequency, register = await tagger.classify(card)

    assert frequency == "common"
    assert register == "formal"
    classifier.frequency.assert_not_called()
    classifier.is_academic.assert_not_called()
    classifier.register.assert_not_awaited()
    anki.update_card_tags.assert_not_awaited()
```

- [ ] **Step 2: Run the tests to confirm they fail**

```
uv run pytest tests/word_service/test_card_tagger.py -v -k "test_classify_returns or test_classify_uses_cache"
```

Expected: 3 failures — `'CardTagger' object has no attribute 'classify'`.

- [ ] **Step 3: Implement `classify`**

Append to `src/word_service/card_tagger.py` inside the `CardTagger` class (above `classify_local`):

```python
    async def classify(self, card: CardData) -> tuple[str, str | None]:
        """Quiz hot path. Applies all 3 axes if missing.
        Returns (frequency, register). `register` is None if its LLM call failed."""
        local = await self.classify_local(card)
        # Frequency is always derivable from cache after classify_local (it ran or was already there);
        # read it back so callers don't need to compute it themselves.
        frequency = self._cached(card, FREQUENCIES)
        assert frequency is not None, "classify_local must leave a frequency tag"
        _ = local  # local.academic_added/frequency_added are bookkeeping, not returned

        register_delta = await self.classify_register(card)
        register = self._cached(card, REGISTERS) if not register_delta.failed else None
        return frequency, register
```

- [ ] **Step 4: Run the tests to confirm they pass**

```
uv run pytest tests/word_service/test_card_tagger.py -v
```

Expected: 15 passed.

- [ ] **Step 5: Refactor `src/agent/state_machine.py`**

Apply this set of edits to `src/agent/state_machine.py`:

1. Replace the `WordClassifier` import block (lines 13–19) with:

```python
from src.word_service.card_tagger import CardTagger
```

(Drop `ACADEMICS`, `FREQUENCIES`, `REGISTERS`, `TAG_PREFIX`, `WordClassifier` — they're all unused after this refactor.)

2. Update the constructor signature and body. Replace lines 47–65:

```python
    def __init__(
        self,
        anki_client: AnkiClient,
        anki_syncer: AnkiSyncer,
        agent: GeminiAgent,
        tagger: CardTagger,
        prefs_store: UserPrefsStore,
        errors_store: ErrorRecordStore,
        sessions_store: ConversationSessionStore,
    ):
        self._anki = anki_client
        self._syncer = anki_syncer
        self._agent = agent
        self._tagger = tagger
        self._prefs = prefs_store
        self._errors = errors_store
        self._sessions = sessions_store
        self._active: _ActiveSession | None = None
        self._user_id: int = 0
```

3. Replace `_begin_card` (currently 293–314) with:

```python
    async def _begin_card(self, card: CardData) -> QuizResult:
        frequency, register = await self._tagger.classify(card)
        recent_errors = self._errors.recent_for_card(card.card_id)
        last_summary = self._sessions.last_summary_for_card(card.card_id)
        question = await self._agent.generate_question(
            card,
            frequency,
            retry_count=0,
            recent_errors=recent_errors,
            conversation_summary=last_summary,
            register=register,
        )
        db_id = self._sessions.create(card.card_id)
        self._active = _ActiveSession(
            card=card,
            frequency=frequency,
            db_session_id=db_id,
            current_question=question,
        )
        return question
```

4. Replace `_next_question` (currently 316–334) with:

```python
    async def _next_question(
        self, session: _ActiveSession, forced_type: str | None = None
    ) -> QuizResult:
        frequency, register = await self._tagger.classify(session.card)
        recent_errors = self._errors.recent_for_card(session.card.card_id)
        last_summary = self._sessions.last_summary_for_card(session.card.card_id)
        question = await self._agent.generate_question(
            session.card,
            frequency,
            retry_count=session.attempt_count,
            recent_errors=recent_errors,
            conversation_summary=last_summary,
            forced_type=forced_type,
            register=register,
        )
        session.current_question = question
        return question
```

5. **Delete** the bottom block (lines 363–408): `_cached`, `_persist_tag`, `_classify_frequency`, `_classify_register`, `_classify_academic`. They are now part of `CardTagger`.

- [ ] **Step 6: Update `tests/agent/test_state_machine.py::_setup`**

Modify the helper around line 19–47. Replace it with:

```python
def _setup(zipf_fn=lambda w, lang: 5.5):
    """Default zipf_fn returns 5.5 → 'common'. Override per-test to force 'rare'/'obsolete'."""
    fd, tmp_db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{tmp_db}")
    SQLModel.metadata.create_all(engine)
    prefs = UserPrefsStore(engine)
    errors = ErrorRecordStore(engine)
    sessions = ConversationSessionStore(engine)

    agent = MagicMock(spec=GeminiAgent)
    anki = MagicMock()
    anki.get_due_cards = AsyncMock(
        return_value=[
            CardData(card_id=1, front="run", back="走る", tags=[], deck_name="EN")
        ]
    )
    anki.answer_card = AsyncMock()
    anki.update_card_tags = AsyncMock()
    anki.get_due_count = AsyncMock(return_value=0)
    anki.get_deck_names = AsyncMock(return_value=[])

    syncer = MagicMock()
    syncer.async_sync = AsyncMock()

    classifier = WordClassifier(agent, zipf_fn=zipf_fn)
    tagger = CardTagger(anki, classifier)

    sm = QuizStateMachine(anki, syncer, agent, tagger, prefs, errors, sessions)
    return sm, agent, anki, engine, tmp_db
```

And add the import at the top of the test file:

```python
from src.word_service.card_tagger import CardTagger
```

- [ ] **Step 7: Run all affected tests**

```
uv run pytest tests/agent/test_state_machine.py tests/word_service/ -v
```

Expected: all pass. If any existing state-machine test was reaching into `_classify_*` or `_cached` / `_persist_tag` directly (none should), the test must be rewritten to mock `tagger.classify` instead.

- [ ] **Step 8: Lint + format**

```
uv run ruff check src/agent/state_machine.py src/word_service/card_tagger.py tests/agent/test_state_machine.py tests/word_service/test_card_tagger.py
uv run ruff format src/agent/state_machine.py src/word_service/card_tagger.py tests/agent/test_state_machine.py tests/word_service/test_card_tagger.py
```

Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add src/agent/state_machine.py src/word_service/card_tagger.py tests/agent/test_state_machine.py tests/word_service/test_card_tagger.py
git commit -m "refactor(state-machine): delegate tag I/O to CardTagger"
```

---

## Task 6: `classify_local_all` batch

**Files:**
- Modify: `src/word_service/card_tagger.py`
- Modify: `tests/word_service/test_card_tagger.py`

- [ ] **Step 1: Append the failing tests**

Append to `tests/word_service/test_card_tagger.py`:

```python
import asyncio as _asyncio  # avoid shadowing the test file's existing imports
from src.word_service.card_tagger import (
    BatchAlreadyRunningError,
    LocalBatchStats,
)


def _tagger_with_cards(card_map: dict[int, CardData], classifier=None):
    """Build a CardTagger whose AnkiClient returns the supplied cards."""
    anki = MagicMock()
    anki.get_all_card_ids = AsyncMock(return_value=list(card_map.keys()))
    anki.get_card = AsyncMock(side_effect=lambda cid: card_map[cid])
    anki.update_card_tags = AsyncMock()
    if classifier is None:
        classifier = MagicMock()
        classifier.frequency = MagicMock(return_value="common")
        classifier.is_academic = MagicMock(return_value=False)
        classifier.register = AsyncMock(return_value="neutral")
    return CardTagger(anki, classifier), anki, classifier


@pytest.mark.asyncio
async def test_classify_local_all_iterates_all_cards_and_accumulates_stats():
    cards = {
        1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d"),
        2: CardData(card_id=2, front="b", back="", tags=["sensei:rare"], deck_name="d"),
        3: CardData(card_id=3, front="c", back="", tags=[], deck_name="d"),
    }
    classifier = MagicMock()
    classifier.frequency = MagicMock(side_effect=lambda w: "common")
    classifier.is_academic = MagicMock(side_effect=lambda w: w == "a")
    classifier.register = AsyncMock()  # MUST NOT be called
    tagger, _, _ = _tagger_with_cards(cards, classifier)

    stats = await tagger.classify_local_all()

    assert isinstance(stats, LocalBatchStats)
    assert stats.cards_scanned == 3
    assert stats.frequency_added == 2  # card 2 already had sensei:rare
    assert stats.academic_added == 1   # only card 1 is academic
    assert stats.write_failures == 0
    classifier.register.assert_not_awaited()


@pytest.mark.asyncio
async def test_classify_local_all_swallows_per_card_exceptions():
    cards = {
        1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d"),
        2: CardData(card_id=2, front="b", back="", tags=[], deck_name="d"),
    }
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="common")
    classifier.is_academic = MagicMock(return_value=False)
    tagger, anki, _ = _tagger_with_cards(cards, classifier)

    async def write(card_id, tags):
        if card_id == 1:
            raise RuntimeError("disk full")

    anki.update_card_tags = AsyncMock(side_effect=write)

    stats = await tagger.classify_local_all()

    assert stats.cards_scanned == 2
    assert stats.write_failures == 1
    assert stats.frequency_added == 1   # card 2 succeeded


@pytest.mark.asyncio
async def test_classify_local_all_raises_when_batch_already_running():
    cards = {1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d")}
    tagger, _, _ = _tagger_with_cards(cards)

    async def gate(card_id, tags):
        await _asyncio.sleep(0.01)  # hold the batch open so the second call sees the lock

    tagger._anki.update_card_tags = AsyncMock(side_effect=gate)

    task = _asyncio.create_task(tagger.classify_local_all())
    await _asyncio.sleep(0)  # let `task` enter the lock
    with pytest.raises(BatchAlreadyRunningError):
        await tagger.classify_local_all()
    await task
```

- [ ] **Step 2: Run the tests to confirm they fail**

```
uv run pytest tests/word_service/test_card_tagger.py -v -k "classify_local_all"
```

Expected: 3 failures — `'CardTagger' object has no attribute 'classify_local_all'`.

- [ ] **Step 3: Implement `classify_local_all`**

Append to `CardTagger` (in `src/word_service/card_tagger.py`):

```python
    async def classify_local_all(self) -> LocalBatchStats:
        """Iterate every card in the collection, ensure frequency + academic
        tags are present. No LLM. Raises BatchAlreadyRunningError if another
        batch is already in flight."""
        if self._batch_lock.locked():
            raise BatchAlreadyRunningError()
        stats = LocalBatchStats()
        async with self._batch_lock:
            card_ids = await self._anki.get_all_card_ids()
            for card_id in card_ids:
                try:
                    card = await self._anki.get_card(card_id)
                    delta = await self.classify_local(card)
                    stats.cards_scanned += 1
                    if delta.frequency_added:
                        stats.frequency_added += 1
                    if delta.academic_added:
                        stats.academic_added += 1
                except Exception:
                    logger.warning(
                        "classify_local_all: card %s failed; continuing", card_id,
                        exc_info=True,
                    )
                    stats.cards_scanned += 1
                    stats.write_failures += 1
        return stats
```

- [ ] **Step 4: Run the tests to confirm they pass**

```
uv run pytest tests/word_service/test_card_tagger.py -v -k "classify_local_all"
```

Expected: 3 passed.

- [ ] **Step 5: Lint + format**

```
uv run ruff check src/word_service/card_tagger.py tests/word_service/test_card_tagger.py
uv run ruff format src/word_service/card_tagger.py tests/word_service/test_card_tagger.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/word_service/card_tagger.py tests/word_service/test_card_tagger.py
git commit -m "feat(tagger): add classify_local_all batch entry point"
```

---

## Task 7: `classify_register_all` batch

**Files:**
- Modify: `src/word_service/card_tagger.py`
- Modify: `tests/word_service/test_card_tagger.py`

- [ ] **Step 1: Append the failing tests**

Append to `tests/word_service/test_card_tagger.py`:

```python
from src.word_service.card_tagger import RegisterBatchStats


@pytest.mark.asyncio
async def test_classify_register_all_writes_register_for_missing_cards():
    cards = {
        1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d"),
        2: CardData(card_id=2, front="b", back="", tags=["sensei:formal"], deck_name="d"),
    }
    classifier = MagicMock()
    classifier.register = AsyncMock(return_value="neutral")
    tagger, _, _ = _tagger_with_cards(cards, classifier)

    stats = await tagger.classify_register_all()

    assert isinstance(stats, RegisterBatchStats)
    assert stats.cards_scanned == 2
    assert stats.register_added == 1   # card 2 already had sensei:formal
    assert stats.register_failures == 0
    assert stats.write_failures == 0


@pytest.mark.asyncio
async def test_classify_register_all_counts_llm_failures_without_writing():
    cards = {1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d")}
    classifier = MagicMock()
    classifier.register = AsyncMock(return_value=None)
    tagger, anki, _ = _tagger_with_cards(cards, classifier)

    stats = await tagger.classify_register_all()

    assert stats.register_added == 0
    assert stats.register_failures == 1
    anki.update_card_tags.assert_not_awaited()


@pytest.mark.asyncio
async def test_classify_register_all_raises_when_batch_already_running():
    cards = {1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d")}
    classifier = MagicMock()

    async def slow_register(card):
        await _asyncio.sleep(0.01)
        return "neutral"

    classifier.register = AsyncMock(side_effect=slow_register)
    tagger, _, _ = _tagger_with_cards(cards, classifier)

    task = _asyncio.create_task(tagger.classify_register_all())
    await _asyncio.sleep(0)
    with pytest.raises(BatchAlreadyRunningError):
        await tagger.classify_register_all()
    await task


@pytest.mark.asyncio
async def test_local_and_register_batches_share_one_lock():
    """Starting local_all then immediately register_all raises — the lock is shared."""
    cards = {1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d")}
    tagger, anki, _ = _tagger_with_cards(cards)

    async def slow_write(card_id, tags):
        await _asyncio.sleep(0.01)

    anki.update_card_tags = AsyncMock(side_effect=slow_write)

    task = _asyncio.create_task(tagger.classify_local_all())
    await _asyncio.sleep(0)
    with pytest.raises(BatchAlreadyRunningError):
        await tagger.classify_register_all()
    await task
```

- [ ] **Step 2: Run the tests to confirm they fail**

```
uv run pytest tests/word_service/test_card_tagger.py -v -k "classify_register_all or local_and_register"
```

Expected: 4 failures.

- [ ] **Step 3: Implement `classify_register_all`**

Append to `CardTagger`:

```python
    async def classify_register_all(self) -> RegisterBatchStats:
        """Iterate every card and ensure a register tag is present.
        Uses the LLM via WordClassifier. Shares _batch_lock with classify_local_all."""
        if self._batch_lock.locked():
            raise BatchAlreadyRunningError()
        stats = RegisterBatchStats()
        async with self._batch_lock:
            card_ids = await self._anki.get_all_card_ids()
            for card_id in card_ids:
                try:
                    card = await self._anki.get_card(card_id)
                    delta = await self.classify_register(card)
                    stats.cards_scanned += 1
                    if delta.register_added:
                        stats.register_added += 1
                    if delta.failed:
                        stats.register_failures += 1
                except Exception:
                    logger.warning(
                        "classify_register_all: card %s failed; continuing", card_id,
                        exc_info=True,
                    )
                    stats.cards_scanned += 1
                    stats.write_failures += 1
        return stats
```

- [ ] **Step 4: Run the tests to confirm they pass**

```
uv run pytest tests/word_service/test_card_tagger.py -v
```

Expected: all tests pass (count grew across tasks; the absolute number is whatever's in the file now).

- [ ] **Step 5: Lint + format**

```
uv run ruff check src/word_service/card_tagger.py tests/word_service/test_card_tagger.py
uv run ruff format src/word_service/card_tagger.py tests/word_service/test_card_tagger.py
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/word_service/card_tagger.py tests/word_service/test_card_tagger.py
git commit -m "feat(tagger): add classify_register_all batch entry point"
```

---

## Task 8: `/retag` Telegram handler

**Files:**
- Create: `tests/bot/__init__.py` (empty)
- Create: `tests/bot/test_retag_handler.py`
- Modify: `src/bot/handlers.py`
- Modify: `src/bot/app.py`

- [ ] **Step 1: Create the test package init**

```bash
ls tests/bot/ 2>/dev/null || mkdir tests/bot
```

Write empty `tests/bot/__init__.py`.

- [ ] **Step 2: Write the failing handler tests**

Create `tests/bot/test_retag_handler.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers import make_handlers
from src.word_service.card_tagger import (
    BatchAlreadyRunningError,
    LocalBatchStats,
    RegisterBatchStats,
)


def _build_handlers(tagger, syncer=None):
    sm = MagicMock()
    sm.has_active_session = MagicMock(return_value=False)
    if syncer is None:
        syncer = MagicMock()
        syncer.try_sync = AsyncMock(return_value=True)
    anki = MagicMock()
    prefs = MagicMock()
    return make_handlers(sm, syncer, anki, prefs, tagger), syncer


def _update_and_ctx():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 555
    ctx = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return update, ctx


@pytest.mark.asyncio
async def test_retag_replies_already_running_when_tagger_is_running():
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: True)
    handlers, _ = _build_handlers(tagger)
    update, ctx = _update_and_ctx()

    await handlers["retag"](update, ctx)

    update.message.reply_text.assert_awaited_once()
    assert "already running" in update.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_retag_runs_full_pipeline_and_reports_stats():
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: False)
    tagger.classify_local_all = AsyncMock(
        return_value=LocalBatchStats(cards_scanned=2, frequency_added=1, academic_added=1)
    )
    tagger.classify_register_all = AsyncMock(
        return_value=RegisterBatchStats(cards_scanned=2, register_added=2)
    )
    handlers, syncer = _build_handlers(tagger)
    update, ctx = _update_and_ctx()

    await handlers["retag"](update, ctx)

    # Let the spawned create_task finish
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        await t

    update.message.reply_text.assert_awaited_once()
    assert "started" in update.message.reply_text.await_args.args[0].lower()
    tagger.classify_local_all.assert_awaited_once()
    tagger.classify_register_all.assert_awaited_once()
    assert syncer.try_sync.await_count == 2
    ctx.bot.send_message.assert_awaited_once()
    body = ctx.bot.send_message.await_args.kwargs.get("text") or ctx.bot.send_message.await_args.args[1]
    assert "frequency=1" in body and "register=2" in body


@pytest.mark.asyncio
async def test_retag_continues_after_first_sync_failure():
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: False)
    tagger.classify_local_all = AsyncMock(return_value=LocalBatchStats(cards_scanned=1))
    tagger.classify_register_all = AsyncMock(return_value=RegisterBatchStats(cards_scanned=1))
    syncer = MagicMock()
    syncer.try_sync = AsyncMock(side_effect=[False, True])
    handlers, _ = _build_handlers(tagger, syncer)
    update, ctx = _update_and_ctx()

    await handlers["retag"](update, ctx)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        await t

    # Register batch still ran despite first sync failure
    tagger.classify_register_all.assert_awaited_once()
    # Final message mentions the failed sync
    final = ctx.bot.send_message.await_args.kwargs.get("text") or ctx.bot.send_message.await_args.args[1]
    assert "sync" in final.lower() and "failed" in final.lower()


@pytest.mark.asyncio
async def test_retag_handles_batch_already_running_race_inside_task():
    """Backstop: even if the up-front is_running check missed it."""
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: False)
    tagger.classify_local_all = AsyncMock(side_effect=BatchAlreadyRunningError())
    handlers, _ = _build_handlers(tagger)
    update, ctx = _update_and_ctx()

    await handlers["retag"](update, ctx)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        await t

    final = ctx.bot.send_message.await_args.kwargs.get("text") or ctx.bot.send_message.await_args.args[1]
    assert "already running" in final.lower()
```

- [ ] **Step 3: Run the tests to confirm they fail**

```
uv run pytest tests/bot/test_retag_handler.py -v
```

Expected: 4 failures — `make_handlers` doesn't accept a `tagger` parameter, and there is no `"retag"` key in the returned dict.

- [ ] **Step 4: Update `make_handlers` signature in `src/bot/handlers.py`**

Modify the function signature (around line 29):

```python
def make_handlers(
    sm: QuizStateMachine,
    syncer: AnkiSyncer,
    anki: AnkiClient,
    prefs: UserPrefsStore,
    tagger: CardTagger,
) -> dict:
```

Add the import at the top:

```python
from src.word_service.card_tagger import (
    BatchAlreadyRunningError,
    CardTagger,
    LocalBatchStats,
    RegisterBatchStats,
)
```

- [ ] **Step 5: Add `retag_handler` + `_format_stats`**

Inside `make_handlers`, alongside the other handler functions (before the `return {...}` block):

```python
    async def retag_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if tagger.is_running:
            await update.message.reply_text(
                "A retag job is already running — try again later."
            )
            return
        await update.message.reply_text("Retag started, will notify when done.")
        asyncio.create_task(_run_retag(update.effective_chat.id, context.bot))

    async def _run_retag(chat_id: int, bot) -> None:
        try:
            local = await tagger.classify_local_all()
            sync1_ok = await syncer.try_sync("after local pass")
            register = await tagger.classify_register_all()
            sync2_ok = await syncer.try_sync("after register pass")
            await bot.send_message(
                chat_id, text=_format_stats(local, register, sync1_ok, sync2_ok)
            )
        except BatchAlreadyRunningError:
            await bot.send_message(
                chat_id, text="A retag job is already running — try again later."
            )
        except Exception as e:
            logger.exception("retag failed")
            await bot.send_message(chat_id, text=f"Retag failed: {e}")
```

Add `asyncio` to the imports at the top of `src/bot/handlers.py`:

```python
import asyncio
```

Add the formatter near the bottom of the file (next to the other module-level helpers like `_format_question`):

```python
def _format_stats(
    local: "LocalBatchStats",
    register: "RegisterBatchStats",
    sync1_ok: bool,
    sync2_ok: bool,
) -> str:
    lines = [
        "Done.",
        f"Local: frequency={local.frequency_added} academic={local.academic_added}.",
        f"Register: added={register.register_added} llm_failures={register.register_failures}.",
        f"Write failures: {local.write_failures + register.write_failures}.",
    ]
    if not sync1_ok or not sync2_ok:
        lines.append(
            f"Sync: local={'OK' if sync1_ok else 'FAILED'} "
            f"register={'OK' if sync2_ok else 'FAILED'}"
        )
    return "\n".join(lines)
```

Register the handler in the return dict at the bottom of `make_handlers`:

```python
        "retag": retag_handler,
```

(Append it inside the dict literal that the function returns, anywhere; convention is at the end.)

- [ ] **Step 6: Register the command in `src/bot/app.py`**

Add to `_BOT_COMMANDS`:

```python
    BotCommand("retag", "Backfill missing sensei:* tags on all cards"),
```

Add the handler registration after the other `CommandHandler` lines:

```python
    app.add_handler(CommandHandler("retag", handlers["retag"], filters=user_filter))
```

- [ ] **Step 7: Run the handler tests**

```
uv run pytest tests/bot/test_retag_handler.py -v
```

Expected: 4 passed.

- [ ] **Step 8: Run the full test suite to verify nothing else regressed**

```
uv run pytest -q
```

Expected: all pass.

- [ ] **Step 9: Lint + format**

```
uv run ruff check src/bot/handlers.py src/bot/app.py tests/bot/test_retag_handler.py
uv run ruff format src/bot/handlers.py src/bot/app.py tests/bot/test_retag_handler.py
```

Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add src/bot/handlers.py src/bot/app.py tests/bot/test_retag_handler.py tests/bot/__init__.py
git commit -m "feat(bot): add /retag command and handler with two-phase orchestration"
```

---

## Task 9: `main.py` wiring + daily JobQueue + config defaults

**Files:**
- Modify: `src/main.py`
- Modify: `src/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Flip the default in `src/config.py`**

Around line 54 in `load_settings`:

```python
        scheduler_daily_hour=int(os.environ.get("SCHEDULER_DAILY_HOUR", "3")),
```

(Change `"8"` to `"3"`.)

- [ ] **Step 2: Update `.env.example`**

Find line 22 (the commented default). Replace the surrounding block so it reads:

```
# Hour-of-day (0–23) for the daily tag-backfill job
# SCHEDULER_DAILY_HOUR=3
```

(Update both the value and the preceding comment if there is one.)

- [ ] **Step 3: Wire `CardTagger` and the daily job in `src/main.py`**

Add imports at the top:

```python
from datetime import time
import logging

from src.word_service.card_tagger import BatchAlreadyRunningError, CardTagger
```

(Some of these may already exist — only add what's missing.)

Replace the section from `classifier = WordClassifier(agent)` (around line 49) through `app.run_polling(...)` with:

```python
    classifier = WordClassifier(agent)
    tagger = CardTagger(anki_client, classifier)
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
        state_machine, anki_syncer, anki_client, prefs_store, tagger
    )
    app = build_app(settings.telegram_token, settings.allowed_user_ids, handlers)

    async def _daily_retag(context) -> None:
        try:
            local = await tagger.classify_local_all()
            await anki_syncer.try_sync("after local pass")
            register = await tagger.classify_register_all()
            await anki_syncer.try_sync("after register pass")
            logging.info(
                "daily retag done: local=%s register=%s", local, register
            )
        except BatchAlreadyRunningError:
            logging.warning("daily retag skipped: another batch already running")

    app.job_queue.run_daily(
        _daily_retag,
        time=time(hour=settings.scheduler_daily_hour),
        name="daily_retag",
    )

    app.run_polling(allowed_updates=["message", "callback_query"])
```

- [ ] **Step 4: Run the full test suite**

```
uv run pytest -q
```

Expected: all pass.

- [ ] **Step 5: Sanity-check that `python -m src.main` imports without error**

This does NOT start the bot — it only confirms the wiring compiles.

```
uv run python -c "import importlib; importlib.import_module('src.main')"
```

Expected: no output, exit code 0. (If it complains about missing env vars, the import itself didn't fail — `load_settings` runs only inside `main()`.)

- [ ] **Step 6: Lint + format**

```
uv run ruff check src/main.py src/config.py
uv run ruff format src/main.py src/config.py
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/main.py src/config.py .env.example
git commit -m "feat(main): wire CardTagger + daily retag JobQueue at 03:00"
```

---

## Task 10: Final verification

**No code changes.** Confirm everything works as a whole before declaring done.

- [ ] **Step 1: Lint everything**

```
uv run ruff check src/ tests/
```

Expected: no errors.

- [ ] **Step 2: Format check**

```
uv run ruff format --check src/ tests/
```

Expected: no errors.

- [ ] **Step 3: Run the full test suite**

```
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Security audit**

```
uv run pip-audit
```

Expected: no new advisories (the existing baseline should be unchanged — we added no deps).

- [ ] **Step 5: Manual smoke test instructions**

Document for the user (do not run automatically):

> 1. Set `SCHEDULER_DAILY_HOUR` in `.env` to the current local hour + 1.
> 2. Run `uv run python -m src.main` and wait until the configured hour.
> 3. Confirm the log line `daily retag done: local=... register=...` appears.
> 4. In Telegram, send `/retag`. Expect "Retag started, will notify when done." immediately, followed (after the LLM pass completes) by a `Done. Local: ... Register: ... Write failures: ...` message.
> 5. Send `/retag` again before the first batch finishes — expect "A retag job is already running — try again later."
> 6. Open Anki desktop after the run, pick a previously-untagged card, confirm `sensei:common|rare|obsolete`, optionally `sensei:academic`, and `sensei:formal|informal|slang|literary|neutral` are present.

- [ ] **Step 6: No commit needed** — this task is verification only.

---

## Self-review notes

This section is the planner's notes for the executor — read once, then discard.

- Spec coverage: every "Modified:" file in the spec maps to a task above. New module → Task 3–7. `AnkiSyncer.try_sync` → Task 1. `AnkiClient` additions → Task 2. State-machine refactor → Task 5. Handler → Task 8. Composition → Task 9.
- The spec described `AnkiSyncer.try_sync` as wrapping a try/except around `async_sync`. The actual `AnkiSyncer.async_sync` returns `SyncResult(success, message)` rather than raising — so this plan's Task 1 implements `try_sync` by checking `result.success` (and still catching unexpected exceptions as a backstop). This is a faithful realisation of the spec's intent ("non-raising sync that reports success/failure") even though the implementation shape differs.
- Order discipline: Task 3 deliberately leaves `tests/agent/test_state_machine.py` broken (it imports the moved constants) for the lifetime of one commit. Task 5 fixes it. This is the smallest-blast-radius way to do the constant move without intermediate compat shims.
- Type/method consistency check: `LocalBatchStats`, `RegisterBatchStats`, `LocalDelta`, `RegisterDelta`, `BatchAlreadyRunningError`, `try_sync`, `get_all_card_ids`, `get_card`, `classify`, `classify_local`, `classify_register`, `classify_local_all`, `classify_register_all`, `is_running` — all names appear consistently in spec + plan.
- `_run_retag`'s final message format in Task 8 matches the spec's `_format_stats` description (lines added per axis; sync line only on failure).
