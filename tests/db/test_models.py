import os
import tempfile

from sqlmodel import Session, SQLModel, create_engine, select

from src.db.conversation_session import ConversationSession
from src.db.error_record import ErrorRecord


def _engine():
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{tmp}")
    SQLModel.metadata.create_all(engine)
    return engine, tmp


def test_create_conversation_session():
    engine, tmp = _engine()
    with Session(engine) as s:
        cs = ConversationSession(card_id=123)
        s.add(cs)
        s.commit()
        s.refresh(cs)
    assert cs.id is not None
    assert cs.card_id == 123
    assert cs.outcome is None
    assert cs.attempt_count == 0
    os.unlink(tmp)


def test_update_conversation_session():
    engine, tmp = _engine()
    with Session(engine) as s:
        cs = ConversationSession(card_id=1)
        s.add(cs)
        s.commit()
        s.refresh(cs)
        cs_id = cs.id
    with Session(engine) as s:
        cs = s.get(ConversationSession, cs_id)
        cs.outcome = "perfect"
        cs.attempt_count = 3
        s.add(cs)
        s.commit()
    with Session(engine) as s:
        cs = s.get(ConversationSession, cs_id)
    assert cs.outcome == "perfect"
    assert cs.attempt_count == 3
    os.unlink(tmp)


def test_create_error_record():
    engine, tmp = _engine()
    with Session(engine) as s:
        er = ErrorRecord(card_id=456, error_type="grammar", user_answer="I goed")
        s.add(er)
        s.commit()
        s.refresh(er)
    assert er.id is not None
    assert er.error_type == "grammar"
    os.unlink(tmp)


def test_query_errors_by_card():
    engine, tmp = _engine()
    with Session(engine) as s:
        s.add(ErrorRecord(card_id=1, error_type="spelling", user_answer="wrng"))
        s.add(ErrorRecord(card_id=1, error_type="grammar", user_answer="bad grammar"))
        s.add(ErrorRecord(card_id=2, error_type="spelling", user_answer="other"))
        s.commit()
    with Session(engine) as s:
        errors = s.exec(select(ErrorRecord).where(ErrorRecord.card_id == 1)).all()
    assert len(errors) == 2
    os.unlink(tmp)
