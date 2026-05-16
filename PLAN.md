# Sensei — Anki AI Quiz Bot: Implementation Plan

## Overview

A Telegram bot that:
- Reads due Anki cards (following the SM-2 spaced-repetition curve)
- Uses Google Gemini CLI to generate quiz questions
- Scores user answers and updates Anki review results
- Deployed on **your own self-hosted Linux server** (not your personal computer) via Docker Compose
- Supports any language (multilingual cards)

---

## Deployment Model

```
[你的電腦 — Anki Desktop]
         │
         │  sync (手動或自動)
         ▼
    [AnkiWeb 雲端]
         │
         │  sync (每次 /quiz 前自動拉取)
         ▼
[你的 Linux Server — Docker]
   Bot 常駐在這裡跑
   headless，無 GUI，無 Anki Desktop
         │
         │  sync (每次 /stop 後自動推回)
         ▼
    [AnkiWeb 雲端]
         │
         │  下次你開 Anki Desktop 時同步
         ▼
[你的電腦 — Anki Desktop]
```

**重點：**
- Server 上**沒有** Anki Desktop，也沒有 GUI
- Bot 透過 `anki` Python package 直接讀寫本地 collection.anki2 檔案
- Collection 檔案從 AnkiWeb 同步取得，結果也推回 AnkiWeb
- Server 上的 `data/anki/` 目錄是 collection 的存放位置（bind mount 進 Docker）
- 你在電腦上用 Anki Desktop 正常複習後，Bot 也會拿到最新進度

---

## Architecture Data Flow

```
[Telegram User]
       |
[python-telegram-bot Application]
       |
[bot/handlers.py]
       |
[quiz/engine.py] ─── async ──→ [gemini/client.py] → gemini CLI subprocess
       |
       └─── run_in_executor ──→ [anki/client.py] → collection.anki2 (SQLite)
                                [anki/sync.py]   → AnkiWeb (before/after session)

[job_queue] → daily due-count notification
```

---

## Target File Structure

```
sensei/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── src/
│   ├── __init__.py
│   ├── main.py                 # Entry point: wires app, registers handlers, starts polling
│   ├── config.py               # Typed Settings dataclass from env vars
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── handlers.py         # All PTB CommandHandlers + MessageHandler
│   │   └── keyboards.py        # Inline keyboard builders
│   ├── anki/
│   │   ├── __init__.py
│   │   ├── client.py           # AnkiClient: get_due_cards, answer_card (run_in_executor)
│   │   └── sync.py             # AnkiSyncer: sync to/from AnkiWeb
│   ├── quiz/
│   │   ├── __init__.py
│   │   ├── engine.py           # QuizEngine: session lifecycle orchestration
│   │   ├── models.py           # QuizSession, QuizQuestion, CardData, QuizType
│   │   └── scorer.py           # Scorer: Gemini scoring + local fallback
│   └── gemini/
│       ├── __init__.py
│       └── client.py           # GeminiClient: async subprocess wrapper + prompt templates
└── data/anki/                  # Docker volume mount for collection.anki2
```

---

## Module Designs

### `src/config.py`

```python
@dataclass
class Settings:
    telegram_token: str
    ankiweb_email: str
    ankiweb_password: str
    anki_collection_path: str   # default: /data/anki/collection.anki2
    gemini_api_key: str | None
    gemini_timeout_seconds: int # default: 30
    scheduler_daily_hour: int   # default: 8
    max_cards_per_session: int  # default: 20

def load_settings() -> Settings: ...  # reads os.environ, raises ValueError if missing
```

---

### `src/quiz/models.py`

```python
class QuizType(Enum):
    FILL_IN_BLANK = "fill_in_blank"
    SPELLING = "spelling"

@dataclass
class CardData:
    card_id: int; front: str; back: str; tags: list[str]; deck_name: str

@dataclass
class QuizQuestion:
    card_data: CardData; quiz_type: QuizType
    question_text: str; correct_answer: str
    acceptable_alternates: list[str]; hint: str

@dataclass
class QuizSession:
    user_id: int; pending_cards: list[CardData]
    current_question: QuizQuestion | None
    cards_done: int; correct_count: int
    is_active: bool; ease_history: list[int]

@dataclass
class AnswerResult:
    ease: int; feedback: str; correct_answer: str

@dataclass
class SessionSummary:
    user_id: int; cards_done: int; correct_count: int; ease_history: list[int]
```

---

### `src/gemini/client.py`

使用 `google-generativeai` Python SDK，透過 API key 直接呼叫，不使用 subprocess：

