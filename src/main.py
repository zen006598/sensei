import asyncio
import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.anki.client import AnkiClient
from src.anki.sync import AnkiSyncer
from src.bot.handlers import make_handlers
from src.config import load_settings
from src.db.prefs import UserPrefsStore
from src.gemini.client import GeminiClient
from src.quiz.engine import QuizEngine
from src.quiz.scorer import Scorer


def main() -> None:
    settings = load_settings()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    anki_client = AnkiClient(settings.anki_collection_path)
    anki_syncer = AnkiSyncer(
        settings.anki_collection_path,
        settings.ankiweb_email,
        settings.ankiweb_password,
    )
    prefs_store = UserPrefsStore(settings.prefs_db_path)
    gemini_client = GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout=settings.gemini_timeout_seconds,
    )
    scorer = Scorer(gemini_client)
    engine = QuizEngine(anki_client, anki_syncer, gemini_client, scorer, prefs_store)

    handlers = make_handlers(engine, anki_syncer)

    app = Application.builder().token(settings.telegram_token).build()

    app.add_handler(CommandHandler("start", handlers["start"]))
    app.add_handler(CommandHandler("quiz", handlers["quiz"]))
    app.add_handler(CommandHandler("stop", handlers["stop"]))
    app.add_handler(CommandHandler("status", handlers["status"]))
    app.add_handler(CommandHandler("decks", handlers["decks"]))
    app.add_handler(CallbackQueryHandler(handlers["skip"], pattern="^skip$"))
    app.add_handler(CallbackQueryHandler(handlers["hint"], pattern="^hint$"))
    app.add_handler(CallbackQueryHandler(handlers["next"], pattern="^next$"))
    app.add_handler(CallbackQueryHandler(handlers["end"], pattern="^end$"))
    app.add_handler(CallbackQueryHandler(handlers["new_session"], pattern="^new_session$"))
    app.add_handler(CallbackQueryHandler(handlers["sync"], pattern="^sync$"))
    app.add_handler(CallbackQueryHandler(handlers["deck_select"], pattern=r"^deck_select:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers["handle_answer"]))

    # TODO: wire daily due-card notification via app.job_queue.run_daily once
    # TELEGRAM_CHAT_ID is added to Settings (needed as job chat_id target).

    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
