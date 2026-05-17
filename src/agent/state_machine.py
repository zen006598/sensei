import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlmodel import Session, select

from src.agent.gemini_agent import GeminiAgent
from src.agent.tools import JudgeResult, QuizResult
from src.anki.client import AnkiClient
from src.anki.sync import AnkiSyncer
from src.db.models import ConversationSession, ErrorRecord
from src.db.prefs import UserPrefsStore
from src.quiz.models import CardData

_SENSEI_FREQ_TAGS = {"sensei:common", "sensei:rare", "sensei:obsolete"}
_SENSEI_REGISTER_TAGS = {
    "sensei:formal",
    "sensei:informal",
    "sensei:slang",
    "sensei:literary",
    "sensei:neutral",
}


@dataclass
class _ActiveSession:
    card: CardData
    frequency: str
    db_session_id: int
    current_question: QuizResult
    attempt_count: int = 0
    messages: list[str] = field(default_factory=list)
    correct_types: set = field(default_factory=set)  # question types answered correctly


@dataclass
class SubmitResult:
    outcome: str  # "correct" | "wrong" | "semantic_correct" | "grammar_error" | "vocab_error"
    suggestion: str
    correct_answer: str
    question_type: str
    hint: str
    session_ended: bool
    new_session_started: bool
    new_question: QuizResult | None
    remaining_due: int | None = None


