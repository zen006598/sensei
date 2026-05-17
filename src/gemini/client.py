import json

from google import genai
from google.genai import types

from src.quiz.models import CardData, QuizQuestion, QuizType


class GeminiError(Exception):
    pass


class GeminiClient:
    def __init__(
        self, api_key: str, model: str = "gemini-2.5-flash-lite", timeout: int = 30
    ):
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._config = types.GenerateContentConfig(
            response_mime_type="application/json",
        )

    async def call(self, prompt: str) -> dict:
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=self._config,
            )
        except Exception as e:
            raise GeminiError(str(e)) from e
        try:
            return json.loads(response.text)
        except json.JSONDecodeError as e:
            raise GeminiError(f"Invalid JSON from Gemini: {response.text[:200]}") from e

    async def generate_question(
        self, card: CardData, quiz_type: QuizType
    ) -> QuizQuestion:
        """Generate a quiz question from a card."""
        prompt = _build_question_prompt(card, quiz_type)
        data = await self.call(prompt)
        return QuizQuestion(
            card_data=card,
            quiz_type=quiz_type,
            question_text=data["question_text"],
            correct_answer=data["correct_answer"],
            acceptable_alternates=data.get("acceptable_alternates", []),
            hint=data.get("hint", ""),
        )

    async def score_answer(
        self, question: QuizQuestion, user_answer: str
    ) -> tuple[int, str]:
        """Score user answer. Returns (ease 1-4, feedback str)."""
        prompt = _build_scoring_prompt(question, user_answer)
        data = await self.call(prompt)
        ease = max(1, min(4, int(data["ease"])))
        return ease, data.get("feedback", "")


def _build_question_prompt(card: CardData, quiz_type: QuizType) -> str:
    if quiz_type == QuizType.FILL_IN_BLANK:
        return (
            "You are a language learning quiz generator.\n\n"
            f"Card front: {card.front}\n"
            f"Card back: {card.back}\n"
            f"Tags: {', '.join(card.tags)}\n\n"
            'Create a fill-in-the-blank sentence where the key answer replaces "___".\n'
            "Provide enough context in the sentence. Keep the question in the card's language.\n\n"
            "Respond with valid JSON only:\n"
            '{"question_text": "sentence with ___", "correct_answer": "word or phrase", "hint": "short hint or empty string"}'
        )
    return (
        "You are a language learning quiz generator.\n\n"
        f"Card front: {card.front}\n"
        f"Card back: {card.back}\n"
        f"Tags: {', '.join(card.tags)}\n\n"
        "Create a direct recall question asking the learner to type the answer from memory.\n"
        "Keep the question in the card's language.\n\n"
        "Respond with valid JSON only:\n"
        '{"question_text": "question", "correct_answer": "primary answer", "acceptable_alternates": ["alt1"], "hint": ""}'
    )


def _build_scoring_prompt(question: QuizQuestion, user_answer: str) -> str:
    return (
        "Grade the learner's answer (ease scale 1-4):\n"
        "1=Again (wrong), 2=Hard (partial), 3=Good (minor errors), 4=Easy (perfect)\n\n"
        "Rules: minor typos (1-2 chars) → ease 3; semantically equivalent → ease 3-4; ignore capitalisation.\n\n"
        f"Question: {question.question_text}\n"
        f"Correct answer: {question.correct_answer}\n"
        f"Learner's answer: {user_answer}\n\n"
        "Respond with valid JSON only:\n"
        '{"ease": 3, "feedback": "brief explanation"}'
    )
