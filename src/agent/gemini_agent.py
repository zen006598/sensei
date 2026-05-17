from google import genai
from google.genai import types

from src.agent.tools import FREQ_TOOL, JUDGE_TOOL, QUIZ_TOOL, JudgeResult, QuizResult
from src.quiz.models import CardData

_ANY = types.ToolConfig(
    function_calling_config=types.FunctionCallingConfig(mode="ANY")
)


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

    async def generate_question(
        self,
        card: CardData,
        frequency: str,
        retry_count: int,
        recent_errors: list[str],
        conversation_summary: str | None,
        forced_type: str | None = None,
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
            "- correct: near-exact match\n"
            "- semantic_correct: different word but correct meaning (spelling questions only)\n"
            "- grammar_error: right vocabulary, wrong grammar (sentence questions)\n"
            "- vocab_error: wrong vocabulary (sentence questions)\n"
            "- wrong: clearly incorrect\n\n"
            "Also provide a concise suggestion:\n"
            "  correct → more natural/idiomatic phrasing\n"
            "  error → correction with brief explanation\n"
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
