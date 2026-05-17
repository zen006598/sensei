# Agent Quiz System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stateless QuizEngine with a Gemini function-calling agent that drills one card at a time until mastery, with persistent session history and error memory.

**Architecture:** Python state machine owns session lifecycle and exact-match checks. GeminiAgent (function calling via `google-genai`) handles word frequency classification, question generation, answer evaluation, and session summarisation. SQLModel persists ConversationSession and ErrorRecord. Single-user service — no user_id on new DB tables.

**Tech Stack:** Python 3.14, `google-genai` SDK (function calling), SQLModel, anki 25.9.4, python-telegram-bot 22.7.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/db/models.py` | Create | `ConversationSession`, `ErrorRecord` SQLModel tables |
| `src/db/prefs.py` | Modify | Add `quiz_mode` to `UserPrefs`; add `get_mode`/`set_mode` |
| `src/anki/client.py` | Modify | Add `update_card_tags()`; add `mode` param to `get_due_cards()` |
| `src/agent/__init__.py` | Create | Package marker |
| `src/agent/tools.py` | Create | `QuizResult`, `JudgeResult` dataclasses + Gemini tool definitions |
| `src/agent/gemini_agent.py` | Create | `GeminiAgent` — function-calling wrapper |
| `src/agent/state_machine.py` | Create | `QuizStateMachine` — session lifecycle, exact match, state transitions |
| `src/bot/keyboards.py` | Modify | Add `mode_keyboard()` |
| `src/bot/handlers.py` | Modify | Wire `QuizStateMachine`, add `/mode` command |
| `src/main.py` | Modify | Init `GeminiAgent`, `QuizStateMachine`; remove old engine/scorer |
| `src/quiz/models.py` | Modify | Keep only `CardData`; remove unused types |
| `src/quiz/engine.py` | Delete | Replaced by `QuizStateMachine` |
| `src/quiz/scorer.py` | Delete | Replaced by `GeminiAgent.evaluate_answer` |
| `src/gemini/client.py` | Modify | Remove `generate_question`/`score_answer`; keep `call()` for raw JSON |

---

### Task 1: Create `src/db/models.py`

**Files:**
- Create: `src/db/models.py`
- Test: `tests/db/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/db/test_models.py`:

```python
import os
import tempfile

from sqlmodel import Session, SQLModel, create_engine, select

from src.db.models import ConversationSession, ErrorRecord


def _engine():
    tmp = tempfile.mktemp(suffix=".db")
    engine = create_engine(f"sqlite:///{tmp}")
    SQLModel.metadata.create_all(engine)
    return engine, tmp


def test_create_conversation_session():
    engine, tmp = _engine()
    with Session(engine) as s:
        cs = ConversationSession(card_id=123)
        s.add(cs)
        s.commit()
        s.refresh(cs)
    assert cs.id is not None
    assert cs.card_id == 123
    assert cs.outcome is None
    assert cs.attempt_count == 0
    os.unlink(tmp)


def test_update_conversation_session():
    engine, tmp = _engine()
    with Session(engine) as s:
        cs = ConversationSession(card_id=1)
        s.add(cs)
        s.commit()
        s.refresh(cs)
        cs_id = cs.id
    with Session(engine) as s:
        cs = s.get(ConversationSession, cs_id)
        cs.outcome = "perfect"
        cs.attempt_count = 3
        s.add(cs)
        s.commit()
    with Session(engine) as s:
        cs = s.get(ConversationSession, cs_id)
    assert cs.outcome == "perfect"
    assert cs.attempt_count == 3
    os.unlink(tmp)


def test_create_error_record():
    engine, tmp = _engine()
    with Session(engine) as s:
        er = ErrorRecord(card_id=456, error_type="grammar", user_answer="I goed")
        s.add(er)
        s.commit()
        s.refresh(er)
    assert er.id is not None
    assert er.error_type == "grammar"
    os.unlink(tmp)


def test_query_errors_by_card():
    engine, tmp = _engine()
    with Session(engine) as s:
        s.add(ErrorRecord(card_id=1, error_type="spelling", user_answer="wrng"))
        s.add(ErrorRecord(card_id=1, error_type="grammar", user_answer="bad grammar"))
        s.add(ErrorRecord(card_id=2, error_type="spelling", user_answer="other"))
        s.commit()
    with Session(engine) as s:
        errors = s.exec(select(ErrorRecord).where(ErrorRecord.card_id == 1)).all()
    assert len(errors) == 2
    os.unlink(tmp)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run python -m pytest tests/db/test_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.db.models'`

- [ ] **Step 3: Create `src/db/models.py`**

```python
from datetime import datetime

from sqlmodel import Field, SQLModel


class ConversationSession(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    outcome: str | None = None      # "perfect" | "skipped" | "stopped"
    summary: str | None = None      # Gemini NL summary
    messages: str | None = None     # JSON: last 3 user messages
    attempt_count: int = 0


class ErrorRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int                    # soft FK → Anki card id
    error_type: str                 # "grammar" | "vocabulary" | "spelling"
    user_answer: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run python -m pytest tests/db/test_models.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/db/models.py tests/db/test_models.py
git commit -m "feat(db): add ConversationSession and ErrorRecord models"
```

---

### Task 2: Add `quiz_mode` to `UserPrefs`

**Files:**
- Modify: `src/db/prefs.py`
- Test: `tests/db/test_prefs.py` (add 2 tests)

> **Note:** The existing `data/sensei.db` must be deleted before running the bot after this change, since SQLModel's `create_all` does not add new columns to existing tables.
> ```bash
> rm -f data/sensei.db
> ```

- [ ] **Step 1: Add tests for `quiz_mode`**

Append to `tests/db/test_prefs.py`:

```python
def test_get_mode_returns_default_for_unknown_user():
    store, tmp = _store()
    assert store.get_mode(12345) == "default"
    os.unlink(tmp)


def test_set_and_get_mode():
    store, tmp = _store()
    store.set_mode(1, "due")
    assert store.get_mode(1) == "due"
    os.unlink(tmp)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/db/test_prefs.py -v
```

