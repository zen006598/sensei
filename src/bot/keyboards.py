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


def deck_list_keyboard(deck_names: list[str]) -> InlineKeyboardMarkup:
    """One button per deck + 'All decks' at top. Capped at 8 decks."""
    rows = [[InlineKeyboardButton("📚 All decks", callback_data="deck_select:")]]
    for name in deck_names[:8]:
        rows.append([InlineKeyboardButton(name, callback_data=f"deck_select:{name}")])
    return InlineKeyboardMarkup(rows)
