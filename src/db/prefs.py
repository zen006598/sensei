import sqlite3
from pathlib import Path


class UserPrefsStore:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS user_prefs "
                "(user_id INTEGER PRIMARY KEY, selected_deck TEXT)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def get_deck(self, user_id: int) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT selected_deck FROM user_prefs WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row[0] if row else None

    def set_deck(self, user_id: int, deck_name: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_prefs (user_id, selected_deck) VALUES (?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET selected_deck = excluded.selected_deck",
                (user_id, deck_name),
            )
