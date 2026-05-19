import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.gemini_agent import GeminiAgent
from src.agent.schemas import JudgeResult, QuizResult
from src.anki.card_data import CardData


def _card():
    return CardData(card_id=1, front="run", back="走る", tags=[], deck_name="EN")


def _mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.text = json.dumps(payload)
    return resp


@pytest.mark.asyncio
async def test_classify_register():
    agent = GeminiAgent(api_key="test")
    response = _mock_response({"register": "formal"})
    with patch.object(
        agent._client.aio.models,
        "generate_content",
        new=AsyncMock(return_value=response),
    ):
        result = await agent.classify_register(_card())
    assert result == "formal"


@pytest.mark.asyncio
async def test_classify_register_returns_none_on_unexpected_value():
    """The schema's enum constrains the LLM, but unexpected values can still slip
    through on API errors / parse drift. Such values must not propagate."""
    agent = GeminiAgent(api_key="test")
    response = _mock_response({"register": "bogus"})
    with patch.object(
        agent._client.aio.models,
        "generate_content",
        new=AsyncMock(return_value=response),
    ):
        result = await agent.classify_register(_card())
    assert result is None


@pytest.mark.asyncio
async def test_generate_question_returns_quiz_result():
    agent = GeminiAgent(api_key="test")
    response = _mock_response(
        {
            "question_type": "spelling",
            "question_text": "How do you spell 'run'?",
            "correct_answer": "run",
            "hint": "",
        }
    )
    with patch.object(
        agent._client.aio.models,
        "generate_content",
        new=AsyncMock(return_value=response),
    ):
        result = await agent.generate_question(
            _card(),
            "common",
            retry_count=0,
            recent_errors=[],
            conversation_summary=None,
        )
    assert isinstance(result, QuizResult)
    assert result.question_type == "spelling"
    assert result.correct_answer == "run"


@pytest.mark.asyncio
async def test_evaluate_answer_returns_judge_result():
    agent = GeminiAgent(api_key="test")
    response = _mock_response(
        {
            "outcome": "grammar_error",
            "error_type": "grammar",
            "suggestion": "Use past tense: ran.",
        }
    )
    with patch.object(
        agent._client.aio.models,
        "generate_content",
        new=AsyncMock(return_value=response),
    ):
        result = await agent.evaluate_answer(
            "sentence", "Use 'run' in a sentence.", "run", "I runned fast."
        )
    assert isinstance(result, JudgeResult)
    assert result.outcome == "grammar_error"
    assert result.error_type == "grammar"
