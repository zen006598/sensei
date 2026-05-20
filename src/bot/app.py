import logging
from datetime import time, timedelta, timezone

from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.anki.sync import AnkiSyncer
from src.db.user_prefs_store import UserPrefsStore
from src.tts.backfill import run_tts_backfill
from src.tts.generator import TTSGenerator
from src.word_service.backfill import run_full_backfill
from src.word_service.card_tagger import BatchAlreadyRunningError, CardTagger

# Asia/Taipei is permanently UTC+8 (no DST). Hard-coding the offset avoids
# bundling tzdata in the image and the TZ env var.
_LOCAL_TZ = timezone(timedelta(hours=8))

_BOT_COMMANDS = [
    BotCommand("quiz", "Start a review session"),
    BotCommand("sync", "Sync with AnkiWeb"),
    BotCommand("decks", "Choose deck"),
    BotCommand("mode", "Choose card mode"),
    BotCommand("status", "Check due count"),
    BotCommand("stop", "End current session"),
    BotCommand("retag", "Backfill missing sensei:* tags on selected deck"),
    BotCommand("tts", "Generate pronunciation audio for selected deck"),
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


def build_app(token: str, allowed_user_ids: set[int], handlers: dict) -> Application:
    app = Application.builder().token(token).post_init(_post_init).build()

    user_filter = filters.User(user_id=allowed_user_ids) if allowed_user_ids else None
    text_filter = filters.TEXT & ~filters.COMMAND
    if user_filter is not None:
        text_filter = text_filter & user_filter
        app.add_handler(
            CallbackQueryHandler(_make_auth_gate(allowed_user_ids)),
            group=-1,
        )

    app.add_handler(CommandHandler("start", handlers["start"], filters=user_filter))
    app.add_handler(CommandHandler("help", handlers["help"], filters=user_filter))
    app.add_handler(CommandHandler("quiz", handlers["quiz"], filters=user_filter))
    app.add_handler(CommandHandler("stop", handlers["stop"], filters=user_filter))
    app.add_handler(CommandHandler("status", handlers["status"], filters=user_filter))
    app.add_handler(CommandHandler("retag", handlers["retag"], filters=user_filter))
    app.add_handler(CommandHandler("tts", handlers["tts"], filters=user_filter))
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

    return app


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
