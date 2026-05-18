from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

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