Expected: 2 new tests fail with `AttributeError`.

- [ ] **Step 3: Update `src/db/prefs.py`**

```python
from pathlib import Path

from sqlmodel import Field, Session, SQLModel, create_engine


class UserPrefs(SQLModel, table=True):
    user_id: int = Field(primary_key=True)
    selected_deck: str | None = None
    quiz_mode: str = "default"      # "default" | "due" | "new"


class UserPrefsStore:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(self._engine)

    def _get_or_create(self, session: Session, user_id: int) -> UserPrefs:
        prefs = session.get(UserPrefs, user_id)
        if prefs is None:
            prefs = UserPrefs(user_id=user_id)
            session.add(prefs)
            session.flush()
        return prefs

    def get_deck(self, user_id: int) -> str | None:
        with Session(self._engine) as session:
            prefs = session.get(UserPrefs, user_id)
            return prefs.selected_deck if prefs else None

    def set_deck(self, user_id: int, deck_name: str | None) -> None:
        with Session(self._engine) as session:
            prefs = self._get_or_create(session, user_id)
            prefs.selected_deck = deck_name
            session.add(prefs)
            session.commit()

    def get_mode(self, user_id: int) -> str:
        with Session(self._engine) as session:
            prefs = session.get(UserPrefs, user_id)
            return prefs.quiz_mode if prefs else "default"

    def set_mode(self, user_id: int, mode: str) -> None:
        with Session(self._engine) as session:
            prefs = self._get_or_create(session, user_id)
            prefs.quiz_mode = mode
            session.add(prefs)
            session.commit()
```

- [ ] **Step 4: Run all prefs tests**

```bash
uv run python -m pytest tests/db/test_prefs.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/db/prefs.py tests/db/test_prefs.py
git commit -m "feat(db): add quiz_mode to UserPrefs with get_mode/set_mode"
```

---

### Task 3: Update `AnkiClient`

**Files:**
- Modify: `src/anki/client.py`

- [ ] **Step 1: Add `update_card_tags` and `mode` param to `get_due_cards`**

Full updated `src/anki/client.py`:

```python
import asyncio
from contextlib import contextmanager

from anki.collection import Collection
from bs4 import BeautifulSoup

from src.quiz.models import CardData

_MODE_QUERIES = {
    "default": "is:due OR is:new",
    "due": "is:due",
    "new": "is:new",
}


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

    def get_due_cards(
        self, limit: int = 20, deck: str | None = None, mode: str = "default"
    ) -> list[CardData]:
        """Synchronous. Call via run_in_executor."""
        with self._get_collection() as col:
            mode_query = _MODE_QUERIES.get(mode, _MODE_QUERIES["default"])
            if deck:
                query = f'deck:"{deck}" ({mode_query})'
            else:
                query = mode_query
            card_ids = col.find_cards(query)[:limit]
            cards = [self._card_to_data(col, cid) for cid in card_ids]
            return [c for c in cards if c.front or c.back]

    def answer_card(self, card_id: int, ease: int) -> None:
        """Synchronous. Call via run_in_executor. ease: 1=Again 2=Hard 3=Good 4=Easy."""
        with self._get_collection() as col:
            card = col.get_card(card_id)
            card.start_timer()
            col.sched.answerCard(card, ease)

    def update_card_tags(self, card_id: int, tags_to_add: list[str]) -> None:
        """Append tags to a card's note. Synchronous. Call via run_in_executor."""
        with self._get_collection() as col:
            card = col.get_card(card_id)
            note = card.note()
            for tag in tags_to_add:
                if tag not in note.tags:
                    note.tags.append(tag)
            col.update_note(note)

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
        return CardData(
            card_id=card_id,
            front=front,
            back=back,
            tags=note.tags,
            deck_name=deck_name,
        )


def _strip_html(html: str) -> str:
    return BeautifulSoup(html, "lxml").get_text(separator=" ").strip()
```

- [ ] **Step 2: Smoke-test**

```bash
uv run python -c "
from src.anki.client import AnkiClient
c = AnkiClient('./data/anki/collection.anki2')
cards = c.get_due_cards(limit=3, mode='due')
print('due mode:', len(cards), 'cards')
cards = c.get_due_cards(limit=3, mode='new')
print('new mode:', len(cards), 'cards')
"
```

Expected: prints card counts for each mode.

- [ ] **Step 3: Commit**

```bash
git add src/anki/client.py
git commit -m "feat(anki): add update_card_tags, add mode param to get_due_cards"
```

---

### Task 4: Create `src/agent/tools.py`

**Files:**
- Create: `src/agent/__init__.py`
- Create: `src/agent/tools.py`

No tests needed — these are dataclass definitions and Gemini schema declarations.

- [ ] **Step 1: Create `src/agent/__init__.py`**

```bash
touch src/agent/__init__.py
```

- [ ] **Step 2: Create `src/agent/tools.py`**

```python
from dataclasses import dataclass

from google.genai import types


@dataclass
class QuizResult:
    question_type: str      # "fill_in_blank" | "spelling" | "sentence"
    question_text: str
    correct_answer: str
    hint: str = ""


@dataclass
class JudgeResult:
    outcome: str            # "correct" | "semantic_correct" | "grammar_error" | "vocab_error" | "wrong"
    error_type: str | None  # "grammar" | "vocabulary" | "spelling" | None
    suggestion: str


QUIZ_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="quiz",
            description="Output a quiz question for the learner",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "question_type": types.Schema(
                        type=types.Type.STRING,
                        enum=["fill_in_blank", "spelling", "sentence"],
                    ),
                    "question_text": types.Schema(type=types.Type.STRING),
                    "correct_answer": types.Schema(type=types.Type.STRING),
                    "hint": types.Schema(type=types.Type.STRING),
                },
                required=["question_type", "question_text", "correct_answer", "hint"],
            ),
        )
    ]
)

JUDGE_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="judge_score",
            description="Evaluate the learner's answer and provide a suggestion",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "outcome": types.Schema(
                        type=types.Type.STRING,
                        enum=[
                            "correct",
                            "semantic_correct",
                            "grammar_error",
                            "vocab_error",
                            "wrong",
                        ],
                    ),
                    "error_type": types.Schema(
                        type=types.Type.STRING,
                        enum=["grammar", "vocabulary", "spelling", ""],
                    ),
                    "suggestion": types.Schema(type=types.Type.STRING),
                },
                required=["outcome", "error_type", "suggestion"],
            ),
        )
    ]
)

FREQ_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="classify_frequency",
            description="Classify how commonly this word/phrase is used",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "frequency": types.Schema(
                        type=types.Type.STRING,
                        enum=["common", "rare", "obsolete"],
                    ),
                },
                required=["frequency"],
            ),
        )
    ]
)
```