```python
import asyncio
import json
import google.generativeai as genai
from google.generativeai.types import GenerationConfig

class GeminiClient:
    def __init__(self, api_key: str, model: str = "gemini-2.0-flash", timeout: int = 30):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            model,
            generation_config=GenerationConfig(response_mime_type="application/json"),
        )
        self.timeout = timeout

    async def call(self, prompt: str) -> dict:
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: self.model.generate_content(prompt)),
            timeout=self.timeout,
        )
        return json.loads(response.text)

    async def generate_question(self, card: CardData, quiz_type: QuizType) -> QuizQuestion: ...
    async def score_answer(self, question: QuizQuestion, user_answer: str) -> tuple[int, str]: ...
```

**重點：**
- `response_mime_type="application/json"` 讓 Gemini 直接輸出乾淨的 JSON，不需要 strip ANSI / markdown fences
- `run_in_executor` 讓同步的 SDK call 不阻塞 asyncio event loop
- 認證只需 `GEMINI_API_KEY` 環境變數

**認證方式：**
- `.env` → `GEMINI_API_KEY=AIza...`
- Docker Compose 透過 `environment` 傳入 container

**Fill-in-blank prompt template:**
```
You are a language learning quiz generator.

Given this Anki card:
Front: {front}
Back: {back}
Tags: {tags}

Create a fill-in-the-blank sentence where the key answer replaces "___".
Provide enough context in the sentence for the learner to guess.
Keep the question in the card's language.

Respond ONLY with valid JSON (no markdown, no extra text):
{"question_text": "Complete: ___", "correct_answer": "exact word/phrase", "hint": "optional hint or empty string"}
```

**Spelling/direct-input prompt template:**
```
You are a language learning quiz generator.

Given this Anki card:
Front: {front}
Back: {back}
Tags: {tags}

Create a direct recall question asking the learner to type the answer from memory.
Keep the question in the card's language.

Respond ONLY with valid JSON (no markdown, no extra text):
{"question_text": "...", "correct_answer": "primary correct answer", "acceptable_alternates": ["alt1", "alt2"], "hint": ""}
```

**Scoring prompt template:**
```
Grade the learner's answer using the Anki SM-2 ease scale (1–4):
1 = Again (completely wrong)
2 = Hard (partial recall, significant errors)
3 = Good (correct with minor errors or typos)
4 = Easy (perfectly correct, immediate recall)

Rules:
- Minor typos (1–2 chars) in long words → ease 3
- Semantically equivalent answers → ease 3 or 4
- Ignore capitalisation differences
- Accept correct answers in the card's language

Question: {question_text}
Correct answer: {correct_answer}
Learner's answer: {user_answer}

Respond ONLY with valid JSON (no markdown, no extra text):
{"ease": 3, "feedback": "brief explanation"}
```

---

### `src/anki/client.py`

```python
class AnkiClient:
    _collection_lock = asyncio.Lock()   # class-level: one user at a time

    @contextmanager
    def _get_collection(self): ...      # open → yield → close (always)

    def get_due_cards(self, limit: int) -> list[CardData]: ...   # sync, run in executor
    def answer_card(self, card_id: int, ease: int) -> None: ...  # sync, run in executor
    def get_due_count(self) -> int: ...                          # sync, run in executor
    def _card_to_data(self, col, card_id: int) -> CardData: ...  # strips HTML via BS4
```

Key: `_card_to_data()` strips HTML with BeautifulSoup. Cards with image-only backs are skipped.

---

### `src/anki/sync.py`

```python
class AnkiSyncer:
    def sync(self) -> SyncResult: ...          # sync to/from AnkiWeb (sync, run in executor)
    async def async_sync(self) -> SyncResult: ...
```

Sync timing:
- **Before** `start_session()` — pull latest due cards from AnkiWeb
- **After** `end_session()` — push review results back
- NOT after every card answer (avoids AnkiWeb rate limits)

---

### `src/quiz/scorer.py`

```python
class Scorer:
    async def score(self, question: QuizQuestion, user_answer: str) -> int:
        # Calls Gemini, falls back to local heuristic on failure
        # Local: exact match=4, near match (Levenshtein≤2)=3, alternate=3, else=1

    def _local_score(self, question: QuizQuestion, user_answer: str) -> int: ...
```

---

### `src/quiz/engine.py`

```python
class QuizEngine:
    _sessions: dict[int, QuizSession] = {}   # keyed by telegram user_id

    async def start_session(self, user_id: int, max_cards: int) -> QuizSession
    async def next_question(self, user_id: int) -> QuizQuestion | None
    async def submit_answer(self, user_id: int, user_answer: str) -> AnswerResult
    async def end_session(self, user_id: int) -> SessionSummary

    async def _run_anki(self, fn, *args):     # acquires lock, runs in executor
        async with AnkiClient._collection_lock:
            return await loop.run_in_executor(None, fn, *args)
```

---

### `src/bot/handlers.py`

