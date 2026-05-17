# Deck List & Select Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/decks` command that lists all Anki decks as inline buttons; selecting one persists the choice in SQLite (`data/sensei.db`) so subsequent `/quiz` sessions always filter cards to that deck.

**Architecture:** A new `UserPrefsStore` (`src/db/prefs.py`) owns SQLite reads/writes for per-user settings. `QuizEngine` delegates deck get/set to this store. `AnkiClient` gains `get_deck_names()` and a deck-filtered `get_due_cards()`. New `/decks` command and `deck_select:*` callback added to handlers; registered in `main.py`.

**Tech Stack:** Python built-in `sqlite3`, anki 24.11, python-telegram-bot 22.7, existing `run_in_executor` + `AnkiClient._collection_lock` pattern.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/db/__init__.py` | Create | Package marker |
| `src/db/prefs.py` | Create | `UserPrefsStore` — SQLite CRUD for user prefs |
| `src/config.py` | Modify | Add `prefs_db_path` setting |
| `src/anki/client.py` | Modify | Add `get_deck_names()`, deck filter in `get_due_cards()` |
| `src/quiz/engine.py` | Modify | Use `UserPrefsStore` for deck state, add `get_deck_names()` |
| `src/bot/keyboards.py` | Modify | Add `deck_list_keyboard()` |
| `src/bot/handlers.py` | Modify | Add `decks_command`, `deck_select_callback`, update help text |
| `src/main.py` | Modify | Init `UserPrefsStore`, pass to engine, register new handlers |

---

### Task 1: Create `src/db/prefs.py` — SQLite user prefs store

**Files:**
- Create: `src/db/__init__.py`
- Create: `src/db/prefs.py`
- Test: `tests/db/test_prefs.py`

- [ ] **Step 1: Write the failing test**

```bash
mkdir -p tests/db && touch tests/db/__init__.py
```

Create `tests/db/test_prefs.py`:

```python
import tempfile
import os
from src.db.prefs import UserPrefsStore


def _store():
    tmp = tempfile.mktemp(suffix=".db")
    return UserPrefsStore(tmp), tmp


def test_get_deck_returns_none_for_unknown_user():
    store, tmp = _store()
    assert store.get_deck(12345) is None
    os.unlink(tmp)


def test_set_and_get_deck():
    store, tmp = _store()
    store.set_deck(1, "Japanese::N5")
    assert store.get_deck(1) == "Japanese::N5"
    os.unlink(tmp)


def test_set_deck_none_clears_selection():
    store, tmp = _store()
    store.set_deck(1, "Japanese::N5")
    store.set_deck(1, None)
    assert store.get_deck(1) is None
    os.unlink(tmp)


def test_set_deck_overwrites_previous():
    store, tmp = _store()
    store.set_deck(1, "Deck A")
    store.set_deck(1, "Deck B")
    assert store.get_deck(1) == "Deck B"
    os.unlink(tmp)


def test_multiple_users_independent():
    store, tmp = _store()
    store.set_deck(1, "Deck A")
    store.set_deck(2, "Deck B")
    assert store.get_deck(1) == "Deck A"
    assert store.get_deck(2) == "Deck B"
    os.unlink(tmp)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/db/test_prefs.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.db.prefs'`

- [ ] **Step 3: Create the package and implement `UserPrefsStore`**

```bash
touch src/db/__init__.py
```

Create `src/db/prefs.py`:

```python
import sqlite3
from pathlib import Path


class UserPrefsStore:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS user_prefs "
                "(user_id INTEGER PRIMARY KEY, selected_deck TEXT)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def get_deck(self, user_id: int) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT selected_deck FROM user_prefs WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row[0] if row else None

    def set_deck(self, user_id: int, deck_name: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_prefs (user_id, selected_deck) VALUES (?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET selected_deck = excluded.selected_deck",
                (user_id, deck_name),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/db/test_prefs.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/db/__init__.py src/db/prefs.py tests/db/__init__.py tests/db/test_prefs.py
git commit -m "feat(db): add UserPrefsStore for SQLite-backed user preferences"
```

---

### Task 2: Add `prefs_db_path` to `src/config.py`

**Files:**
- Modify: `src/config.py`

- [ ] **Step 1: Add `prefs_db_path` field to `Settings` dataclass**

In `src/config.py`, the `Settings` dataclass currently ends at `max_cards_per_session`. Add the new field:

```python
@dataclass
class Settings:
    telegram_token: str
    ankiweb_email: str
    ankiweb_password: str
    anki_collection_path: str
    prefs_db_path: str
    gemini_api_key: str
    gemini_timeout_seconds: int
    scheduler_daily_hour: int
    max_cards_per_session: int
```

