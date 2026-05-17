import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel, create_engine

from src.agent.gemini_agent import GeminiAgent
from src.agent.state_machine import QuizStateMachine
from src.agent.tools import JudgeResult, QuizResult
from src.db.models import ConversationSession, ErrorRecord
from src.db.prefs import UserPrefsStore
from src.quiz.models import CardData


def _setup():
    fd, tmp_db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    fd2, tmp_prefs = tempfile.mkstemp(suffix=".db")
    os.close(fd2)
    engine = create_engine(f"sqlite:///{tmp_db}")
    SQLModel.metadata.create_all(engine)
    prefs = UserPrefsStore(tmp_prefs)

    agent = MagicMock(spec=GeminiAgent)
    anki = MagicMock()
    anki.get_due_cards = MagicMock(return_value=[
        CardData(card_id=1, front="run", back="走る", tags=[], deck_name="EN")
    ])
    anki.answer_card = MagicMock()
    anki.update_card_tags = MagicMock()

    syncer = MagicMock()
    syncer.async_sync = AsyncMock()

    sm = QuizStateMachine(anki, syncer, agent, prefs, engine)
    return sm, agent, anki, engine, tmp_db, tmp_prefs


@pytest.mark.asyncio
async def test_start_returns_question():
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    agent.generate_question = AsyncMock(return_value=QuizResult(
        question_type="spelling", question_text="Spell: run", correct_answer="run"
    ))

    question = await sm.start(user_id=1)

    assert question is not None
    assert question.question_type == "spelling"
    assert sm.has_active_session()
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)


@pytest.mark.asyncio
async def test_exact_match_ends_session():
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    agent.generate_question = AsyncMock(side_effect=[
        QuizResult(question_type="spelling", question_text="Spell: run", correct_answer="run"),
        QuizResult(question_type="spelling", question_text="Spell: eat", correct_answer="eat"),
    ])
    agent.evaluate_answer = AsyncMock(return_value=JudgeResult(
        outcome="correct", error_type=None, suggestion="Perfect! Also: dash, sprint."
    ))
    agent.generate_session_summary = AsyncMock(return_value="Good session.")

    await sm.start(user_id=1)
    result = await sm.submit_answer("run")

    assert result.outcome == "correct"
    assert result.session_ended is True
    anki.answer_card.assert_called_once_with(1, 4)
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)


@pytest.mark.asyncio
async def test_wrong_answer_session_continues():
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    agent.generate_question = AsyncMock(return_value=QuizResult(
        question_type="spelling", question_text="Spell: run", correct_answer="run"
    ))
    agent.evaluate_answer = AsyncMock(return_value=JudgeResult(
        outcome="wrong", error_type="spelling", suggestion="The correct spelling is 'run'."
    ))
    agent.generate_session_summary = AsyncMock(return_value="Struggled.")

    await sm.start(user_id=1)
    result = await sm.submit_answer("ran")

    assert result.outcome == "wrong"
    assert result.session_ended is False
    assert sm.has_active_session()
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)


@pytest.mark.asyncio
async def test_grammar_error_records_and_continues():
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    agent.generate_question = AsyncMock(return_value=QuizResult(
        question_type="sentence", question_text="Use 'run' in a sentence.", correct_answer="I run every day."
    ))
    agent.evaluate_answer = AsyncMock(return_value=JudgeResult(
        outcome="grammar_error", error_type="grammar", suggestion="Say 'I run' not 'I runned'."
    ))
    agent.generate_session_summary = AsyncMock(return_value="Grammar issues.")

    await sm.start(user_id=1)
    result = await sm.submit_answer("I runned every day.")

    assert result.outcome == "grammar_error"
    assert result.session_ended is False
    from sqlmodel import Session, select
    with Session(engine) as s:
        errors = s.exec(select(ErrorRecord).where(ErrorRecord.card_id == 1)).all()
    assert len(errors) == 1
    assert errors[0].error_type == "grammar"
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)
