# Agent Quiz System Design

**Date:** 2026-05-17

**Goal:** Replace the current stateless quiz engine with a Gemini-powered agent that handles question generation, scoring judgment, and suggestion — while a Python state machine owns session lifecycle and persistence.

**Architecture:** Hybrid — Python state machine controls session flow; Gemini function calling handles all language decisions (word frequency judgment, question generation, scoring, suggestion). SQLite (SQLModel) persists all state.

**Stack:** Python, `google-genai` SDK, Gemini 2.5 Flash Lite, SQLModel, python-telegram-bot.

---

## Architecture Overview

```
Telegram → bot/handlers.py
               ↓
          QuizStateMachine (Python)
          ├── session lifecycle (start / end / retry)
          ├── exact match check (spelling)
          └── DB reads/writes
               ↓
          GeminiAgent
          ├── tool: quiz(card, retry_count, errors) → question
          ├── tool: judge_score(question, answer)   → result + suggestion
          └── tool: suggest(question, answer)       → suggestion text
               ↓
          SQLite (SQLModel)
          ├── UserPrefs          (existing — deck, mode)
          ├── ConversationSession
          └── ErrorRecord
```

---

## 1. 選題 (Card Selection)

Three modes per user, persisted in existing `UserPrefs`:

| Mode | Query |
|---|---|
| `default` | `is:due OR is:new` |
| `due` | `is:due` |
| `new` | `is:new` |

`/decks` already supports deck selection. A new `/mode` command (or inline button) lets the user switch mode. Selection persisted to `UserPrefs.quiz_mode`.

---

## 2. Session Control (State Machine)

Each session focuses on **one card** drilled until perfect mastery.

### Transitions

```
[select card]
      ↓
[ACTIVE] ←─────────────────────────────────┐
      ↓                                     │
 user answers                               │
      ↓                                     │
 spelling?                                  │
   exact match ──→ ease 4 → END → auto new session
   not match   ──→ agent judges:            │
     semantic correct, diff word ──→ 造句題 (同 session)
     wrong ──────────────────────────────→ retry (re-ask) ─┘
                                            │
 造句題?                                    │
   agent judges:                            │
     grammar error ──→ record ErrorRecord → retry 造句題 ─┘
     vocab error ───→ back to spelling ─────┘

skip  ──→ END → auto new session
/stop ──→ END, no new session
no more cards ──→ END, notify user
```

### Session persistence

`ConversationSession` is written to DB at session start and updated on each state change. Bot restart resumes the active session.

---

## 3. 出題 (Question Generation)

### Word Frequency Judgment (cached in Anki tags)

On first encounter, agent judges the card's word frequency and writes back to Anki:

| Tag | Question types allowed |
|---|---|
| `sensei:common` | fill-in-blank, spelling, 造句 (random) |
| `sensei:rare` | fill-in-blank only |
| `sensei:obsolete` | fill-in-blank only |

If tag already exists → skip agent judgment, use cached tag. Tags use `sensei:` namespace to avoid collision with user's own tags.

`AnkiClient` gains `update_card_tags(card_id, tags_to_add: list[str])`.

### quiz tool

```python
quiz(
    card_front: str,
    card_back: str,
    card_tags: list[str],
    retry_count: int,
    recent_errors: list[str],   # from ErrorRecord for this card
    conversation_summary: str,  # from last ConversationSession.summary
) → QuizQuestion
```

Agent decides on retry whether to use the same angle or a different one.

---

## 4. Score & Suggestion

### Spelling flow

1. Python checks `user_answer.strip().lower() == correct_answer.strip().lower()`
   - Match → ease 4, end session, auto new
   - No match → call `judge_score` tool

### judge_score tool

```python
judge_score(
    question_type: str,         # "spelling" | "sentence"
    question_text: str,
    correct_answer: str,
    user_answer: str,
) → JudgeResult(
    outcome: str,               # "correct" | "semantic_correct" | "grammar_error" | "vocab_error" | "wrong"
    error_type: str | None,     # "grammar" | "vocabulary" | "spelling"
    suggestion: str,            # always present
)
```

### Suggestion

Always generated as part of `judge_score`. Never a separate call.

- Correct → more natural / idiomatic alternative
- Grammar error → correction + explanation
- Vocab error → meaning clarification

---

## 5. Memory

### Conversation compression

`ConversationSession.messages` stores the last 3 user messages as JSON. Older messages are dropped. On session end, Gemini generates a natural-language `summary` of the session stored in `ConversationSession.summary`.

### Error context for question generation

When generating a question for a card, query `ErrorRecord` for that `card_id` and pass the most recent N errors to the `quiz` tool as `recent_errors`.

---

## 6. Data Model

Single-user service — no `user_id` on new tables.

```python
class ConversationSession(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int                        # soft FK → Anki card
    started_at: datetime
    ended_at: datetime | None = None
    outcome: str | None = None          # "perfect" | "skipped" | "stopped"
    summary: str | None = None          # Gemini NL summary of this session
    messages: str | None = None         # JSON: last 3 user messages
    attempt_count: int = 0

class ErrorRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int                        # soft FK → Anki card (shared across sessions)
    error_type: str                     # "grammar" | "vocabulary" | "spelling"
    user_answer: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

`UserPrefs` (existing) gains `quiz_mode: str = "default"`.

---

## 7. New Files

| File | Action | Responsibility |
|---|---|---|
| `src/db/models.py` | Create | `ConversationSession`, `ErrorRecord` SQLModel tables |
| `src/agent/gemini_agent.py` | Create | Gemini function calling wrapper, tool definitions |
| `src/agent/state_machine.py` | Create | `QuizStateMachine` — session lifecycle, exact match, state transitions |
| `src/agent/tools.py` | Create | Tool implementations: `quiz`, `judge_score` |
| `src/anki/client.py` | Modify | Add `update_card_tags()` |
| `src/db/prefs.py` | Modify | Add `quiz_mode` to `UserPrefs` |
| `src/quiz/engine.py` | Replace | Superseded by `QuizStateMachine` |
| `src/bot/handlers.py` | Modify | Wire new state machine, add `/mode` command |
| `src/main.py` | Modify | Init new components |

---

## 8. Out of Scope

- Multi-user support
- Media cards (image-only fronts/backs)
- Push notifications (existing TODO)
- AnkiWeb sync changes