- [ ] **Step 2: Add the value in `load_settings()`**

In `load_settings()`, after the `anki_collection_path` line:

```python
    return Settings(
        telegram_token=os.environ["TELEGRAM_TOKEN"],
        ankiweb_email=os.environ["ANKIWEB_EMAIL"],
        ankiweb_password=os.environ["ANKIWEB_PASSWORD"],
        anki_collection_path=os.environ.get(
            "ANKI_COLLECTION_PATH", "./data/anki/collection.anki2"
        ),
        prefs_db_path=os.environ.get("PREFS_DB_PATH", "./data/sensei.db"),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        gemini_timeout_seconds=int(os.environ.get("GEMINI_TIMEOUT", "30")),
        scheduler_daily_hour=int(os.environ.get("SCHEDULER_DAILY_HOUR", "8")),
        max_cards_per_session=int(os.environ.get("MAX_CARDS_PER_SESSION", "20")),
    )
```

- [ ] **Step 3: Verify settings load**

```bash
uv run python -c "from src.config import load_settings; s = load_settings(); print(s.prefs_db_path)"
```

Expected: `./data/sensei.db`

- [ ] **Step 4: Commit**

```bash
git add src/config.py
git commit -m "feat(config): add prefs_db_path setting (default ./data/sensei.db)"
```

---

### Task 3: Extend `AnkiClient` — `get_deck_names()` + deck-filtered `get_due_cards()`

**Files:**
- Modify: `src/anki/client.py`

Current `get_due_cards` at line 24 queries `"is:due"` with no deck filter.

- [ ] **Step 1: Add `get_deck_names` method and update `get_due_cards` signature**

Full updated `src/anki/client.py`:

```python
import asyncio
from contextlib import contextmanager

from anki.collection import Collection
from bs4 import BeautifulSoup

from src.quiz.models import CardData


class AnkiClient:
    _collection_lock = asyncio.Lock()

    def __init__(self, collection_path: str):
        self._collection_path = collection_path

    @contextmanager
    def _get_collection(self):
        col = Collection(self._collection_path)
        try:
            yield col
        finally:
            col.close()

    def get_deck_names(self) -> list[str]:
        """Synchronous. Call via run_in_executor."""
        with self._get_collection() as col:
            return sorted(d["name"] for d in col.decks.all())

    def get_due_cards(self, limit: int = 20, deck: str | None = None) -> list[CardData]:
        """Synchronous. Call via run_in_executor."""
        with self._get_collection() as col:
            if deck:
                query = f'deck:"{deck}" (is:due OR is:new)'
            else:
                query = "is:due"
            card_ids = col.find_cards(query)[:limit]
            cards = [self._card_to_data(col, cid) for cid in card_ids]
            return [c for c in cards if c.front or c.back]

    def answer_card(self, card_id: int, ease: int) -> None:
        """Synchronous. Call via run_in_executor. ease: 1=Again, 2=Hard, 3=Good, 4=Easy."""
        with self._get_collection() as col:
            card = col.get_card(card_id)
            col.sched.answerCard(card, ease)

    def get_due_count(self) -> int:
        """Synchronous. Call via run_in_executor."""
        with self._get_collection() as col:
            return len(col.find_cards("is:due"))

    def _card_to_data(self, col, card_id: int) -> CardData:
        card = col.get_card(card_id)
        note = card.note()
        deck_name = col.decks.name(card.did)
        fields = note.fields
        front = _strip_html(fields[0]) if fields else ""
        back = _strip_html(fields[1]) if len(fields) > 1 else ""
        tags = note.tags
        return CardData(
            card_id=card_id,
            front=front,
            back=back,
            tags=tags,
            deck_name=deck_name,
        )


def _strip_html(html: str) -> str:
    return BeautifulSoup(html, "lxml").get_text(separator=" ").strip()
```

- [ ] **Step 2: Smoke-test `get_deck_names`**

```bash
uv run python -c "
from src.anki.client import AnkiClient
c = AnkiClient('./data/anki/collection.anki2')
print(c.get_deck_names())
"
```

Expected: a list of deck name strings, e.g. `['Default', 'Japanese::N5']`.

- [ ] **Step 3: Commit**

```bash
git add src/anki/client.py
git commit -m "feat(anki): add get_deck_names, add deck filter + is:new to get_due_cards"
```

---

### Task 4: Update `QuizEngine` to use `UserPrefsStore`

**Files:**
- Modify: `src/quiz/engine.py`

