import asyncio
import logging

from sqlmodel import SQLModel, create_engine
from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
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
from src.db.conversation_session import ConversationSession  # noqa: F401 — ensure table registered
from src.db.conversation_session_store import ConversationSessionStore
from src.db.error_record import ErrorRecord  # noqa: F401 — ensure table registered
from src.db.error_record_store import ErrorRecordStore
from src.db.user_prefs_store import UserPrefsStore
from src.db.user_prefs import UserPrefs  # noqa: F401 — ensure table registered

_BOT_COMMANDS = [
    BotCommand("quiz", "Start a review session"),
    BotCommand("sync", "Sync with AnkiWeb"),
    BotCommand("decks", "Choose deck"),
    BotCommand("mode", "Choose card mode"),
    BotCommand("status", "Check due count"),
    BotCommand("stop", "End current session"),
    BotCommand("help", "Show this help message"),
]


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(_BOT_COMMANDS)


def _make_auth_gate(allowed: set[int]):
    async def _auth_gate(update, _ctx) -> None:
        user = update.effective_user
        if user is None or user.id not in allowed:
            raise ApplicationHandlerStop

    return _auth_gate


def main() -> None:
    settings = load_settings()

    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("google_genai").setLevel(logging.WARNING)

    db_engine = create_engine(f"sqlite:///{settings.db_path}")
    SQLModel.metadata.create_all(db_engine)

    anki_client = AnkiClient(settings.anki_collection_path)
    anki_syncer = AnkiSyncer(
        settings.anki_collection_path,
        settings.ankiweb_email,
        settings.ankiweb_password,
    )
    prefs_store = UserPrefsStore(settings.db_path, engine=db_engine)
    errors_store = ErrorRecordStore(db_engine)
    sessions_store = ConversationSessionStore(db_engine)
    agent = GeminiAgent(api_key=settings.gemini_api_key, model=settings.gemini_model)
    state_machine = QuizStateMachine(
        anki_client, anki_syncer, agent, prefs_store, errors_store, sessions_store
    )

    handlers = make_handlers(state_machine, anki_syncer, prefs_store)

    app = (
        Application.builder()
        .token(settings.telegram_token)
        .post_init(_post_init)
        .build()
    )

    user_filter = (
        filters.User(user_id=settings.allowed_user_ids)
        if settings.allowed_user_ids
        else None
    )
    text_filter = filters.TEXT & ~filters.COMMAND
    if user_filter is not None:
        text_filter = text_filter & user_filter
        app.add_handler(
            CallbackQueryHandler(_make_auth_gate(settings.allowed_user_ids)),
            group=-1,
        )

    app.add_handler(CommandHandler("start", handlers["start"], filters=user_filter))
    app.add_handler(CommandHandler("help", handlers["help"], filters=user_filter))
    app.add_handler(CommandHandler("quiz", handlers["quiz"], filters=user_filter))
    app.add_handler(CommandHandler("stop", handlers["stop"], filters=user_filter))
    app.add_handler(CommandHandler("status", handlers["status"], filters=user_filter))
    app.add_handler(
        CommandHandler("sync", handlers["sync_command"], filters=user_filter)
    )
    app.add_handler(CommandHandler("decks", handlers["decks"], filters=user_filter))
    app.add_handler(CommandHandler("mode", handlers["mode"], filters=user_filter))
    app.add_handler(CallbackQueryHandler(handlers["skip"], pattern="^skip$"))
    app.add_handler(CallbackQueryHandler(handlers["dont_know"], pattern="^dont_know$"))
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
    app.add_handler(MessageHandler(text_filter, handlers["handle_answer"]))
    app.add_error_handler(handlers["error"])

    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
