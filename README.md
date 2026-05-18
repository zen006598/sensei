# Sensei

A Telegram bot that quizzes you on cards due in your Anki collection, using Google Gemini to generate questions and grade answers.

AnkiWeb is the source of truth: the bot syncs from AnkiWeb at the start of each session, runs the quiz, then syncs review results back when the session ends. Runs headless in Docker on a self-hosted Linux server — no Anki Desktop needed on the host.

## How it works

- Three question types: **fill-in-the-blank**, **spelling**, **sentence composition**. Gemini picks the appropriate type per card.
- The first time a card is reviewed, the bot classifies it for frequency (`common` / `rare` / `obsolete`) and register (`formal` / `informal` / `slang` / `literary` / `neutral`), writing the result back as a `sensei:*` Anki tag so future runs skip the classification call.
- Mistakes are recorded per card; the next question on the same card sees your recent errors and the previous session's summary, so Gemini can adapt.
- Ease scoring: perfect on the first attempt → ease 4 (Easy), perfect after retries → ease 3 (Good), skipped / stopped → ease 1 (Again).

## Question selection

### Picking the next card

The bot fetches your due and new cards from the synced collection, shuffles each pool separately, then picks one — due cards always before new cards. Shuffling means skipping or stopping a card doesn't immediately bring it back.

The pool depends on `/mode`:

| Mode | Pool |
|---|---|
| `default` | `is:due` (shuffled), then `is:new` (shuffled) |
| `due` | `is:due` (shuffled) |
| `new` | `is:new` (shuffled) |

### When is a card "done"?

| Frequency | Mastery condition |
|---|---|
| `rare` / `obsolete` | Any **1** correct answer (`fill_in_blank` only) |
| `common` | Correct on `fill_in_blank` **or** `spelling`, **and** on `sentence` |

Once mastered, the card is graded with the ease scoring above, written back to the collection, and synced to AnkiWeb. The bot then auto-picks the next card.

### Picking the question type within a card

The **first** question depends on frequency:

- `rare` / `obsolete` → always `fill_in_blank` (the word is uncommon enough that recognition is the only realistic test)
- `common` → Gemini freely picks `fill_in_blank`, `spelling`, or `sentence`

The **next** question depends on the previous outcome:

| Outcome | Next question type |
|---|---|
| `correct` and mastered | — card ends |
| `correct` but still need the other half | `sentence` if recall already done, else `fill_in_blank` |
| `semantic_correct` (spelling: right meaning, wrong word) | `sentence` |
| `grammar_error` (sentence) | `sentence` — retry |
| `vocab_error` (sentence: wrong word entirely) | `spelling` — drill the word |
| `wrong` | same type — retry |

A spelling answer that matches letter-for-letter (case-insensitive) takes a shortcut: marked correct immediately without calling the grader.

## Bot commands

| Command | What it does |
|---|---|
| `/quiz` | Sync from AnkiWeb, pick one due card, start a session |
| `/sync` | Sync with AnkiWeb without quizzing |
| `/decks` | Choose which deck to study |
| `/mode` | Choose card mode (due / new / both) |
| `/status` | Check how many cards are due |
| `/stop` | End the current session |
| `/help` | Show the command list |

In a session, inline buttons offer `Hint`, `Skip`, and `I don't know`.

## Prerequisites

- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- An AnkiWeb account (the bot syncs `collection.anki2` through AnkiWeb)
- A Google Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey)
- Your Telegram user ID (message [@userinfobot](https://t.me/userinfobot) to find it)
- Docker + Docker Compose on the host

## Deployment

1. Copy `.env.example` to `.env` on the host and fill in the required values.
2. `docker compose up -d`

The compose file pulls `ghcr.io/zen006598/sensei:latest` and mounts two named volumes:

- `anki_data` — the synced `collection.anki2`
- `sensei_db` — per-user preferences and per-card session history

Pushes to `main` trigger `.github/workflows/cd.yml`, which builds the image, pushes to GHCR, and redeploys to the configured host over Tailscale + SSH.

## Configuration

| Variable | Required | Default | Notes |
|---|---|---|---|
| `TELEGRAM_TOKEN` | yes | — | From @BotFather |
| `ANKIWEB_EMAIL` | yes | — | AnkiWeb login |
| `ANKIWEB_PASSWORD` | yes | — | AnkiWeb password |
| `GEMINI_API_KEY` | yes | — | From Google AI Studio |
| `ALLOWED_USER_IDS` | recommended | empty | Comma-separated Telegram user IDs. Empty means allow anyone — only safe if you do not advertise the bot. |
| `GEMINI_MODEL` | no | `gemini-2.5-flash-lite` | Used for quiz, judge, summary |
| `GEMINI_CLASSIFY_MODEL` | no | `gemini-3-flash-lite` | Used only for register classification (lighter call) |
| `ANKI_COLLECTION_PATH` | no | `./data/anki/collection.anki2` | Inside the container it's `/data/anki/collection.anki2` |
| `DB_PATH` | no | `./data/sensei.db` | Inside the container it's `/data/db/sensei.db` |
| `LOG_LEVEL` | no | `INFO` locally, `ERROR` in compose | |

## Local development

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups            # install deps including dev tools
uv run python -m src.main       # run the bot (reads .env in cwd)
uv run pytest -q                # run tests
uv run ruff check src/ tests/   # lint
uv run ruff format src/ tests/  # format
```

See [`CLAUDE.md`](CLAUDE.md) for architecture and design notes.

## Caveats

- The bot supports **one active quiz session at a time** across the whole process. `ALLOWED_USER_IDS` is what prevents users from clobbering each other — keep it configured.
- If Anki Desktop is open on the same machine that holds the collection file, it will lock the database and the bot will fail to read it. The deployment model assumes AnkiWeb is the only mediator.