```python
# Commands
async def start_command(update, context)    # welcome + command list
async def quiz_command(update, context)     # start session, send first question
async def stop_command(update, context)     # end session early, show summary + sync
async def status_command(update, context)   # show due card count (no session started)

# Message handler
async def handle_answer(update, context)    # free text → score answer → feedback → next

# Callback query handlers
async def skip_callback(update, context)    # ease=1 (Again), load next card
async def hint_callback(update, context)    # reveal hint text
async def next_callback(update, context)    # load next question after seeing feedback

# Scheduled job
async def send_due_notification(context)    # daily at scheduler_daily_hour
```

---

### `src/bot/keyboards.py`

```python
def question_keyboard(has_hint: bool) -> InlineKeyboardMarkup:
    # [💡 Hint] (if has_hint)  [⏭ Skip]

def after_answer_keyboard() -> InlineKeyboardMarkup:
    # [➡ Next Card]  [🛑 End Session]

def session_summary_keyboard() -> InlineKeyboardMarkup:
    # [🔄 New Session]  [☁ Sync Now]
```

---

### `src/main.py`

Wires all components, registers handlers, configures daily job, starts PTB polling.

---

## Docker Configuration

### `Dockerfile`

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y \
    pkg-config libssl-dev libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
RUN mkdir -p /data/anki
CMD ["python", "-m", "src.main"]
```

Node.js 不再需要。Image 顯著變小。

### `docker-compose.yml`

```yaml
services:
  sensei:
    build: .
    restart: unless-stopped
    environment:
      - TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
      - ANKIWEB_EMAIL=${ANKIWEB_EMAIL}
      - ANKIWEB_PASSWORD=${ANKIWEB_PASSWORD}
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - ANKI_COLLECTION_PATH=/data/anki/collection.anki2
      - SCHEDULER_DAILY_HOUR=8
      - MAX_CARDS_PER_SESSION=20
      - GEMINI_TIMEOUT=30
    volumes:
      - type: bind
        source: /home/youruser/anki-data   # ← server 上存放 collection.anki2 的目錄
        target: /data/anki
    # 注意：這個目錄在 server 上，不是你的電腦
    # Bot 會在這裡讀寫 collection.anki2，透過 AnkiWeb API 同步
```

### `.env.example`

```
TELEGRAM_TOKEN=7xxxxxxxxxx:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ANKIWEB_EMAIL=user@example.com
ANKIWEB_PASSWORD=yourpassword
GEMINI_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ANKI_COLLECTION_PATH=/data/anki/collection.anki2
SCHEDULER_DAILY_HOUR=8
MAX_CARDS_PER_SESSION=20
GEMINI_TIMEOUT=30
```

### `requirements.txt`

```
python-telegram-bot[job-queue]==21.9
google-generativeai>=0.8.0
anki==24.11
beautifulsoup4==4.12.3
lxml==5.3.0
python-levenshtein==0.26.1
python-dotenv==1.0.1
structlog==24.4.0

# dev
ruff
```

---

## Concurrency Design

| Concern | Solution |
|---|---|
| Multiple users at once | `QuizEngine._sessions` dict keyed by `user_id`; PTB delivers messages per-user sequentially |
| Anki file lock | `asyncio.Lock()` on `AnkiClient` class; only one operation opens collection at a time |
| Gemini concurrent calls | Safe — each is an independent subprocess |
| Anki sync vs quiz overlap | Same lock covers sync operations |

---

## Build Order (dependency-safe)

1. `src/config.py` + all `__init__.py` files
2. `src/quiz/models.py`
3. `src/gemini/client.py`
4. `src/anki/client.py`
5. `src/anki/sync.py`
6. `src/quiz/scorer.py`
7. `src/quiz/engine.py`
8. `src/bot/keyboards.py`
9. `src/bot/handlers.py`
10. `src/main.py`
11. `Dockerfile` + `docker-compose.yml` + `scripts/install_gemini.sh`
12. `requirements.txt` + `.env.example`

---

## Verification Steps

1. **Anki smoke test**: `python -c "import anki; col = anki.Collection('path'); print(len(col.find_cards('is:due'))); col.close()"`
2. **Gemini CLI smoke test**: `docker compose run --rm sensei bash -c "gemini --prompt 'Return JSON: {\"ok\": true}'"`
3. **Manual Telegram test**: `/status` → due count; `/quiz` → question; answer → feedback; `/stop` → summary
4. **AnkiWeb check**: verify reviews appear in AnkiWeb after bot session ends
5. **Concurrent test**: two Telegram accounts quiz simultaneously — sessions must not interfere

---

## Anticipated Challenges

- **Anki sync API**: Internal and unstable between versions. Pinned to `anki==24.11`. Verify sync invocation against package source.
- **Gemini CLI flags**: Confirm `--prompt` is the correct flag for `@google/gemini-cli`. May need `--no-color` to avoid ANSI codes polluting JSON output.
- **Anki collection lock**: If Anki Desktop runs on the same machine, it will conflict. Bot should catch the error and inform the user.
- **HTML-only cards**: Cards where back field is image-only should be skipped gracefully.