- [ ] **Step 3: Verify import**

```bash
uv run python -c "from src.agent.tools import QuizResult, JudgeResult, QUIZ_TOOL; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/agent/__init__.py src/agent/tools.py
git commit -m "feat(agent): add QuizResult, JudgeResult, and Gemini tool definitions"
```

---

### Task 5: Create `src/agent/gemini_agent.py`

**Files:**
- Create: `src/agent/gemini_agent.py`
- Test: `tests/agent/test_gemini_agent.py`

- [ ] **Step 1: Write failing tests**

```bash
mkdir -p tests/agent && touch tests/agent/__init__.py
```

Create `tests/agent/test_gemini_agent.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.gemini_agent import GeminiAgent
from src.agent.tools import JudgeResult, QuizResult
from src.quiz.models import CardData


def _card():
    return CardData(card_id=1, front="run", back="走る", tags=[], deck_name="EN")


def _mock_fc(name, args):
    """Build a fake function_call part."""
    fc = MagicMock()
    fc.function_call.name = name
    fc.function_call.args = args
    return fc


def _mock_response(parts):
    resp = MagicMock()
    resp.candidates[0].content.parts = parts
    return resp


@pytest.mark.asyncio
async def test_classify_word_frequency():
    agent = GeminiAgent(api_key="test")
    part = _mock_fc("classify_frequency", {"frequency": "common"})
    with patch.object(
        agent._client.aio.models, "generate_content", new=AsyncMock(return_value=_mock_response([part]))
    ):
        result = await agent.classify_word_frequency(_card())
    assert result == "common"


@pytest.mark.asyncio
async def test_generate_question_returns_quiz_result():
    agent = GeminiAgent(api_key="test")
    part = _mock_fc(
        "quiz",
        {"question_type": "spelling", "question_text": "How do you spell 'run'?", "correct_answer": "run", "hint": ""},
    )
    with patch.object(
        agent._client.aio.models, "generate_content", new=AsyncMock(return_value=_mock_response([part]))
    ):
        result = await agent.generate_question(_card(), "common", retry_count=0, recent_errors=[], conversation_summary=None)
    assert isinstance(result, QuizResult)
    assert result.question_type == "spelling"
    assert result.correct_answer == "run"


@pytest.mark.asyncio
async def test_evaluate_answer_returns_judge_result():
    agent = GeminiAgent(api_key="test")
    part = _mock_fc(
        "judge_score",
        {"outcome": "grammar_error", "error_type": "grammar", "suggestion": "Use past tense: ran."},
    )
    with patch.object(
        agent._client.aio.models, "generate_content", new=AsyncMock(return_value=_mock_response([part]))
    ):
        result = await agent.evaluate_answer("sentence", "Use 'run' in a sentence.", "run", "I runned fast.")
    assert isinstance(result, JudgeResult)
    assert result.outcome == "grammar_error"
    assert result.error_type == "grammar"
```

- [ ] **Step 2: Add `pytest-asyncio` dependency**

```bash
uv add --group dev pytest-asyncio
```

Add to `pyproject.toml` under `[tool.pytest.ini_options]`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run python -m pytest tests/agent/test_gemini_agent.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.agent.gemini_agent'`

- [ ] **Step 4: Create `src/agent/gemini_agent.py`**

