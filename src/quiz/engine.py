import asyncio
import random

from src.anki.client import AnkiClient
from src.anki.sync import AnkiSyncer
from src.gemini.client import GeminiClient
from src.quiz.models import (
    AnswerResult,
    QuizQuestion,
    QuizSession,
    QuizType,
    SessionSummary,
)
from src.quiz.scorer import Scorer


class QuizEngine:
    def __init__(
        self,
        anki_client: AnkiClient,
        anki_syncer: AnkiSyncer,
        gemini_client: GeminiClient,
        scorer: Scorer,
    ):
        self._anki = anki_client
        self._syncer = anki_syncer
        self._gemini = gemini_client
        self._scorer = scorer
        self._sessions: dict[int, QuizSession] = {}

    async def start_session(self, user_id: int, max_cards: int) -> QuizSession:
        """Sync from AnkiWeb, fetch due cards, init session."""
        await self._syncer.async_sync()
        cards = await self._run_anki(self._anki.get_due_cards, max_cards)
        session = QuizSession(
            user_id=user_id,
            pending_cards=cards,
            is_active=True,
        )
        self._sessions[user_id] = session
        return session

    async def next_question(self, user_id: int) -> QuizQuestion | None:
        """Pop next card, generate question. Returns None if no cards left."""
        session = self._sessions.get(user_id)
        if not session or not session.pending_cards:
            return None
        card = session.pending_cards.pop(0)
        quiz_type = random.choice(list(QuizType))
        question = await self._gemini.generate_question(card, quiz_type)
        session.current_question = question
        return question

    async def submit_answer(self, user_id: int, user_answer: str) -> AnswerResult:
        """Score answer, update Anki card, return result."""
        session = self._sessions[user_id]
        question = session.current_question
        ease, feedback = await self._scorer.score(question, user_answer)
        await self._run_anki(self._anki.answer_card, question.card_data.card_id, ease)
        session.cards_done += 1
        if ease >= 3:
            session.correct_count += 1
        session.ease_history.append(ease)
        session.current_question = None
        return AnswerResult(
            ease=ease,
            feedback=feedback,
            correct_answer=question.correct_answer,
        )

    async def end_session(self, user_id: int) -> SessionSummary:
        """End session, sync to AnkiWeb, clean up."""
        session = self._sessions.pop(user_id, None)
        await self._syncer.async_sync()
        if session is None:
            return SessionSummary(user_id=user_id, cards_done=0, correct_count=0, ease_history=[])
        return SessionSummary(
            user_id=user_id,
            cards_done=session.cards_done,
            correct_count=session.correct_count,
            ease_history=session.ease_history,
        )

    def get_current_question(self, user_id: int):
        session = self._sessions.get(user_id)
        if session is None:
            return None
        return session.current_question

    def has_active_session(self, user_id: int) -> bool:
        return user_id in self._sessions and self._sessions[user_id].is_active

    def get_due_count_sync(self) -> int:
        """For notification jobs — returns due count synchronously."""
        return self._anki.get_due_count()

    async def _run_anki(self, fn, *args):
        """Acquire collection lock, run synchronous anki fn in executor."""
        async with AnkiClient._collection_lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, fn, *args)
