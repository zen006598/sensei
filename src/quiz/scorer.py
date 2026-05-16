from src.gemini.client import GeminiClient, GeminiError
from src.quiz.models import QuizQuestion


class Scorer:
    def __init__(self, gemini_client: GeminiClient):
        self._gemini = gemini_client

    async def score(self, question: QuizQuestion, user_answer: str) -> tuple[int, str]:
        try:
            return await self._gemini.score_answer(question, user_answer)
        except GeminiError:
            return 1, "⚠️ Scoring unavailable, please try again."