```python
from google import genai
from google.genai import types

from src.agent.tools import FREQ_TOOL, JUDGE_TOOL, QUIZ_TOOL, JudgeResult, QuizResult
from src.quiz.models import CardData

_SENSEI_FREQ_TAGS = {"sensei:common", "sensei:rare", "sensei:obsolete"}


class GeminiAgent:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash-lite"):
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def classify_word_frequency(self, card: CardData) -> str:
        """Returns 'common', 'rare', or 'obsolete'. Falls back to 'common' on failure."""
        prompt = (
            f"Classify the usage frequency of this vocabulary item for a language learner:\n"
            f"Front: {card.front}\nBack: {card.back}\n"
            "common = everyday usage, rare = infrequent/specialised, obsolete = archaic/no longer used.\n"
            "Use the classify_frequency tool."
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[FREQ_TOOL]),
        )
        for part in response.candidates[0].content.parts:
            if part.function_call and part.function_call.name == "classify_frequency":
                return part.function_call.args["frequency"]
        return "common"

    async def generate_question(
        self,
        card: CardData,
        frequency: str,
        retry_count: int,
        recent_errors: list[str],
        conversation_summary: str | None,
        forced_type: str | None = None,
    ) -> QuizResult:
        if forced_type:
            type_instruction = f"You MUST generate a '{forced_type}' question. No other type is allowed."
        elif frequency == "common":
            type_instruction = "Choose the most suitable type: fill_in_blank, spelling, or sentence."
        else:
            type_instruction = "You MUST generate a 'fill_in_blank' question (word is rare/obsolete)."

        context_lines = []
        if conversation_summary:
            context_lines.append(f"Previous session summary: {conversation_summary}")
        if recent_errors:
            context_lines.append(f"Recent errors for this card: {'; '.join(recent_errors)}")
        if retry_count > 0:
            context_lines.append(
                f"This is retry #{retry_count}. Consider a different angle or phrasing."
            )
        context = "\n".join(context_lines)

        prompt = (
            "You are a language learning quiz generator.\n"
            f"Card front: {card.front}\nCard back: {card.back}\nTags: {', '.join(card.tags)}\n"
            f"{context}\n"
            f"{type_instruction}\n"
            "Use the quiz tool to output the question."
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[QUIZ_TOOL]),
        )
        for part in response.candidates[0].content.parts:
            if part.function_call and part.function_call.name == "quiz":
                args = part.function_call.args
                return QuizResult(
                    question_type=args["question_type"],
                    question_text=args["question_text"],
                    correct_answer=args["correct_answer"],
                    hint=args.get("hint", ""),
                )
        raise RuntimeError("Agent did not call quiz tool")

    async def evaluate_answer(
        self,
        question_type: str,
        question_text: str,
        correct_answer: str,
        user_answer: str,
    ) -> JudgeResult:
        prompt = (
            f"Evaluate the learner's answer for a '{question_type}' question.\n"
            f"Question: {question_text}\n"
            f"Correct answer: {correct_answer}\n"
            f"Learner's answer: {user_answer}\n\n"
            "Scoring guide:\n"
            "- correct: near-exact match\n"
            "- semantic_correct: different word but correct meaning (spelling questions only)\n"
            "- grammar_error: right vocabulary, wrong grammar (sentence questions)\n"
            "- vocab_error: wrong vocabulary (sentence questions)\n"
            "- wrong: clearly incorrect\n\n"
            "Also provide a concise suggestion:\n"
            "  correct → more natural/idiomatic phrasing\n"
            "  error → correction with brief explanation\n"
            "Use the judge_score tool."
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[JUDGE_TOOL]),
        )
        for part in response.candidates[0].content.parts:
            if part.function_call and part.function_call.name == "judge_score":
                args = part.function_call.args
                raw_error_type = args.get("error_type", "")
                return JudgeResult(
                    outcome=args["outcome"],
                    error_type=raw_error_type if raw_error_type else None,
                    suggestion=args["suggestion"],
                )
        raise RuntimeError("Agent did not call judge_score tool")

    async def generate_session_summary(
        self,
        card_front: str,
        card_back: str,
        messages: list[str],
        recent_errors: list[str],
    ) -> str:
        msgs_text = "\n".join(f"- {m}" for m in messages) or "(none)"
        errs_text = "\n".join(f"- {e}" for e in recent_errors) or "(none)"
        prompt = (
            "Summarise this language learning session in 2-3 sentences for future reference.\n"
            f"Card: {card_front} → {card_back}\n"
            f"Learner's messages:\n{msgs_text}\n"
            f"Errors:\n{errs_text}\n"
            "Focus on what the learner struggled with and any patterns observed."
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
        )
        return response.text.strip()
```

- [ ] **Step 5: Run tests**

```bash
uv run python -m pytest tests/agent/test_gemini_agent.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/agent/gemini_agent.py tests/agent/__init__.py tests/agent/test_gemini_agent.py pyproject.toml
git commit -m "feat(agent): add GeminiAgent with function calling for quiz/score/summary"
```

---

### Task 6: Create `src/agent/state_machine.py`

**Files:**
- Create: `src/agent/state_machine.py`
- Test: `tests/agent/test_state_machine.py`

- [ ] **Step 1: Write failing tests**

Create `tests/agent/test_state_machine.py`:

```python
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel, create_engine

from src.agent.gemini_agent import GeminiAgent
from src.agent.state_machine import QuizStateMachine
from src.agent.tools import JudgeResult, QuizResult
from src.db.models import ConversationSession, ErrorRecord
from src.db.prefs import UserPrefsStore
from src.quiz.models import CardData


def _setup():
    tmp_db = tempfile.mktemp(suffix=".db")
    tmp_prefs = tempfile.mktemp(suffix=".db")
    engine = create_engine(f"sqlite:///{tmp_db}")
    SQLModel.metadata.create_all(engine)
    prefs = UserPrefsStore(tmp_prefs)

    agent = MagicMock(spec=GeminiAgent)
    anki = MagicMock()
    anki.get_due_cards = MagicMock(return_value=[
        CardData(card_id=1, front="run", back="走る", tags=[], deck_name="EN")
    ])
    anki.answer_card = MagicMock()
    anki.update_card_tags = MagicMock()

    syncer = MagicMock()
    syncer.async_sync = AsyncMock()

    sm = QuizStateMachine(anki, syncer, agent, prefs, engine)
    return sm, agent, anki, engine, tmp_db, tmp_prefs


@pytest.mark.asyncio
async def test_start_returns_question():
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    agent.generate_question = AsyncMock(return_value=QuizResult(
        question_type="spelling", question_text="Spell: run", correct_answer="run"
    ))

    question = await sm.start(user_id=1)

    assert question is not None
    assert question.question_type == "spelling"
    assert sm.has_active_session()
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)


@pytest.mark.asyncio
async def test_exact_match_ends_session():
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    agent.generate_question = AsyncMock(return_value=QuizResult(
        question_type="spelling", question_text="Spell: run", correct_answer="run"
    ))
    agent.evaluate_answer = AsyncMock(return_value=JudgeResult(
        outcome="correct", error_type=None, suggestion="Perfect! Also: dash, sprint."
    ))
    agent.generate_question.side_effect = [
        QuizResult(question_type="spelling", question_text="Spell: run", correct_answer="run"),
        QuizResult(question_type="spelling", question_text="Spell: eat", correct_answer="eat"),
    ]
    agent.generate_session_summary = AsyncMock(return_value="Good session.")

    await sm.start(user_id=1)
    result = await sm.submit_answer("run")

    assert result.outcome == "correct"
    assert result.session_ended is True
    anki.answer_card.assert_called_once_with(1, 4)
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)


@pytest.mark.asyncio
async def test_wrong_answer_session_continues():
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    agent.generate_question = AsyncMock(return_value=QuizResult(
        question_type="spelling", question_text="Spell: run", correct_answer="run"
    ))
    agent.evaluate_answer = AsyncMock(return_value=JudgeResult(
        outcome="wrong", error_type="spelling", suggestion="The correct spelling is 'run'."
    ))
    agent.generate_session_summary = AsyncMock(return_value="Struggled.")

    await sm.start(user_id=1)
    result = await sm.submit_answer("ran")

    assert result.outcome == "wrong"
    assert result.session_ended is False
    assert not sm.has_active_session() is False  # still active
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)


@pytest.mark.asyncio
async def test_grammar_error_records_and_continues():
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    agent.generate_question = AsyncMock(return_value=QuizResult(
        question_type="sentence", question_text="Use 'run' in a sentence.", correct_answer="I run every day."
    ))
    agent.evaluate_answer = AsyncMock(return_value=JudgeResult(
        outcome="grammar_error", error_type="grammar", suggestion="Say 'I run' not 'I runned'."
    ))
    agent.generate_session_summary = AsyncMock(return_value="Grammar issues.")

    await sm.start(user_id=1)
    result = await sm.submit_answer("I runned every day.")

    assert result.outcome == "grammar_error"
    assert result.session_ended is False
    from sqlmodel import Session, select
    with Session(engine) as s:
        errors = s.exec(select(ErrorRecord).where(ErrorRecord.card_id == 1)).all()
    assert len(errors) == 1
    assert errors[0].error_type == "grammar"
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/agent/test_state_machine.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.agent.state_machine'`

