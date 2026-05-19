import json
import logging
from typing import cast, get_args

from google import genai
from google.genai import types

from src.agent.schemas import (
    JUDGE_SCHEMA,
    QUIZ_SCHEMA,
    REGISTER_SCHEMA,
    ErrorType,
    Frequency,
    JudgeResult,
    Outcome,
    QuestionType,
    QuizResult,
    Register,
)
from src.anki.card_data import CardData

logger = logging.getLogger(__name__)

_REGISTER_VALUES: frozenset[str] = frozenset(get_args(Register))
_QUESTION_TYPE_VALUES: frozenset[str] = frozenset(get_args(QuestionType))
_OUTCOME_VALUES: frozenset[str] = frozenset(get_args(Outcome))
_ERROR_TYPE_VALUES: frozenset[str] = frozenset(get_args(ErrorType))


class GeminiAgent:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash-lite",
        classify_model: str = "gemini-3.1-flash-lite",
    ):
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._classify_model = classify_model

    async def _structured(
        self, prompt: str, schema: types.Schema, model: str | None = None
    ) -> dict:
        response = await self._client.aio.models.generate_content(
            model=model or self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        return json.loads(response.text)

    async def classify_register(self, card: CardData) -> Register | None:
        """Returns the formality register, or None if the call/parse fails or
        the model returned a value outside the schema's enum. Errors are
        logged, not raised — caller decides how to handle absence."""
        prompt = (
            "Classify the formality register of this vocabulary item for a language learner.\n"
            f"Word: {card.front}\n"
            "formal = academic/professional, informal = conversational, slang = very casual/street, "
            "literary = poetic/archaic, neutral = neither formal nor informal."
        )
        try:
            data = await self._structured(
                prompt, REGISTER_SCHEMA, model=self._classify_model
            )
        except Exception:
            logger.warning(
                "classify_register failed for card %s (front=%r); skipping",
                card.card_id,
                card.front,
                exc_info=True,
            )
            return None
        value = data["register"]
        if value not in _REGISTER_VALUES:
            logger.warning(
                "classify_register got unexpected value %r for card %s; ignoring",
                value,
                card.card_id,
            )
            return None
        return value

    async def generate_question(
        self,
        card: CardData,
        frequency: Frequency,
        retry_count: int,
        recent_errors: list[str],
        conversation_summary: str | None,
        forced_type: QuestionType | None = None,
        register: Register | None = None,
    ) -> QuizResult:
        if forced_type:
            type_instruction = f"You MUST generate a '{forced_type}' question. No other type is allowed."
        elif frequency == "common":
            type_instruction = (
                "Choose the most suitable type: fill_in_blank, spelling, or sentence."
            )
        else:
            type_instruction = (
                "You MUST generate a 'fill_in_blank' question (word is rare/obsolete)."
            )

        context_lines = []
        if conversation_summary:
            context_lines.append(f"Previous session summary: {conversation_summary}")
        if recent_errors:
            context_lines.append(
                f"Recent errors for this card: {'; '.join(recent_errors)}"
            )
        if retry_count > 0:
            context_lines.append(
                f"This is retry #{retry_count}. Consider a different angle or phrasing."
            )
        context = "\n".join(context_lines)

        register_bullet = (
            f"The FIRST bullet of the hint MUST be exactly: '- register: {register}'.\n"
            if register
            else ""
        )

        prompt = (
            "You are a language learning quiz generator.\n"
            f"Card front: {card.front}\nCard back: {card.back}\nTags: {', '.join(card.tags)}\n"
            f"{context}\n"
            f"{type_instruction}\n"
            "Vocabulary level (applies to ALL of question_text and hint):\n"
            "- Use vocabulary at CEFR C1 or below (roughly the top 10000 most frequent English words).\n"
            f"- The target word itself ('{card.front}') is exempt — it can be any level, since it's what's being tested.\n"
            "- Prefer plainer wording when in doubt. Avoid literary, archaic, or specialised technical vocabulary unless it's the target word.\n"
            "Question type rules:\n"
            "- spelling: Give the word's meaning/definition as the question (e.g. 'What word means \"to move quickly on foot\"?'). "
            "Do NOT just say 'Spell: {word}'. The learner must recall and spell the word from its definition.\n"
            "- fill_in_blank: question_text MUST be a complete sentence with the target word replaced by '___' (e.g. 'She made a solemn ___ to keep her word.').\n"
            f"- sentence: Explicitly state the target word (from card front: '{card.front}') in the question, "
            "then provide at least 2 short scenario descriptions. "
            "Format the question_text like: 'Use the word \"<word>\" in a sentence for one of these situations:\n1. <scenario A>\n2. <scenario B>'\n\n"
            "Hint format rules (the 'hint' field):\n"
            "Output as plain-text bullets — one bullet per line, each line starting with '- '. Keep bullets concise.\n"
            f"{register_bullet}"
            "Then, per question type, include these bullets in order:\n"
            "- spelling:\n"
            "    - syllable count (e.g. '2 syllables')\n"
            "    - first letter (e.g. \"starts with 'P'\")\n"
            "    - one rhyming or similar-sounding word\n"
            "- fill_in_blank:\n"
            "    - part of speech\n"
            "    - one common collocation or grammatical pattern\n"
            "    - one example sentence that paraphrases with a synonym (NOT the target word)\n"
            "    - one-sentence meaning explanation\n"
            "- sentence:\n"
            "    - part of speech\n"
            "    - 1–2 close synonyms\n"
            "    - one-sentence meaning explanation\n"
            f"CRITICAL: For fill_in_blank, the hint MUST NOT contain the target word '{card.front}' or any inflected/stemmed form of it "
            "(e.g. for 'promise', do not write 'promise', 'promised', 'promising', 'promises'). "
            "For sentence and spelling, the target word may appear in synonym lists but should not be the whole answer."
        )
        data = await self._structured(prompt, QUIZ_SCHEMA)
        question_type = data["question_type"]
        if question_type not in _QUESTION_TYPE_VALUES:
            logger.warning(
                "generate_question got unexpected question_type %r for card %s",
                question_type,
                card.card_id,
            )
        return QuizResult(
            question_type=cast("QuestionType", question_type),
            question_text=data["question_text"],
            correct_answer=data["correct_answer"],
            hint=data.get("hint", ""),
        )

    async def evaluate_answer(
        self,
        question_type: QuestionType,
        question_text: str,
        correct_answer: str,
        user_answer: str,
    ) -> JudgeResult:
        prompt = (
            f"Evaluate the learner's answer for a '{question_type}' question.\n"
            f"Question: {question_text}\n"
            f"Correct answer: {correct_answer}\n"
            f"Learner's answer: {user_answer}\n\n"
            "Scoring guide:\n"
            "- correct: the target word is used correctly and the sentence is grammatically acceptable. "
            "Be GENEROUS: accept all valid stylistic, tense, and aspect variants (e.g. present perfect vs present perfect continuous), "
            "uncountable/countable noun preferences that are still grammatical, and any natural phrasing a native speaker might use. "
            "If you'd merely 'prefer' a different wording, it's still correct — put the preference in the suggestion as a native tip.\n"
            "- semantic_correct: different word but correct meaning (spelling questions only)\n"
            "- grammar_error: ONLY use for clear, unambiguous violations such as subject-verb disagreement "
            "('she go' instead of 'she goes'), wrong tense that breaks meaning, missing required articles where omission is ungrammatical, "
            "wrong preposition that breaks meaning, or word form errors ('negotiation' as a verb). "
            "Do NOT flag stylistic preferences, alternative valid tenses, or 'experiences' vs 'experience' when both are grammatically acceptable. "
            "(sentence questions only)\n"
            "- vocab_error: wrong vocabulary choice — used the wrong word entirely (sentence questions only)\n"
            "- wrong: spelling mistakes in the sentence, or meaning is clearly incorrect\n\n"
            "Acceptable examples (→ correct, not grammar_error):\n"
            "  'I have been working on DevOps teams and having infrastructure maintenance experiences' — "
            "tense + 'experiences' are both grammatically valid, even if 'have worked' / 'experience' is more idiomatic.\n\n"
            "IMPORTANT: For sentence questions, any scenario the learner chooses is acceptable — "
            "do NOT penalise them for not using the suggested scenarios. "
            "Only evaluate whether the target word is used correctly with grammatically acceptable form.\n"
            "IMPORTANT: spelling mistakes in the sentence → 'wrong', not 'grammar_error'.\n\n"
            "Suggestion format:\n"
            "  correct (sentence question) → give a native-speaker tip: a more natural/idiomatic rephrasing, "
            "a collocate or synonym that fits the same context, or how a native speaker would say it differently. "
            "Format: '💬 Native tip: <rephrased sentence or usage note>'\n"
            "  correct (spelling/fill_in_blank) → leave empty\n"
            "  wrong (spelling/fill_in_blank) → start with a closeness indicator: "
            "'Very close — N letter(s) off' for typos ≤2 chars, or 'Different word' when the answer is unrelated. "
            "Then optionally ONE short directional nudge — only a thematic category or context "
            "(e.g. 'think about transport', 'related to weather', 'a feeling, not an object'). "
            "The suggestion MUST NOT:\n"
            f"    - contain the target word ('{correct_answer}') or any inflected/stemmed form of it,\n"
            "    - define, paraphrase, or explain what the target word means,\n"
            "    - reveal the target word's length, first/last letter, or any letter-by-letter structure,\n"
            "    - use a synonym so close it gives the answer away (e.g. 'box' for 'crate').\n"
            "  wrong/grammar_error (sentence) → first write the corrected sentence, then list each correction as a bullet:\n"
            "    • 'original' → 'corrected': reason\n"
            "    Example: • 'negotiation' → 'negotiate': should be a verb after 'help someone'"
        )
        data = await self._structured(prompt, JUDGE_SCHEMA)
        outcome = data["outcome"]
        if outcome not in _OUTCOME_VALUES:
            logger.warning("evaluate_answer got unexpected outcome %r", outcome)
        raw_error_type = data.get("error_type", "")
        if raw_error_type and raw_error_type not in _ERROR_TYPE_VALUES:
            logger.warning(
                "evaluate_answer got unexpected error_type %r", raw_error_type
            )
            raw_error_type = ""
        return JudgeResult(
            outcome=cast("Outcome", outcome),
            error_type=cast("ErrorType", raw_error_type) if raw_error_type else None,
            suggestion=data["suggestion"],
        )

    async def generate_session_summary(
        self,
        card_front: str,
        card_back: str,
        messages: list[str],
        recent_errors: list[str],
    ) -> str:
        msgs_text = "\n".join(f"- {m}" for m in messages) or "(none)"
        errs_text = "\n".join(f"- {e}" for e in recent_errors) or "(none)"
        prompt = (
            "Summarise this language learning session in 2-3 sentences for future reference.\n"
            f"Card: {card_front} → {card_back}\n"
            f"Learner's messages:\n{msgs_text}\n"
            f"Errors:\n{errs_text}\n"
            "Focus on what the learner struggled with and any patterns observed."
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
        )
        return response.text.strip()