Replace the in-memory `_selected_decks` dict with `UserPrefsStore`. Add `get_deck_names()` async wrapper. Pass `deck` to `get_due_cards` in `start_session`.

- [ ] **Step 1: Write the full updated `src/quiz/engine.py`**

```python
import asyncio
import random

from src.anki.client import AnkiClient
from src.anki.sync import AnkiSyncer
from src.db.prefs import UserPrefsStore
from src.gemini.client import GeminiClient
from src.quiz.models import (
    AnswerResult,
    QuizQuestion,
    QuizSession,
    QuizType,
    SessionSummary,
)
from src.quiz.scorer import Scorer


class QuizEngine:
    def __init__(
        self,
        anki_client: AnkiClient,
        anki_syncer: AnkiSyncer,
        gemini_client: GeminiClient,
        scorer: Scorer,
        prefs_store: UserPrefsStore,
    ):
        self._anki = anki_client
        self._syncer = anki_syncer
        self._gemini = gemini_client
        self._scorer = scorer
        self._prefs = prefs_store
        self._sessions: dict[int, QuizSession] = {}

    async def start_session(self, user_id: int, max_cards: int) -> QuizSession:
        """Sync from AnkiWeb, fetch due cards for the user's selected deck, init session."""
        await self._syncer.async_sync()
        deck = self._prefs.get_deck(user_id)
        cards = await self._run_anki(self._anki.get_due_cards, max_cards, deck)
        session = QuizSession(
            user_id=user_id,
            pending_cards=cards,
            is_active=True,
        )
        self._sessions[user_id] = session
        return session

    async def next_question(self, user_id: int) -> QuizQuestion | None:
        """Pop next card, generate question. Returns None if no cards left."""
        session = self._sessions.get(user_id)
        if not session or not session.pending_cards:
            return None
        card = session.pending_cards.pop(0)
        quiz_type = random.choice(list(QuizType))
        question = await self._gemini.generate_question(card, quiz_type)
        session.current_question = question
        return question

    async def submit_answer(self, user_id: int, user_answer: str) -> AnswerResult:
        """Score answer, update Anki card, return result."""
        session = self._sessions[user_id]
        question = session.current_question
        ease, feedback = await self._scorer.score(question, user_answer)
        await self._run_anki(self._anki.answer_card, question.card_data.card_id, ease)
        session.cards_done += 1
        if ease >= 3:
            session.correct_count += 1
        session.ease_history.append(ease)
        session.current_question = None
        return AnswerResult(
            ease=ease,
            feedback=feedback,
            correct_answer=question.correct_answer,
        )

    async def end_session(self, user_id: int) -> SessionSummary:
        """End session, sync to AnkiWeb, clean up."""
        session = self._sessions.pop(user_id, None)
        await self._syncer.async_sync()
        if session is None:
            return SessionSummary(user_id=user_id, cards_done=0, correct_count=0, ease_history=[])
        return SessionSummary(
            user_id=user_id,
            cards_done=session.cards_done,
            correct_count=session.correct_count,
            ease_history=session.ease_history,
        )

    def get_current_question(self, user_id: int):
        session = self._sessions.get(user_id)
        if session is None:
            return None
        return session.current_question

    def has_active_session(self, user_id: int) -> bool:
        return user_id in self._sessions and self._sessions[user_id].is_active

    def get_due_count_sync(self) -> int:
        """For notification jobs — returns due count synchronously."""
        return self._anki.get_due_count()

    async def get_deck_names(self) -> list[str]:
        return await self._run_anki(self._anki.get_deck_names)

    def set_deck(self, user_id: int, deck_name: str | None) -> None:
        self._prefs.set_deck(user_id, deck_name)

    def get_deck(self, user_id: int) -> str | None:
        return self._prefs.get_deck(user_id)

    async def _run_anki(self, fn, *args):
        """Acquire collection lock, run synchronous anki fn in executor."""
        async with AnkiClient._collection_lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, fn, *args)
```

- [ ] **Step 2: Commit**

```bash
git add src/quiz/engine.py
git commit -m "feat(engine): use UserPrefsStore for deck selection, add get_deck_names/set_deck/get_deck"
```

---

### Task 5: Add `deck_list_keyboard` to `src/bot/keyboards.py`

**Files:**
- Modify: `src/bot/keyboards.py`

Callback data format: `deck_select:<deck_name>` (empty string after colon = All decks).

- [ ] **Step 1: Add `deck_list_keyboard` to the end of `src/bot/keyboards.py`**

