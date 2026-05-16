from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def question_keyboard(has_hint: bool) -> InlineKeyboardMarkup:
    """Keyboard shown while a question is active."""
    buttons = []
    if has_hint:
        buttons.append(InlineKeyboardButton("💡 Hint", callback_data="hint"))
    buttons.append(InlineKeyboardButton("⏭ Skip", callback_data="skip"))
    return InlineKeyboardMarkup([buttons])


def after_answer_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown after an answer is scored."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➡ Next Card", callback_data="next"),
            InlineKeyboardButton("🛑 End Session", callback_data="end"),
        ]
    ])


def session_summary_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown at session summary."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 New Session", callback_data="new_session"),
            InlineKeyboardButton("☁ Sync Now", callback_data="sync"),
        ]
    ])
