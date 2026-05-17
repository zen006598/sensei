from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_keyboard() -> ReplyKeyboardMarkup:
    """Persistent bottom menu always visible in chat."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("/quiz"), KeyboardButton("/sync"), KeyboardButton("/status")],
            [KeyboardButton("/decks"), KeyboardButton("/help")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=True,
    )


def question_keyboard(has_hint: bool) -> InlineKeyboardMarkup:
    """Keyboard shown while a question is active."""
    row = []
    if has_hint:
        row.append(InlineKeyboardButton("Hint", callback_data="hint"))
    row.append(InlineKeyboardButton("Skip", callback_data="skip"))
    return InlineKeyboardMarkup([
        row,
        [InlineKeyboardButton("I don't know", callback_data="dont_know")],
    ])


def session_summary_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown at session summary."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("New Session", callback_data="new_session"),
                InlineKeyboardButton("Sync", callback_data="sync"),
            ]
        ]
    )


def deck_list_keyboard(deck_names: list[str]) -> InlineKeyboardMarkup:
    """One button per deck + 'All decks' at top. Capped at 8 decks."""
    rows = [[InlineKeyboardButton("All decks", callback_data="deck_select:")]]
    for name in deck_names[:8]:
        rows.append([InlineKeyboardButton(name, callback_data=f"deck_select:{name}")])
    return InlineKeyboardMarkup(rows)


def mode_keyboard(current_mode: str) -> InlineKeyboardMarkup:
    """Keyboard for selecting study mode."""

    def label(mode: str, text: str) -> str:
        return f"* {text}" if current_mode == mode else text

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label("default", "Due + New"), callback_data="mode_select:default")],
            [InlineKeyboardButton(label("due", "Due only"), callback_data="mode_select:due")],
            [InlineKeyboardButton(label("new", "New only"), callback_data="mode_select:new")],
        ]
    )
