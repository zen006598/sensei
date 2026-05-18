from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from src.db.error_record import ErrorRecord


class ErrorRecordStore:
    def __init__(self, engine: Engine):
        self._engine = engine

    def record(self, card_id: int, error_type: str, user_answer: str) -> None:
        with Session(self._engine) as s:
            s.add(
                ErrorRecord(
                    card_id=card_id, error_type=error_type, user_answer=user_answer
                )
            )
            s.commit()

    def recent_for_card(self, card_id: int, limit: int = 5) -> list[str]:
        with Session(self._engine) as s:
            errors = s.exec(
                select(ErrorRecord)
                .where(ErrorRecord.card_id == card_id)
                .order_by(ErrorRecord.created_at.desc())
                .limit(limit)
            ).all()
        return [f"{e.error_type}: {e.user_answer}" for e in reversed(errors)]