class QuizStateMachine:
    def __init__(
        self,
        anki_client: AnkiClient,
        anki_syncer: AnkiSyncer,
        agent: GeminiAgent,
        prefs_store: UserPrefsStore,
        db_engine,
    ):
        self._anki = anki_client
        self._syncer = anki_syncer
        self._agent = agent
        self._prefs = prefs_store
        self._engine = db_engine
        self._active: _ActiveSession | None = None
        self._user_id: int = 0

    def has_active_session(self) -> bool:
        return self._active is not None

    def get_current_question(self) -> QuizResult | None:
        return self._active.current_question if self._active else None

    def get_due_count_sync(self) -> int:
        return self._anki.get_due_count()

    def set_deck(self, user_id: int, deck_name: str | None) -> None:
        self._prefs.set_deck(user_id, deck_name)

    def get_deck(self, user_id: int) -> str | None:
        return self._prefs.get_deck(user_id)

    def set_mode(self, user_id: int, mode: str) -> None:
        self._prefs.set_mode(user_id, mode)

    def get_mode(self, user_id: int) -> str:
        return self._prefs.get_mode(user_id)

    async def get_deck_names(self) -> list[str]:
        return await self._run_anki(self._anki.get_deck_names)

    async def start(self, user_id: int) -> QuizResult | None:
        self._user_id = user_id
        await self._syncer.async_sync()
        return await self._auto_start()

    async def skip(self) -> QuizResult | None:
        await self._end_session("skipped")
        return await self._auto_start()

    async def dont_know(self) -> QuizResult | None:
        """Fast skip: no summary generation, sync in background after lock is free."""
        if not self._active:
            return None
        session = self._active
        self._active = None
        await self._run_anki(self._anki.answer_card, session.card.card_id, 1)
        self._update_db_session(
            session.db_session_id,
            outcome="skipped",
            messages=json.dumps(session.messages),
            attempt_count=session.attempt_count,
        )
        asyncio.create_task(self._locked_sync())
        return await self._auto_start()

    async def _locked_sync(self) -> None:
        async with AnkiClient._collection_lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._syncer.sync)

    async def stop(self) -> int:
        return await self._end_session("stopped")

    async def submit_answer(self, user_answer: str) -> SubmitResult:
        assert self._active is not None
        session = self._active
        question = session.current_question

        session.messages.append(user_answer)
        if len(session.messages) > 3:
            session.messages = session.messages[-3:]
        session.attempt_count += 1
        self._update_attempt_count(session.db_session_id, session.attempt_count)

        # Spelling exact match shortcut
        if question.question_type == "spelling":
            if user_answer.strip().lower() == question.correct_answer.strip().lower():
                judge = await self._agent.evaluate_answer(
                    question.question_type,
                    question.question_text,
                    question.correct_answer,
                    user_answer,
                )
                session.correct_types.add("spelling")
                if self._is_mastered(session):
                    remaining = await self._end_session("perfect")
                    new_q = await self._auto_start()
                    return SubmitResult(
                        outcome="correct",
                        suggestion=judge.suggestion,
                        correct_answer=question.correct_answer,
                        question_type=question.question_type,
                        hint=question.hint,
                        session_ended=True,
                        new_session_started=new_q is not None,
                        new_question=new_q,
                        remaining_due=remaining,
                    )
                # common card: spelling done, now needs sentence
                new_q = await self._next_question(session, forced_type="sentence")
                return SubmitResult(
                    outcome="correct",
                    suggestion=judge.suggestion,
                    correct_answer=question.correct_answer,
                    question_type=question.question_type,
                    hint=question.hint,
                    session_ended=False,
                    new_session_started=False,
                    new_question=new_q,
                )

        judge = await self._agent.evaluate_answer(
            question.question_type,
            question.question_text,
            question.correct_answer,
            user_answer,
        )
        return await self._handle_judgment(judge, question, session)

    async def _handle_judgment(
        self, judge: JudgeResult, question: QuizResult, session: _ActiveSession
    ) -> SubmitResult:
        card_id = session.card.card_id

        if judge.outcome == "correct":
            session.correct_types.add(question.question_type)
            if self._is_mastered(session):
                remaining = await self._end_session("perfect")
                new_q = await self._auto_start()
                return SubmitResult(
                    outcome="correct",
                    suggestion=judge.suggestion,
                    correct_answer=question.correct_answer,
                    question_type=question.question_type,
                    hint=question.hint,
                    session_ended=True,
                    new_session_started=new_q is not None,
                    new_question=new_q,
                    remaining_due=remaining,
                )
            # common card: one type done, need the other
            has_fill_or_spelling = bool(
                {"fill_in_blank", "spelling"} & session.correct_types
            )
            next_type = "sentence" if has_fill_or_spelling else "fill_in_blank"
            new_q = await self._next_question(session, forced_type=next_type)
            return SubmitResult(
                outcome="correct",
                suggestion=judge.suggestion,
                correct_answer=question.correct_answer,
                question_type=question.question_type,
                hint=question.hint,
                session_ended=False,
                new_session_started=False,
                new_question=new_q,
            )

        if judge.outcome == "semantic_correct":
            new_q = await self._next_question(session, forced_type="sentence")
            return SubmitResult(
                outcome="semantic_correct",
                suggestion=judge.suggestion,
                correct_answer=question.correct_answer,
                question_type=question.question_type,
                hint=question.hint,
                session_ended=False,
                new_session_started=False,
                new_question=new_q,
            )

        if judge.outcome == "grammar_error":
            self._record_error(card_id, "grammar", session.messages[-1])
            new_q = await self._next_question(session, forced_type="sentence")
            return SubmitResult(
                outcome="grammar_error",
                suggestion=judge.suggestion,
                correct_answer=question.correct_answer,
                question_type=question.question_type,
                hint=question.hint,
                session_ended=False,
                new_session_started=False,
                new_question=new_q,
            )

        if judge.outcome == "vocab_error":
            self._record_error(card_id, "vocabulary", session.messages[-1])
            new_q = await self._next_question(session, forced_type="spelling")
            return SubmitResult(
                outcome="vocab_error",
                suggestion=judge.suggestion,
                correct_answer=question.correct_answer,
                question_type=question.question_type,
                hint=question.hint,
                session_ended=False,
                new_session_started=False,
                new_question=new_q,
            )

        # "wrong"
        self._record_error(card_id, "spelling", session.messages[-1])
        new_q = await self._next_question(session, forced_type=question.question_type)
        return SubmitResult(
            outcome="wrong",
            suggestion=judge.suggestion,
            correct_answer=question.correct_answer,
            question_type=question.question_type,
            hint=question.hint,
            session_ended=False,
            new_session_started=False,
            new_question=new_q,
        )

    def _is_mastered(self, session: _ActiveSession) -> bool:
        if session.frequency != "common":
            return True
        has_fill_or_spelling = bool(
            {"fill_in_blank", "spelling"} & session.correct_types
        )
        return has_fill_or_spelling and "sentence" in session.correct_types

    async def _auto_start(self) -> QuizResult | None:
        deck = self._prefs.get_deck(self._user_id)
        mode = self._prefs.get_mode(self._user_id)
        cards = await self._run_anki(self._anki.get_due_cards, 1, deck, mode)
        if not cards:
            return None
        return await self._begin_card(cards[0])

    async def _begin_card(self, card: CardData) -> QuizResult:
        frequency = await self._get_or_classify_frequency(card)
        register = await self._get_or_classify_register(card)
        recent_errors = self._get_recent_errors(card.card_id)
        last_summary = self._get_last_summary(card.card_id)
        question = await self._agent.generate_question(
            card,
            frequency,
            retry_count=0,
            recent_errors=recent_errors,
            conversation_summary=last_summary,
            register=register,
        )
        db_id = self._create_db_session(card.card_id)
        self._active = _ActiveSession(
            card=card,
            frequency=frequency,
            db_session_id=db_id,
            current_question=question,
        )
        return question

    async def _next_question(
        self, session: _ActiveSession, forced_type: str | None = None
    ) -> QuizResult:
        frequency = await self._get_or_classify_frequency(session.card)
        register = await self._get_or_classify_register(session.card)
        recent_errors = self._get_recent_errors(session.card.card_id)
        last_summary = self._get_last_summary(session.card.card_id)
        question = await self._agent.generate_question(
            session.card,
            frequency,
            retry_count=session.attempt_count,
            recent_errors=recent_errors,
            conversation_summary=last_summary,
            forced_type=forced_type,
            register=register,
        )
        session.current_question = question
        return question

    async def _end_session(self, outcome: str) -> int:
        if not self._active:
            return await self._run_anki(self._anki.get_due_count)
        session = self._active
        self._active = None
        if outcome == "perfect":
            ease = 4 if session.attempt_count == 1 else 3
        else:
            ease = 1
        await self._run_anki(self._anki.answer_card, session.card.card_id, ease)
        recent_errors = self._get_recent_errors(session.card.card_id)
        summary = await self._agent.generate_session_summary(
            session.card.front,
            session.card.back,
            session.messages,
            recent_errors,
        )
        self._update_db_session(
            session.db_session_id,
            outcome=outcome,
            summary=summary,
            messages=json.dumps(session.messages),
            attempt_count=session.attempt_count,
        )
        await self._syncer.async_sync()
        return await self._run_anki(self._anki.get_due_count)

    async def _get_or_classify_frequency(self, card: CardData) -> str:
        existing = {t for t in card.tags if t in _SENSEI_FREQ_TAGS}
        if existing:
            return existing.pop().removeprefix("sensei:")
        frequency = await self._agent.classify_word_frequency(card)
        tag = f"sensei:{frequency}"
        await self._run_anki(self._anki.update_card_tags, card.card_id, [tag])
        card.tags.append(tag)
        return frequency

    async def _get_or_classify_register(self, card: CardData) -> str:
        existing = {t for t in card.tags if t in _SENSEI_REGISTER_TAGS}
        if existing:
            return existing.pop().removeprefix("sensei:")
        register = await self._agent.classify_word_register(card)
        tag = f"sensei:{register}"
        await self._run_anki(self._anki.update_card_tags, card.card_id, [tag])
        card.tags.append(tag)
        return register

    def _create_db_session(self, card_id: int) -> int:
        with Session(self._engine) as s:
            cs = ConversationSession(card_id=card_id)
            s.add(cs)
            s.commit()
            s.refresh(cs)
            return cs.id

    def _update_db_session(self, session_id: int, **kwargs) -> None:
        with Session(self._engine) as s:
            cs = s.get(ConversationSession, session_id)
            if cs:
                for k, v in kwargs.items():
                    setattr(cs, k, v)
                cs.ended_at = datetime.now(UTC)
                s.add(cs)
                s.commit()

    def _update_attempt_count(self, session_id: int, count: int) -> None:
        with Session(self._engine) as s:
            cs = s.get(ConversationSession, session_id)
            if cs:
                cs.attempt_count = count
                s.add(cs)
                s.commit()

    def _record_error(self, card_id: int, error_type: str, user_answer: str) -> None:
        with Session(self._engine) as s:
            s.add(
                ErrorRecord(
                    card_id=card_id, error_type=error_type, user_answer=user_answer
                )
            )
            s.commit()

    def _get_recent_errors(self, card_id: int, limit: int = 5) -> list[str]:
        with Session(self._engine) as s:
            errors = s.exec(
                select(ErrorRecord)
                .where(ErrorRecord.card_id == card_id)
                .order_by(ErrorRecord.created_at.desc())
                .limit(limit)
            ).all()
        return [f"{e.error_type}: {e.user_answer}" for e in reversed(errors)]

    def _get_last_summary(self, card_id: int) -> str | None:
        with Session(self._engine) as s:
            cs = s.exec(
                select(ConversationSession)
                .where(ConversationSession.card_id == card_id)
                .where(ConversationSession.summary.is_not(None))
                .order_by(ConversationSession.started_at.desc())
                .limit(1)
            ).first()
        return cs.summary if cs else None

    async def _run_anki(self, fn, *args):
        async with AnkiClient._collection_lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, fn, *args)
