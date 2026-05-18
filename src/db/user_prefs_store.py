from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from src.db.user_prefs import UserPrefs


class UserPrefsStore:
    def __init__(self, db_path: str, engine: Engine | None = None):
        if engine is not None:
            self._engine = engine
        else:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._engine = create_engine(f"sqlite:///{db_path}")
            SQLModel.metadata.create_all(self._engine)

    def _get_or_create(self, session: Session, user_id: int) -> UserPrefs:
        prefs = session.get(UserPrefs, user_id)
        if prefs is None:
            prefs = UserPrefs(user_id=user_id)
            session.add(prefs)
            session.flush()
        return prefs

    def get_deck(self, user_id: int) -> str | None:
        with Session(self._engine) as session:
            prefs = session.get(UserPrefs, user_id)
            return prefs.selected_deck if prefs else None

    def set_deck(self, user_id: int, deck_name: str | None) -> None:
        with Session(self._engine) as session:
            prefs = self._get_or_create(session, user_id)
            prefs.selected_deck = deck_name
            session.add(prefs)
            session.commit()

    def get_mode(self, user_id: int) -> str:
        with Session(self._engine) as session:
            prefs = session.get(UserPrefs, user_id)
            return prefs.quiz_mode if prefs else "default"

    def set_mode(self, user_id: int, mode: str) -> None:
        with Session(self._engine) as session:
            prefs = self._get_or_create(session, user_id)
            prefs.quiz_mode = mode
            session.add(prefs)
            session.commit()