- [ ] **Step 3: Create `src/agent/state_machine.py`**

```python
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session, select

from src.agent.gemini_agent import GeminiAgent
from src.agent.tools import JudgeResult, QuizResult
from src.anki.client import AnkiClient
from src.anki.sync import AnkiSyncer
from src.db.models import ConversationSession, ErrorRecord
from src.db.prefs import UserPrefsStore
from src.quiz.models import CardData

_SENSEI_FREQ_TAGS = {"sensei:common", "sensei:rare", "sensei:obsolete"}


@dataclass
class _ActiveSession:
    card: CardData
    db_session_id: int
    current_question: QuizResult
    attempt_count: int = 0
    messages: list[str] = field(default_factory=list)


@dataclass
class SubmitResult:
    outcome: str            # "correct" | "wrong" | "semantic_correct" | "grammar_error" | "vocab_error"
    suggestion: str
    correct_answer: str
    session_ended: bool
    new_session_started: bool
    new_question: QuizResult | None


class QuizStateMachine:
    def __init__(
        self,
        anki_client: AnkiClient,
        anki_syncer: AnkiSyncer,
        agent: GeminiAgent,
        prefs_store: UserPrefsStore,
        db_engine,
    ):
        self._anki = anki_client
        self._syncer = anki_syncer
        self._agent = agent
        self._prefs = prefs_store
        self._engine = db_engine
        self._active: _ActiveSession | None = None
        self._user_id: int = 0

    def has_active_session(self) -> bool:
        return self._active is not None

    def get_current_question(self) -> QuizResult | None:
        return self._active.current_question if self._active else None

    def get_due_count_sync(self) -> int:
        return self._anki.get_due_count()

    def set_deck(self, user_id: int, deck_name: str | None) -> None:
        self._prefs.set_deck(user_id, deck_name)

    def get_deck(self, user_id: int) -> str | None:
        return self._prefs.get_deck(user_id)

    def set_mode(self, user_id: int, mode: str) -> None:
        self._prefs.set_mode(user_id, mode)

    def get_mode(self, user_id: int) -> str:
        return self._prefs.get_mode(user_id)

    async def get_deck_names(self) -> list[str]:
        return await self._run_anki(self._anki.get_deck_names)

    async def start(self, user_id: int) -> QuizResult | None:
        """Sync, pick a card, begin session. Returns None if no cards."""
        self._user_id = user_id
        await self._syncer.async_sync()
        return await self._auto_start()

    async def skip(self) -> QuizResult | None:
        """End current session (skipped) and auto-start next."""
        await self._end_session("skipped")
        return await self._auto_start()

    async def stop(self) -> None:
        """End current session without starting a new one."""
        await self._end_session("stopped")

    async def submit_answer(self, user_answer: str) -> SubmitResult:
        assert self._active is not None
        session = self._active
        question = session.current_question

        session.messages.append(user_answer)
        if len(session.messages) > 3:
            session.messages = session.messages[-3:]
        session.attempt_count += 1
        self._update_attempt_count(session.db_session_id, session.attempt_count)

        # Spelling exact match shortcut
        if question.question_type == "spelling":
            if user_answer.strip().lower() == question.correct_answer.strip().lower():
                judge = await self._agent.evaluate_answer(
                    question.question_type, question.question_text,
                    question.correct_answer, user_answer,
                )
                await self._run_anki(self._anki.answer_card, session.card.card_id, 4)
                await self._end_session("perfect")
                new_q = await self._auto_start()
                return SubmitResult(
                    outcome="correct", suggestion=judge.suggestion,
                    correct_answer=question.correct_answer,
                    session_ended=True, new_session_started=new_q is not None, new_question=new_q,
                )

        judge = await self._agent.evaluate_answer(
            question.question_type, question.question_text,
            question.correct_answer, user_answer,
        )
        return await self._handle_judgment(judge, question, session)

    async def _handle_judgment(
        self, judge: JudgeResult, question: QuizResult, session: _ActiveSession
    ) -> SubmitResult:
        card_id = session.card.card_id

        if judge.outcome == "correct":
            await self._run_anki(self._anki.answer_card, card_id, 4)
            await self._end_session("perfect")
            new_q = await self._auto_start()
            return SubmitResult(
                outcome="correct", suggestion=judge.suggestion,
                correct_answer=question.correct_answer,
                session_ended=True, new_session_started=new_q is not None, new_question=new_q,
            )

        if judge.outcome == "semantic_correct":
            new_q = await self._next_question(session, forced_type="sentence")
            return SubmitResult(
                outcome="semantic_correct", suggestion=judge.suggestion,
                correct_answer=question.correct_answer,
                session_ended=False, new_session_started=False, new_question=new_q,
            )

        if judge.outcome == "grammar_error":
            self._record_error(card_id, "grammar", session.messages[-1])
            await self._run_anki(self._anki.answer_card, card_id, 1)
            new_q = await self._next_question(session, forced_type="sentence")
            return SubmitResult(
                outcome="grammar_error", suggestion=judge.suggestion,
                correct_answer=question.correct_answer,
                session_ended=False, new_session_started=False, new_question=new_q,
            )

        if judge.outcome == "vocab_error":
            self._record_error(card_id, "vocabulary", session.messages[-1])
            await self._run_anki(self._anki.answer_card, card_id, 1)
            new_q = await self._next_question(session, forced_type="spelling")
            return SubmitResult(
                outcome="vocab_error", suggestion=judge.suggestion,
                correct_answer=question.correct_answer,
                session_ended=False, new_session_started=False, new_question=new_q,
            )

        # "wrong"
        self._record_error(card_id, "spelling", session.messages[-1])
        await self._run_anki(self._anki.answer_card, card_id, 1)
        new_q = await self._next_question(session)
        return SubmitResult(
            outcome="wrong", suggestion=judge.suggestion,
            correct_answer=question.correct_answer,
            session_ended=False, new_session_started=False, new_question=new_q,
        )

    async def _auto_start(self) -> QuizResult | None:
        deck = self._prefs.get_deck(self._user_id)
        mode = self._prefs.get_mode(self._user_id)
        cards = await self._run_anki(self._anki.get_due_cards, 1, deck, mode)
        if not cards:
            return None
        return await self._begin_card(cards[0])

    async def _begin_card(self, card: CardData) -> QuizResult:
        frequency = await self._get_or_classify_frequency(card)
        recent_errors = self._get_recent_errors(card.card_id)
        last_summary = self._get_last_summary(card.card_id)
        question = await self._agent.generate_question(
            card, frequency, retry_count=0,
            recent_errors=recent_errors, conversation_summary=last_summary,
        )
        db_id = self._create_db_session(card.card_id)
        self._active = _ActiveSession(card=card, db_session_id=db_id, current_question=question)
        return question

    async def _next_question(self, session: _ActiveSession, forced_type: str | None = None) -> QuizResult:
        frequency = await self._get_or_classify_frequency(session.card)
        recent_errors = self._get_recent_errors(session.card.card_id)
        last_summary = self._get_last_summary(session.card.card_id)
        question = await self._agent.generate_question(
            session.card, frequency, retry_count=session.attempt_count,
            recent_errors=recent_errors, conversation_summary=last_summary,
            forced_type=forced_type,
        )
        session.current_question = question
        return question

    async def _end_session(self, outcome: str) -> None:
        if not self._active:
            return
        session = self._active
        self._active = None
        recent_errors = self._get_recent_errors(session.card.card_id)
        summary = await self._agent.generate_session_summary(
            session.card.front, session.card.back,
            session.messages, recent_errors,
        )
        self._update_db_session(
            session.db_session_id,
            outcome=outcome,
            summary=summary,
            messages=json.dumps(session.messages),
            attempt_count=session.attempt_count,
        )

    async def _get_or_classify_frequency(self, card: CardData) -> str:
        existing = {t for t in card.tags if t in _SENSEI_FREQ_TAGS}
        if existing:
            return existing.pop().removeprefix("sensei:")
        frequency = await self._agent.classify_word_frequency(card)
        tag = f"sensei:{frequency}"
        await self._run_anki(self._anki.update_card_tags, card.card_id, [tag])
        card.tags.append(tag)
        return frequency

    def _create_db_session(self, card_id: int) -> int:
        with Session(self._engine) as s:
            cs = ConversationSession(card_id=card_id)
            s.add(cs)
            s.commit()
            s.refresh(cs)
            return cs.id

    def _update_db_session(self, session_id: int, **kwargs) -> None:
        with Session(self._engine) as s:
            cs = s.get(ConversationSession, session_id)
            if cs:
                for k, v in kwargs.items():
                    setattr(cs, k, v)
                cs.ended_at = datetime.utcnow()
                s.add(cs)
                s.commit()

    def _update_attempt_count(self, session_id: int, count: int) -> None:
        with Session(self._engine) as s:
            cs = s.get(ConversationSession, session_id)
            if cs:
                cs.attempt_count = count
                s.add(cs)
                s.commit()

    def _record_error(self, card_id: int, error_type: str, user_answer: str) -> None:
        with Session(self._engine) as s:
            s.add(ErrorRecord(card_id=card_id, error_type=error_type, user_answer=user_answer))
            s.commit()

    def _get_recent_errors(self, card_id: int, limit: int = 5) -> list[str]:
        with Session(self._engine) as s:
            errors = s.exec(
                select(ErrorRecord)
                .where(ErrorRecord.card_id == card_id)
                .order_by(ErrorRecord.created_at.desc())
                .limit(limit)
            ).all()
        return [f"{e.error_type}: {e.user_answer}" for e in reversed(errors)]

    def _get_last_summary(self, card_id: int) -> str | None:
        with Session(self._engine) as s:
            cs = s.exec(
                select(ConversationSession)
                .where(ConversationSession.card_id == card_id)
                .where(ConversationSession.summary.is_not(None))
                .order_by(ConversationSession.started_at.desc())
                .limit(1)
            ).first()
        return cs.summary if cs else None

    async def _run_anki(self, fn, *args):
        async with AnkiClient._collection_lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, fn, *args)
```

