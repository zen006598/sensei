import json

from google import genai
from google.genai import types

from src.agent.schemas import (
    CLASSIFY_SCHEMA,
    JUDGE_SCHEMA,
    QUIZ_SCHEMA,
    JudgeResult,
    QuizResult,
    WordClassification,
)
from src.quiz.models import CardData


class GeminiAgent:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash-lite"):
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def _structured(self, prompt: str, schema: types.Schema) -> dict:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        return json.loads(response.text)

    async def classify_word(self, card: CardData) -> WordClassification:
        """Single Gemini call returning both frequency and register. Falls back to (common, neutral)."""
        prompt = (
            "Classify this vocabulary item for a language learner.\n"
            f"Word: {card.front}\n"
            "frequency: common = everyday usage, rare = infrequent/specialised, obsolete = archaic/no longer used.\n"
            "register: formal = academic/professional, informal = conversational, slang = very casual/street, "
            "literary = poetic/archaic, neutral = neither formal nor informal."
        )
        try:
            data = await self._structured(prompt, CLASSIFY_SCHEMA)
            return WordClassification(
                frequency=data["frequency"], register=data["register"]
            )
        except (json.JSONDecodeError, KeyError):
            return WordClassification(frequency="common", register="neutral")

    async def generate_question(
        self,
        card: CardData,
        frequency: str,
        retry_count: int,
        recent_errors: list[str],
        conversation_summary: str | None,
        forced_type: str | None = None,
        register: str = "neutral",
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

        prompt = (
            "You are a language learning quiz generator.\n"
            f"Card front: {card.front}\nCard back: {card.back}\nTags: {', '.join(card.tags)}\n"
            f"{context}\n"
            f"{type_instruction}\n"
            "Question type rules:\n"
            "- spelling: Give the word's meaning/definition as the question (e.g. 'What word means \"to move quickly on foot\"?'). "
            "Do NOT just say 'Spell: {word}'. The learner must recall and spell the word from its definition.\n"
            f"- fill_in_blank: question_text MUST be a complete sentence with the target word replaced by '___' (e.g. 'She made a solemn ___ to keep her word.'). "
            f"hint = '{register} | ' followed by a one-sentence explanation of the word's meaning, e.g. '{register} | a formal promise or guarantee'.\n"
            f"- sentence: Explicitly state the target word (from card front: '{card.front}') in the question, "
            "then provide at least 2 short scenario descriptions. "
            "Format the question_text like: 'Use the word \"<word>\" in a sentence for one of these situations:\n1. <scenario A>\n2. <scenario B>'"
        )
        data = await self._structured(prompt, QUIZ_SCHEMA)
        return QuizResult(
            question_type=data["question_type"],
            question_text=data["question_text"],
            correct_answer=data["correct_answer"],
            hint=data.get("hint", ""),
        )

    async def evaluate_answer(
        self,
        question_type: str,
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
            "Then optionally a brief hint about the part that's wrong (e.g. 'check the vowel in the middle').\n"
            "  wrong/grammar_error (sentence) → first write the corrected sentence, then list each correction as a bullet:\n"
            "    • 'original' → 'corrected': reason\n"
            "    Example: • 'negotiation' → 'negotiate': should be a verb after 'help someone'"
        )
        data = await self._structured(prompt, JUDGE_SCHEMA)
        raw_error_type = data.get("error_type", "")
        return JudgeResult(
            outcome=data["outcome"],
            error_type=raw_error_type if raw_error_type else None,
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
