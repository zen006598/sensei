import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.agent.state_machine import QuizStateMachine
from src.anki.client import AnkiClient
from src.anki.sync import AnkiSyncer
from src.db.user_prefs_store import UserPrefsStore
from src.bot.keyboards import (
    deck_list_keyboard,
    mode_keyboard,
    question_keyboard,
    session_summary_keyboard,
)

logger = logging.getLogger(__name__)

_OUTCOME_LABEL = {
    "correct": "Correct",
    "semantic_correct": "Meaning correct — let's practice with a sentence",
    "grammar_error": "Grammar error or spelling mistake in sentence — see hint and try again",
    "sentence_vocab_error": "Wrong word in sentence — see hint and try again",
    "vocab_error": "Wrong word — try again",
    "wrong": "Incorrect",
}


def make_handlers(
    sm: QuizStateMachine,
    syncer: AnkiSyncer,
    anki: AnkiClient,
    prefs: UserPrefsStore,
) -> dict:
    _HELP_TEXT = (
        "Commands:\n"
        "/quiz — Start a review session\n"
        "/sync — Sync with AnkiWeb\n"
        "/decks — Choose which deck to study\n"
        "/mode — Choose card mode (due / new / both)\n"
        "/status — Check how many cards are due\n"
        "/stop — End current session\n"
        "/help — Show this help message\n"
    )

    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(f"Welcome to Sensei!\n\n{_HELP_TEXT}")

    async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(_HELP_TEXT)

    async def status_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        count = await anki.get_due_count()
        await update.message.reply_text(f"{count} card(s) due for review.")

    async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        if sm.has_active_session():
            await update.message.reply_text(
                "Already in a session. Use /stop to end it first."
            )
            return
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
        question = await sm.start(user_id)
        if question is None:
            await update.message.reply_text("No cards due! Come back later.")
            return
        await _send_question(update, question)

    async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not sm.has_active_session():
            return
        result = await sm.submit_answer(update.message.text)
        outcome_key = (
            "sentence_vocab_error"
            if result.outcome == "vocab_error" and result.question_type == "sentence"
            else result.outcome
        )
        label = _OUTCOME_LABEL.get(outcome_key, result.outcome)
        is_simple = result.question_type in ("spelling", "fill_in_blank")

        if result.outcome == "correct" and is_simple:
            text = label
        elif result.outcome == "correct" and result.question_type == "sentence":
            suggestion = result.suggestion.strip()
            text = f"{label}\n\n{suggestion}" if suggestion else label
        elif result.outcome in ("wrong", "vocab_error") and is_simple:
            ans = result.correct_answer or ""
            first = ans[0].upper() if ans else "?"
            last = ans[-1].upper() if ans else "?"
            shape = f"{len(ans)} letters · starts with '{first}' · ends with '{last}'"
            hint_line = f"{result.hint}\n\n" if result.hint else ""
            suggestion = result.suggestion.strip()
            suggestion_line = f"{suggestion}\n\n" if suggestion else ""
            text = f"{label}\n\n{suggestion_line}{hint_line}{shape}"
        elif (
            result.outcome in ("grammar_error", "wrong")
            and result.question_type == "sentence"
        ):
            text = f"{label}\n\n{result.suggestion.strip()}\n\nPlease refer to the hint above and try again."
        else:
            suggestion = result.suggestion.strip()
            text = f"{label}\n\nAnswer: {result.correct_answer}\n\n{suggestion}"

        if result.session_ended:
            due_line = (
                f"{result.remaining_due} card(s) remaining"
                if result.remaining_due is not None
                else ""
            )
            footer = f"\n\nSynced\n{due_line}".rstrip()
            if result.new_question:
                await update.message.reply_text(text + footer)
                await _send_question(update, result.new_question)
            else:
                await update.message.reply_text(f"{text}{footer}\n\nNo more cards due!")
        elif result.new_question:
            await update.message.reply_text(text)
            await _send_question(update, result.new_question)

    async def skip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from telegram.error import BadRequest as TgBadRequest

        query = update.callback_query
        try:
            await query.answer()
        except TgBadRequest:
            return
        if not sm.has_active_session():
            return

        await sm.skip()
        try:
            await query.edit_message_text("Skipped")
        except TgBadRequest:
            pass

        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
        question = await sm.start_next()
        if question:
            await query.message.reply_text(
                _format_question(question),
                reply_markup=question_keyboard(bool(question.hint)),
            )
        else:
            await query.message.reply_text("No more cards due.")

    async def dont_know_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        from telegram.error import BadRequest as TgBadRequest

        query = update.callback_query
        try:
            await query.answer()
        except TgBadRequest:
            return
        if not sm.has_active_session():
            return
        question = sm.get_current_question()
        correct_answer = question.correct_answer if question else "—"

        await sm.discard_current()
        try:
            await query.edit_message_text(f"ans: {correct_answer}")
        except TgBadRequest:
            pass

        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
        question_next = await sm.start_next()
        if question_next:
            await query.message.reply_text(
                _format_question(question_next),
                reply_markup=question_keyboard(bool(question_next.hint)),
            )
        else:
            await query.message.reply_text("No more cards due!")

    async def hint_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        question = sm.get_current_question()
        if not question:
            await query.answer()
            return
        hint_text = question.hint if question.hint else "No hint available."
        await query.answer(hint_text, show_alert=True)

    async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not sm.has_active_session():
            await update.message.reply_text("No active session.")
            return
        remaining = await sm.stop()
        await update.message.reply_text(
            f"Session stopped.\n\nSynced\n{remaining} card(s) remaining",
            reply_markup=session_summary_keyboard(),
        )

    async def decks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        deck_names = await anki.get_deck_names()
        user_id = update.effective_user.id
        current = prefs.get_deck(user_id)
        header = f"Select a deck\nCurrent: {current or 'All decks'}"
        await update.message.reply_text(
            header, reply_markup=deck_list_keyboard(deck_names)
        )

    async def deck_select_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        deck_name = query.data.removeprefix("deck_select:") or None
        prefs.set_deck(user_id, deck_name)
        label = deck_name if deck_name else "All decks"
        await query.edit_message_text(f"Deck set to: {label}\nUse /quiz to start.")

    async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        current = prefs.get_mode(user_id)
        await update.message.reply_text(
            f"Select card mode\nCurrent: {current}",
            reply_markup=mode_keyboard(current),
        )

    async def mode_select_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        mode = query.data.removeprefix("mode_select:")
        prefs.set_mode(user_id, mode)
        await query.edit_message_text(
            f"Mode set to: {mode}\nUse /quiz to start.",
        )

    async def new_session_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("Use /quiz to start a new session.")

    async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
        result = await syncer.async_sync()
        due = await anki.get_due_count()
        if result.success:
            await update.message.reply_text(f"Synced\n{due} card(s) remaining")
        else:
            await update.message.reply_text(f"Sync failed: {result.message}")

    async def sync_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        result = await syncer.async_sync()
        due = await anki.get_due_count()
        if result.success:
            await query.edit_message_text(f"Synced\n{due} card(s) remaining")
        else:
            await query.edit_message_text(f"Sync failed: {result.message}")

    async def send_due_notification(context: ContextTypes.DEFAULT_TYPE) -> None:
        count = await anki.get_due_count()
        if count > 0:
            await context.bot.send_message(
                chat_id=context.job.chat_id,
                text=f"You have {count} card(s) due. Use /quiz to start!",
            )

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Unhandled exception", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "An error occurred. Please try again later."
            )

    return {
        "start": start_command,
        "help": help_command,
        "quiz": quiz_command,
        "stop": stop_command,
        "status": status_command,
        "decks": decks_command,
        "mode": mode_command,
        "deck_select": deck_select_callback,
        "mode_select": mode_select_callback,
        "handle_answer": handle_answer,
        "skip": skip_callback,
        "dont_know": dont_know_callback,
        "hint": hint_callback,
        "new_session": new_session_callback,
        "sync_command": sync_command,
        "sync": sync_callback,
        "send_due_notification": send_due_notification,
        "error": error_handler,
    }


def _format_question(question) -> str:
    labels = {
        "fill_in_blank": "Fill in the blank",
        "spelling": "Spell it",
        "sentence": "Make a sentence",
    }
    q_type = labels.get(question.question_type, question.question_type)
    return f"[{q_type}]\n\n{question.question_text}"


async def _send_question(update: Update, question) -> None:
    await update.message.reply_text(
        _format_question(question),
        reply_markup=question_keyboard(bool(question.hint)),
    )