- [ ] **Step 4: Run tests**

```bash
uv run python -m pytest tests/agent/test_state_machine.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run full test suite**

```bash
uv run python -m pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/agent/state_machine.py tests/agent/test_state_machine.py
git commit -m "feat(agent): add QuizStateMachine with persistent sessions and error tracking"
```

---

### Task 7: Update `src/bot/keyboards.py` — add `mode_keyboard`

**Files:**
- Modify: `src/bot/keyboards.py`

- [ ] **Step 1: Add `mode_keyboard` to end of file**

```python
def mode_keyboard(current_mode: str) -> InlineKeyboardMarkup:
    """Three mode buttons; current mode is marked with ✓."""
    def label(mode: str, text: str) -> str:
        return f"✓ {text}" if current_mode == mode else text

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label("default", "📚 Due + New"), callback_data="mode_select:default")],
        [InlineKeyboardButton(label("due", "🔁 Due only"), callback_data="mode_select:due")],
        [InlineKeyboardButton(label("new", "✨ New only"), callback_data="mode_select:new")],
    ])
```

- [ ] **Step 2: Commit**

```bash
git add src/bot/keyboards.py
git commit -m "feat(keyboards): add mode_keyboard"
```

---

### Task 8: Rewrite `src/bot/handlers.py`

**Files:**
- Modify: `src/bot/handlers.py`

Replace the `make_handlers` function to use `QuizStateMachine` instead of `QuizEngine`. The public interface (handler dict keys) stays identical so `main.py` changes are minimal.

- [ ] **Step 1: Write the full updated `src/bot/handlers.py`**

```python
import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.agent.state_machine import QuizStateMachine
from src.anki.sync import AnkiSyncer
from src.bot.keyboards import (
    after_answer_keyboard,
    deck_list_keyboard,
    mode_keyboard,
    question_keyboard,
    session_summary_keyboard,
)

