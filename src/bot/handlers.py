import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.agent.state_machine import QuizStateMachine
from src.anki.sync import AnkiSyncer
from src.bot.keyboards import (
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

    async def status_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        count = sm.get_due_count_sync()
        await update.message.reply_text(f"📚 {count} card(s) due for review.")

    async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        if sm.has_active_session():
            await update.message.reply_text(
                "Already in a session. Use /stop to end it first."
            )
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
        await update.message.reply_text(
            "🛑 Session stopped.", reply_markup=session_summary_keyboard()
        )

    async def decks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        deck_names = await sm.get_deck_names()
        user_id = update.effective_user.id
        current = sm.get_deck(user_id)
        header = f"📂 Select a deck\nCurrent: {current or 'All decks'}"
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

    async def mode_select_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        mode = query.data.removeprefix("mode_select:")
        sm.set_mode(user_id, mode)
        await query.edit_message_text(
            f"✅ Mode set to: {mode}\nUse /quiz to start.",
        )

    async def new_session_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("Use /quiz to start a new session.")

    async def sync_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        result = await syncer.async_sync()
        msg = (
            "☁ Sync complete!" if result.success else f"⚠ Sync failed: {result.message}"
        )
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
