from google import genai
from google.genai import types

from src.agent.tools import (
    FREQ_TOOL,
    JUDGE_TOOL,
    QUIZ_TOOL,
    REGISTER_TOOL,
    JudgeResult,
    QuizResult,
)
from src.quiz.models import CardData

_ANY = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY"))


class GeminiAgent:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash-lite"):
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def classify_word_frequency(self, card: CardData) -> str:
        """Returns 'common', 'rare', or 'obsolete'. Falls back to 'common' on failure."""
        prompt = (
            f"Classify the usage frequency of this vocabulary item for a language learner:\n"
            f"Front: {card.front}\nBack: {card.back}\n"
            "common = everyday usage, rare = infrequent/specialised, obsolete = archaic/no longer used.\n"
            "Use the classify_frequency tool."
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[FREQ_TOOL], tool_config=_ANY),
        )
        for part in response.candidates[0].content.parts:
            if part.function_call and part.function_call.name == "classify_frequency":
                return part.function_call.args["frequency"]
        return "common"

    async def classify_word_register(self, card: CardData) -> str:
        """Returns 'formal', 'informal', 'slang', 'literary', or 'neutral'. Falls back to 'neutral' on failure."""
        prompt = (
            f"Classify the formality register of this vocabulary item for a language learner:\n"
            f"Front: {card.front}\nBack: {card.back}\n"
            "formal = academic/professional, informal = conversational, slang = very casual/street, "
            "literary = poetic/archaic, neutral = neither formal nor informal.\n"
            "Use the classify_register tool."
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[REGISTER_TOOL], tool_config=_ANY),
        )
        for part in response.candidates[0].content.parts:
            if part.function_call and part.function_call.name == "classify_register":
                return part.function_call.args["register"]
        return "neutral"

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
            "Format the question_text like: 'Use the word \"<word>\" in a sentence for one of these situations:\n1. <scenario A>\n2. <scenario B>'\n"
            "Use the quiz tool to output the question."
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[QUIZ_TOOL], tool_config=_ANY),
        )
        for part in response.candidates[0].content.parts:
            if part.function_call and part.function_call.name == "quiz":
                args = part.function_call.args
                return QuizResult(
                    question_type=args["question_type"],
                    question_text=args["question_text"],
                    correct_answer=args["correct_answer"],
                    hint=args.get("hint", ""),
                )
        raise RuntimeError("Agent did not call quiz tool")

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
            "- correct: the target word is used correctly with proper grammar and clear meaning\n"
            "- semantic_correct: different word but correct meaning (spelling questions only)\n"
            "- grammar_error: target word is correct but the sentence has a grammar error (sentence questions only)\n"
            "- vocab_error: wrong vocabulary choice — used the wrong word entirely (sentence questions only)\n"
            "- wrong: spelling mistakes in the sentence, or meaning is clearly incorrect\n\n"
            "IMPORTANT: For sentence questions, any scenario the learner chooses is acceptable — "
            "do NOT penalise them for not using the suggested scenarios. "
            "Only evaluate whether the target word is used correctly with proper grammar.\n"
            "IMPORTANT: spelling mistakes in the sentence → 'wrong', not 'grammar_error'.\n\n"
            "Suggestion format:\n"
            "  correct (sentence question) → give a native-speaker tip: a more natural/idiomatic rephrasing, "
            "a collocate or synonym that fits the same context, or how a native speaker would say it differently. "
            "Format: '💬 Native tip: <rephrased sentence or usage note>'\n"
            "  correct (other types) → leave empty\n"
            "  wrong/grammar_error → first write the corrected sentence, then list each correction as a bullet:\n"
            "    • 'original' → 'corrected': reason\n"
            "    Example: • 'negotiation' → 'negotiate': should be a verb after 'help someone'\n"
            "Use the judge_score tool."
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[JUDGE_TOOL], tool_config=_ANY),
        )
        for part in response.candidates[0].content.parts:
            if part.function_call and part.function_call.name == "judge_score":
                args = part.function_call.args
                raw_error_type = args.get("error_type", "")
                return JudgeResult(
                    outcome=args["outcome"],
                    error_type=raw_error_type if raw_error_type else None,
                    suggestion=args["suggestion"],
                )
        raise RuntimeError("Agent did not call judge_score tool")

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
