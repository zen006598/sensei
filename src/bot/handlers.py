import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

from src.anki.sync import AnkiSyncer
from src.bot.keyboards import (
    after_answer_keyboard,
    deck_list_keyboard,
    question_keyboard,
    session_summary_keyboard,
)
from src.quiz.engine import QuizEngine
from src.quiz.models import SessionSummary


def make_handlers(engine: QuizEngine, syncer: AnkiSyncer):
    """Returns a dict of all handler functions, each closed over engine and syncer."""

    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "👋 Welcome to Sensei!\n\n"
            "Commands:\n"
            "/quiz — Start a review session\n"
            "/decks — Choose which deck to study\n"
            "/status — Check how many cards are due\n"
            "/stop — End current session\n"
        )
        await update.message.reply_text(text)

    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        count = engine.get_due_count_sync()
        await update.message.reply_text(f"📚 {count} card(s) due for review.")

    async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        if engine.has_active_session(user_id):
            await update.message.reply_text("You already have an active session. Use /stop to end it first.")
            return
        await update.message.reply_text("⏳ Starting session, syncing with AnkiWeb...")
        session = await engine.start_session(user_id, max_cards=20)
        if not session.pending_cards:
            await update.message.reply_text("🎉 No cards due! Come back later.")
            return
        question = await engine.next_question(user_id)
        await _send_question(update, question)

    async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        if not engine.has_active_session(user_id):
            return
        user_answer = update.message.text
        result = await engine.submit_answer(user_id, user_answer)
        ease_label = {1: "Again ❌", 2: "Hard 😓", 3: "Good ✅", 4: "Easy 🌟"}[result.ease]
        text = f"{ease_label}\n{result.feedback}\n\n💡 Answer: {result.correct_answer}"
        await update.message.reply_text(text, reply_markup=after_answer_keyboard())

    async def skip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        if not engine.has_active_session(user_id):
            return
        result = await engine.submit_answer(user_id, "")
        text = f"⏭ Skipped\n\n💡 Answer: {result.correct_answer}"
        await query.edit_message_text(text, reply_markup=after_answer_keyboard())

    async def hint_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user_id = update.effective_user.id
        question = engine.get_current_question(user_id)
        if not question:
            await query.answer()
            return
        q = question
        hint_text = q.hint if q.hint else "No hint available."
        await query.answer(f"💡 {hint_text}", show_alert=True)

    async def next_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        question = await engine.next_question(user_id)
        if question is None:
            summary = await engine.end_session(user_id)
            await query.edit_message_text(_format_summary(summary), reply_markup=session_summary_keyboard())
        else:
            await query.edit_message_text(_format_question(question), reply_markup=question_keyboard(bool(question.hint)))

    async def end_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        summary = await engine.end_session(user_id)
        await query.edit_message_text(_format_summary(summary), reply_markup=session_summary_keyboard())

    async def new_session_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("Use /quiz to start a new session.")

    async def sync_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        result = await syncer.async_sync()
        msg = "☁ Sync complete!" if result.success else f"⚠ Sync failed: {result.message}"
        await query.edit_message_text(msg)

    async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        if not engine.has_active_session(user_id):
            await update.message.reply_text("No active session.")
            return
        summary = await engine.end_session(user_id)
        await update.message.reply_text(_format_summary(summary), reply_markup=session_summary_keyboard())

    async def decks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        deck_names = await engine.get_deck_names()
        current = engine.get_deck(update.effective_user.id)
        header = f"📂 Select a deck to study\nCurrent: {current or 'All decks'}"
        await update.message.reply_text(header, reply_markup=deck_list_keyboard(deck_names))

    async def deck_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        deck_name = query.data.removeprefix("deck_select:") or None
        engine.set_deck(user_id, deck_name)
        label = deck_name if deck_name else "All decks"
        await query.edit_message_text(f"✅ Deck set to: {label}\nUse /quiz to start reviewing.")

    async def send_due_notification(context: ContextTypes.DEFAULT_TYPE) -> None:
        count = engine.get_due_count_sync()
        if count > 0:
            await context.bot.send_message(
                chat_id=context.job.chat_id,
                text=f"📚 You have {count} card(s) due for review. Use /quiz to start!",
            )

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error("Unhandled exception", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ 系統發生錯誤，請稍後再試。"
            )

    return {
        "start": start_command,
        "quiz": quiz_command,
        "stop": stop_command,
        "status": status_command,
        "decks": decks_command,
        "deck_select": deck_select_callback,
        "handle_answer": handle_answer,
        "skip": skip_callback,
        "hint": hint_callback,
        "next": next_callback,
        "end": end_callback,
        "new_session": new_session_callback,
        "sync": sync_callback,
        "send_due_notification": send_due_notification,
        "error": error_handler,
    }


def _format_question(question) -> str:
    q_type = "Fill in the blank" if question.quiz_type.value == "fill_in_blank" else "Spell it out"
    return f"[{q_type}]\n\n{question.question_text}"


def _format_summary(summary: SessionSummary) -> str:
    avg_ease = sum(summary.ease_history) / len(summary.ease_history) if summary.ease_history else 0
    return (
        f"📊 Session complete!\n\n"
        f"Cards reviewed: {summary.cards_done}\n"
        f"Correct (Good+Easy): {summary.correct_count}\n"
        f"Average ease: {avg_ease:.1f}/4.0"
    )


async def _send_question(update: Update, question) -> None:
    text = _format_question(question)
    await update.message.reply_text(text, reply_markup=question_keyboard(bool(question.hint)))