logger = logging.getLogger(__name__)

_OUTCOME_LABEL = {
    "correct": "✅ 正確！",
    "semantic_correct": "🔄 語意正確，但讓我們練習造句",
    "grammar_error": "📝 文法有誤，再試試造句",
    "vocab_error": "📖 單字有誤，重新練習",
    "wrong": "❌ 答錯了",
}


def make_handlers(sm: QuizStateMachine, syncer: AnkiSyncer) -> dict:
    """Returns handler dict. sm = QuizStateMachine."""

    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "👋 Welcome to Sensei!\n\n"
            "Commands:\n"
            "/quiz — Start a review session\n"
            "/decks — Choose which deck to study\n"
            "/mode — Choose card mode (due / new / both)\n"
            "/status — Check how many cards are due\n"
            "/stop — End current session\n"
        )
        await update.message.reply_text(text)

    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        count = sm.get_due_count_sync()
        await update.message.reply_text(f"📚 {count} card(s) due for review.")

    async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        if sm.has_active_session():
            await update.message.reply_text("Already in a session. Use /stop to end it first.")
            return
        await update.message.reply_text("⏳ Syncing with AnkiWeb...")
        question = await sm.start(user_id)
        if question is None:
            await update.message.reply_text("🎉 No cards due! Come back later.")
            return
        await _send_question(update, question)

    async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not sm.has_active_session():
            return
        result = await sm.submit_answer(update.message.text)
        label = _OUTCOME_LABEL.get(result.outcome, result.outcome)
        text = f"{label}\n\n{result.suggestion}\n\n💡 Answer: {result.correct_answer}"

        if result.session_ended and result.new_question:
            await update.message.reply_text(text)
            await _send_question(update, result.new_question)
        elif result.session_ended and not result.new_question:
            await update.message.reply_text(f"{text}\n\n🎉 No more cards due!")
        elif result.new_question:
            await update.message.reply_text(text)
            await _send_question(update, result.new_question)

    async def skip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        if not sm.has_active_session():
            return
        question = await sm.skip()
        if question:
            await query.edit_message_text("⏭ Skipped")
            await query.message.reply_text(
                _format_question(question),
                reply_markup=question_keyboard(bool(question.hint)),
            )
        else:
            await query.edit_message_text("⏭ Skipped — no more cards due.")

    async def hint_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        question = sm.get_current_question()
        if not question:
            await query.answer()
            return
        hint_text = question.hint if question.hint else "No hint available."
        await query.answer(f"💡 {hint_text}", show_alert=True)

    async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not sm.has_active_session():
            await update.message.reply_text("No active session.")
            return
        await sm.stop()
        await update.message.reply_text("🛑 Session stopped.", reply_markup=session_summary_keyboard())

    async def decks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        deck_names = await sm.get_deck_names()
        user_id = update.effective_user.id
        current = sm.get_deck(user_id)
        header = f"📂 Select a deck\nCurrent: {current or 'All decks'}"
        await update.message.reply_text(header, reply_markup=deck_list_keyboard(deck_names))

    async def deck_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        deck_name = query.data.removeprefix("deck_select:") or None
        sm.set_deck(user_id, deck_name)
        label = deck_name if deck_name else "All decks"
        await query.edit_message_text(f"✅ Deck set to: {label}\nUse /quiz to start.")

    async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        current = sm.get_mode(user_id)
        await update.message.reply_text(
            f"🎛 Select card mode\nCurrent: {current}",
            reply_markup=mode_keyboard(current),
        )

    async def mode_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        mode = query.data.removeprefix("mode_select:")
        sm.set_mode(user_id, mode)
        await query.edit_message_text(
            f"✅ Mode set to: {mode}\nUse /quiz to start.",
        )

    async def new_session_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("Use /quiz to start a new session.")

    async def sync_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        result = await syncer.async_sync()
        msg = "☁ Sync complete!" if result.success else f"⚠ Sync failed: {result.message}"
        await query.edit_message_text(msg)

    async def send_due_notification(context: ContextTypes.DEFAULT_TYPE) -> None:
        count = sm.get_due_count_sync()
        if count > 0:
            await context.bot.send_message(
                chat_id=context.job.chat_id,
                text=f"📚 You have {count} card(s) due. Use /quiz to start!",
            )

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Unhandled exception", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text("⚠️ 系統發生錯誤，請稍後再試。")

    return {
        "start": start_command,
        "quiz": quiz_command,
        "stop": stop_command,
        "status": status_command,
        "decks": decks_command,
        "mode": mode_command,
        "deck_select": deck_select_callback,
        "mode_select": mode_select_callback,
        "handle_answer": handle_answer,
        "skip": skip_callback,
        "hint": hint_callback,
        "new_session": new_session_callback,
        "sync": sync_callback,
        "send_due_notification": send_due_notification,
        "error": error_handler,
    }