```python
def deck_list_keyboard(deck_names: list[str]) -> InlineKeyboardMarkup:
    """One button per deck + 'All decks' at top. Capped at 8 decks."""
    rows = [[InlineKeyboardButton("📚 All decks", callback_data="deck_select:")]]
    for name in deck_names[:8]:
        rows.append([InlineKeyboardButton(name, callback_data=f"deck_select:{name}")])
    return InlineKeyboardMarkup(rows)
```

- [ ] **Step 2: Commit**

```bash
git add src/bot/keyboards.py
git commit -m "feat(keyboards): add deck_list_keyboard"
```

---

### Task 6: Add `/decks` command and `deck_select_callback` to `src/bot/handlers.py`

**Files:**
- Modify: `src/bot/handlers.py`

- [ ] **Step 1: Update the `keyboards` import**

Replace the existing import line:

```python
from src.bot.keyboards import after_answer_keyboard, question_keyboard, session_summary_keyboard
```

with:

```python
from src.bot.keyboards import (
    after_answer_keyboard,
    deck_list_keyboard,
    question_keyboard,
    session_summary_keyboard,
)
```

- [ ] **Step 2: Update `start_command` help text inside `make_handlers`**

```python
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 Welcome to Sensei!\n\n"
        "Commands:\n"
        "/quiz — Start a review session\n"
        "/decks — Choose which deck to study\n"
        "/status — Check how many cards are due\n"
        "/stop — End current session\n"
    )
    await update.message.reply_text(text)
```

- [ ] **Step 3: Add `decks_command` inside `make_handlers` (after `stop_command`)**

```python
async def decks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deck_names = await engine.get_deck_names()
    current = engine.get_deck(update.effective_user.id)
    header = f"📂 Select a deck to study\nCurrent: {current or 'All decks'}"
    await update.message.reply_text(header, reply_markup=deck_list_keyboard(deck_names))
```

- [ ] **Step 4: Add `deck_select_callback` inside `make_handlers` (after `decks_command`)**

```python
async def deck_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    deck_name = query.data.removeprefix("deck_select:") or None
    engine.set_deck(user_id, deck_name)
    label = deck_name if deck_name else "All decks"
    await query.edit_message_text(f"✅ Deck set to: {label}\nUse /quiz to start reviewing.")
```

- [ ] **Step 5: Add `decks` and `deck_select` to the returned dict**

```python
return {
    "start": start_command,
    "quiz": quiz_command,
    "stop": stop_command,
    "status": status_command,
    "decks": decks_command,
    "deck_select": deck_select_callback,
    "handle_answer": handle_answer,
    "skip": skip_callback,
    "hint": hint_callback,
    "next": next_callback,
    "end": end_callback,
    "new_session": new_session_callback,
    "sync": sync_callback,
    "send_due_notification": send_due_notification,
}
```

- [ ] **Step 6: Commit**

```bash
git add src/bot/handlers.py
git commit -m "feat(handlers): add /decks command and deck_select callback"
```

---

### Task 7: Wire everything in `src/main.py`

**Files:**
- Modify: `src/main.py`

- [ ] **Step 1: Add `UserPrefsStore` import and initialisation**

Add import:

```python
from src.db.prefs import UserPrefsStore
```

In `main()`, after `anki_syncer = AnkiSyncer(...)`:

```python
prefs_store = UserPrefsStore(settings.prefs_db_path)
```

- [ ] **Step 2: Pass `prefs_store` to `QuizEngine`**

```python
engine = QuizEngine(anki_client, anki_syncer, gemini_client, scorer, prefs_store)
```

- [ ] **Step 3: Register the new handlers**

After the existing `CommandHandler` registrations:

```python
app.add_handler(CommandHandler("decks", handlers["decks"]))
```

After the existing `CallbackQueryHandler` registrations:

```python
app.add_handler(CallbackQueryHandler(handlers["deck_select"], pattern=r"^deck_select:"))
```

- [ ] **Step 4: Verify bot starts**

```bash
uv run python -m src.main
```

Expected: Bot starts cleanly, `data/sensei.db` created on first run.

- [ ] **Step 5: Manual Telegram test**

1. `/decks` → deck list with "All decks" button + one button per deck
2. Tap a deck → "✅ Deck set to: Japanese::N5"
3. `/quiz` → session uses cards from that deck only
4. Kill and restart bot → `/decks` → current deck still shown (persisted in SQLite)
5. Tap "All decks" → deck cleared, `/quiz` uses all due cards

- [ ] **Step 6: Commit**

```bash
git add src/main.py
git commit -m "feat(main): wire UserPrefsStore and register /decks + deck_select handlers"
```
