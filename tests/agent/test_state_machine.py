import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from src.agent.gemini_agent import GeminiAgent
from src.agent.state_machine import QuizStateMachine
from src.agent.tools import JudgeResult, QuizResult
from src.db.models import ErrorRecord
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
    anki.get_due_cards = MagicMock(
        return_value=[
            CardData(card_id=1, front="run", back="走る", tags=[], deck_name="EN")
        ]
    )
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
    agent.generate_question = AsyncMock(
        return_value=QuizResult(
            question_type="spelling", question_text="Spell: run", correct_answer="run"
        )
    )

    question = await sm.start(user_id=1)

    assert question is not None
    assert question.question_type == "spelling"
    assert sm.has_active_session()
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)


@pytest.mark.asyncio
async def test_common_card_spelling_correct_continues_to_sentence():
    """For common cards, spelling correct alone does not end the session — sentence follows."""
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    sentence_q = QuizResult(
        question_type="sentence",
        question_text="Use 'run' in a sentence.",
        correct_answer="I run every day.",
    )
    agent.generate_question = AsyncMock(
        side_effect=[
            QuizResult(
                question_type="spelling",
                question_text="Spell: run",
                correct_answer="run",
            ),
            sentence_q,
        ]
    )
    agent.evaluate_answer = AsyncMock(
        return_value=JudgeResult(
            outcome="correct", error_type=None, suggestion="Perfect!"
        )
    )
    agent.generate_session_summary = AsyncMock(return_value="Good session.")

    await sm.start(user_id=1)
    result = await sm.submit_answer("run")  # exact-match spelling

    assert result.outcome == "correct"
    assert result.session_ended is False  # session still alive
    assert result.new_question.question_type == "sentence"
    anki.answer_card.assert_not_called()  # ease 4 NOT yet
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)


@pytest.mark.asyncio
async def test_common_card_full_mastery_ends_session():
    """Spelling correct + sentence correct → ease 4, session ends."""
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    agent.generate_question = AsyncMock(
        side_effect=[
            QuizResult(
                question_type="spelling",
                question_text="Spell: run",
                correct_answer="run",
            ),
            QuizResult(
                question_type="sentence",
                question_text="Use 'run'.",
                correct_answer="I run.",
            ),
            QuizResult(
                question_type="spelling",
                question_text="Spell: eat",
                correct_answer="eat",
            ),  # next card
        ]
    )
    agent.evaluate_answer = AsyncMock(
        return_value=JudgeResult(
            outcome="correct", error_type=None, suggestion="Great!"
        )
    )
    agent.generate_session_summary = AsyncMock(return_value="Mastered.")

    await sm.start(user_id=1)
    result1 = await sm.submit_answer("run")  # spelling ✓ → sentence question
    assert result1.session_ended is False
    anki.answer_card.assert_not_called()

    result2 = await sm.submit_answer(
        "I run."
    )  # sentence ✓ → mastered, attempt_count=2 → ease 3
    assert result2.outcome == "correct"
    assert result2.session_ended is True
    anki.answer_card.assert_called_once_with(1, 3)
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)


@pytest.mark.asyncio
async def test_rare_card_ends_after_one_correct():
    """Rare/obsolete cards only need one correct fill_in_blank → ease 4 immediately."""
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="rare")
    agent.generate_question = AsyncMock(
        side_effect=[
            QuizResult(
                question_type="fill_in_blank",
                question_text="___ means hate.",
                correct_answer="abhor",
            ),
            QuizResult(
                question_type="fill_in_blank",
                question_text="next card",
                correct_answer="x",
            ),
        ]
    )
    agent.evaluate_answer = AsyncMock(
        return_value=JudgeResult(
            outcome="correct", error_type=None, suggestion="Correct!"
        )
    )
    agent.generate_session_summary = AsyncMock(return_value="Done.")

    await sm.start(user_id=1)
    result = await sm.submit_answer("abhor")

    assert result.outcome == "correct"
    assert result.session_ended is True
    anki.answer_card.assert_called_once_with(1, 4)
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)


@pytest.mark.asyncio
async def test_wrong_answer_session_continues():
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    agent.generate_question = AsyncMock(
        return_value=QuizResult(
            question_type="spelling", question_text="Spell: run", correct_answer="run"
        )
    )
    agent.evaluate_answer = AsyncMock(
        return_value=JudgeResult(
            outcome="wrong",
            error_type="spelling",
            suggestion="The correct spelling is 'run'.",
        )
    )
    agent.generate_session_summary = AsyncMock(return_value="Struggled.")

    await sm.start(user_id=1)
    result = await sm.submit_answer("ran")

    assert result.outcome == "wrong"
    assert result.session_ended is False
    assert sm.has_active_session()
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)


@pytest.mark.asyncio
async def test_wrong_on_sentence_stays_on_sentence():
    """Spelling mistakes inside a sentence answer judge as 'wrong'; retry must stay sentence."""
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    sentence_q = QuizResult(
        question_type="sentence",
        question_text="Use 'busy' in a sentence.",
        correct_answer="Times Square is the busiest intersection in the world.",
    )
    next_sentence_q = QuizResult(
        question_type="sentence",
        question_text="Try again — use 'busy' in a sentence.",
        correct_answer="Times Square is the busiest intersection in the world.",
    )
    agent.generate_question = AsyncMock(side_effect=[sentence_q, next_sentence_q])
    agent.evaluate_answer = AsyncMock(
        return_value=JudgeResult(
            outcome="wrong",
            error_type="spelling",
            suggestion="Very close — Time → Times.",
        )
    )

    await sm.start(user_id=1)
    result = await sm.submit_answer(
        "Time Square is the most busy intersection in this world."
    )

    assert result.outcome == "wrong"
    assert result.session_ended is False
    assert result.new_question.question_type == "sentence"
    # generate_question called with forced_type='sentence' on retry
    _, kwargs = agent.generate_question.call_args
    assert kwargs.get("forced_type") == "sentence"
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)


@pytest.mark.asyncio
async def test_grammar_error_records_and_continues():
    sm, agent, anki, engine, tmp_db, tmp_prefs = _setup()
    agent.classify_word_frequency = AsyncMock(return_value="common")
    agent.generate_question = AsyncMock(
        return_value=QuizResult(
            question_type="sentence",
            question_text="Use 'run' in a sentence.",
            correct_answer="I run every day.",
        )
    )
    agent.evaluate_answer = AsyncMock(
        return_value=JudgeResult(
            outcome="grammar_error",
            error_type="grammar",
            suggestion="Say 'I run' not 'I runned'.",
        )
    )
    agent.generate_session_summary = AsyncMock(return_value="Grammar issues.")

    await sm.start(user_id=1)
    result = await sm.submit_answer("I runned every day.")

    assert result.outcome == "grammar_error"
    assert result.session_ended is False
    with Session(engine) as s:
        errors = s.exec(select(ErrorRecord).where(ErrorRecord.card_id == 1)).all()
    assert len(errors) == 1
    assert errors[0].error_type == "grammar"
    os.unlink(tmp_db)
    os.unlink(tmp_prefs)
