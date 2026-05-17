from pathlib import Path

from sqlmodel import Field, Session, SQLModel, create_engine


class UserPrefs(SQLModel, table=True):
    user_id: int = Field(primary_key=True)
    selected_deck: str | None = None


class UserPrefsStore:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(f"sqlite:///{db_path}")
        SQLModel.metadata.create_all(self._engine)

    def get_deck(self, user_id: int) -> str | None:
        with Session(self._engine) as session:
            prefs = session.get(UserPrefs, user_id)
            return prefs.selected_deck if prefs else None

    def set_deck(self, user_id: int, deck_name: str | None) -> None:
        with Session(self._engine) as session:
            prefs = session.get(UserPrefs, user_id)
            if prefs is None:
                prefs = UserPrefs(user_id=user_id, selected_deck=deck_name)
            else:
                prefs.selected_deck = deck_name
            session.add(prefs)
            session.commit()
