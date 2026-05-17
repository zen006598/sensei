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
    state_machine = QuizStateMachine(
        anki_client, anki_syncer, agent, prefs_store, db_engine
    )

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
    app.add_handler(
        CallbackQueryHandler(handlers["new_session"], pattern="^new_session$")
    )
    app.add_handler(CallbackQueryHandler(handlers["sync"], pattern="^sync$"))
    app.add_handler(
        CallbackQueryHandler(handlers["deck_select"], pattern=r"^deck_select:")
    )
    app.add_handler(
        CallbackQueryHandler(handlers["mode_select"], pattern=r"^mode_select:")
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handlers["handle_answer"])
    )
    app.add_error_handler(handlers["error"])

    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
