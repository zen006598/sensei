from datetime import UTC, datetime

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from src.db.conversation_session import ConversationSession


class ConversationSessionStore:
    def __init__(self, engine: Engine):
        self._engine = engine

    def create(self, card_id: int) -> int:
        with Session(self._engine) as s:
            cs = ConversationSession(card_id=card_id)
            s.add(cs)
            s.commit()
            s.refresh(cs)
            return cs.id

    def finalize(self, session_id: int, **fields) -> None:
        with Session(self._engine) as s:
            cs = s.get(ConversationSession, session_id)
            if cs is None:
                return
            for k, v in fields.items():
                setattr(cs, k, v)
            cs.ended_at = datetime.now(UTC)
            s.add(cs)
            s.commit()

    def set_summary(self, session_id: int, summary: str) -> None:
        with Session(self._engine) as s:
            cs = s.get(ConversationSession, session_id)
            if cs is None:
                return
            cs.summary = summary
            s.add(cs)
            s.commit()

    def update_attempt_count(self, session_id: int, count: int) -> None:
        with Session(self._engine) as s:
            cs = s.get(ConversationSession, session_id)
            if cs is None:
                return
            cs.attempt_count = count
            s.add(cs)
            s.commit()

    def last_summary_for_card(self, card_id: int) -> str | None:
        with Session(self._engine) as s:
            cs = s.exec(
                select(ConversationSession)
                .where(ConversationSession.card_id == card_id)
                .where(ConversationSession.summary.is_not(None))
                .order_by(ConversationSession.started_at.desc())
                .limit(1)
            ).first()
        return cs.summary if cs else None