def _format_question(question) -> str:
    labels = {"fill_in_blank": "Fill in the blank", "spelling": "Spell it", "sentence": "Make a sentence"}
    q_type = labels.get(question.question_type, question.question_type)
    return f"[{q_type}]\n\n{question.question_text}"


async def _send_question(update: Update, question) -> None:
    await update.message.reply_text(
        _format_question(question),
        reply_markup=question_keyboard(bool(question.hint)),
    )
```

- [ ] **Step 2: Commit**

```bash
git add src/bot/handlers.py
git commit -m "feat(handlers): wire QuizStateMachine, add /mode command"
```

---

### Task 9: Rewrite `src/main.py` + cleanup

**Files:**
- Modify: `src/main.py`
- Modify: `src/quiz/models.py` (keep only `CardData`)
- Delete: `src/quiz/engine.py`, `src/quiz/scorer.py`

- [ ] **Step 1: Write the full updated `src/main.py`**

```python
import asyncio
import logging

from sqlmodel import SQLModel, create_engine
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.agent.gemini_agent import GeminiAgent
from src.agent.state_machine import QuizStateMachine
from src.anki.client import AnkiClient
from src.anki.sync import AnkiSyncer
from src.bot.handlers import make_handlers
from src.config import load_settings
from src.db.models import ConversationSession, ErrorRecord  # noqa: F401 — ensure tables registered
from src.db.prefs import UserPrefs, UserPrefsStore  # noqa: F401


def main() -> None:
    settings = load_settings()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    db_engine = create_engine(f"sqlite:///{settings.prefs_db_path}")
    SQLModel.metadata.create_all(db_engine)

    anki_client = AnkiClient(settings.anki_collection_path)
    anki_syncer = AnkiSyncer(
        settings.anki_collection_path,
        settings.ankiweb_email,
        settings.ankiweb_password,
    )
    prefs_store = UserPrefsStore(settings.prefs_db_path)
    agent = GeminiAgent(api_key=settings.gemini_api_key, model=settings.gemini_model)
    state_machine = QuizStateMachine(anki_client, anki_syncer, agent, prefs_store, db_engine)

    handlers = make_handlers(state_machine, anki_syncer)

    app = Application.builder().token(settings.telegram_token).build()

    app.add_handler(CommandHandler("start", handlers["start"]))
    app.add_handler(CommandHandler("quiz", handlers["quiz"]))
    app.add_handler(CommandHandler("stop", handlers["stop"]))
    app.add_handler(CommandHandler("status", handlers["status"]))
    app.add_handler(CommandHandler("decks", handlers["decks"]))
    app.add_handler(CommandHandler("mode", handlers["mode"]))
    app.add_handler(CallbackQueryHandler(handlers["skip"], pattern="^skip$"))
    app.add_handler(CallbackQueryHandler(handlers["hint"], pattern="^hint$"))
    app.add_handler(CallbackQueryHandler(handlers["new_session"], pattern="^new_session$"))
    app.add_handler(CallbackQueryHandler(handlers["sync"], pattern="^sync$"))
    app.add_handler(CallbackQueryHandler(handlers["deck_select"], pattern=r"^deck_select:"))
    app.add_handler(CallbackQueryHandler(handlers["mode_select"], pattern=r"^mode_select:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers["handle_answer"]))
    app.add_error_handler(handlers["error"])

    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
```

- [ ] **Step 2: Trim `src/quiz/models.py` to `CardData` only**

```python
from dataclasses import dataclass


@dataclass
class CardData:
    card_id: int
    front: str
    back: str
    tags: list[str]
    deck_name: str
```

- [ ] **Step 3: Delete unused files**

```bash
rm src/quiz/engine.py src/quiz/scorer.py
```

- [ ] **Step 4: Verify import and startup**

```bash
uv run python -c "from src.main import main; print('import OK')"
```

Expected: `import OK`

- [ ] **Step 5: Delete old DB and run ruff**

```bash
rm -f data/sensei.db
uv run ruff check src/ && uv run ruff format src/
```

Fix any issues reported.

- [ ] **Step 6: Run full test suite**

```bash
uv run python -m pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/main.py src/quiz/models.py
git rm src/quiz/engine.py src/quiz/scorer.py
git commit -m "feat: replace QuizEngine with QuizStateMachine, wire GeminiAgent"
```

---

### Task 10: Manual end-to-end test

- [ ] **Step 1: Start the bot**

```bash
uv run python -m src.main
```

Expected: bot starts, `data/sensei.db` created with all 3 tables.

- [ ] **Step 2: Test `/mode`**

In Telegram:
1. `/mode` → three mode buttons appear, current one marked ✓
2. Tap "Due only" → "✅ Mode set to: due"
3. `/mode` again → "Due only" now marked ✓

- [ ] **Step 3: Test quiz loop**

1. `/quiz` → question appears (fill_in_blank / spelling / sentence)
2. Answer correctly (exact match for spelling) → feedback + suggestion + next card auto-starts
3. Answer wrongly → same card re-asked with suggestion
4. Skip → new card auto-starts
5. `/stop` → session ends, no new card

- [ ] **Step 4: Test Anki tag write-back**

```bash
uv run python -c "
from src.anki.client import AnkiClient
c = AnkiClient('./data/anki/collection.anki2')
cards = c.get_due_cards(limit=1)
if cards:
    print('tags before:', cards[0].tags)
"
```

After running `/quiz` once, run the same command — the card should now have a `sensei:common` (or `sensei:rare`) tag.

- [ ] **Step 5: Commit if any fixes were needed**

```bash
git add -A && git commit -m "fix: manual test corrections"
```
