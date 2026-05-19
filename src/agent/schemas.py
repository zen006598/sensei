from dataclasses import dataclass
from typing import Literal, get_args

from google.genai import types


Register = Literal["formal", "informal", "slang", "literary", "neutral"]


@dataclass
class QuizResult:
    question_type: str  # "fill_in_blank" | "spelling" | "sentence"
    question_text: str
    correct_answer: str
    hint: str = ""


@dataclass
class JudgeResult:
    outcome: str  # "correct" | "semantic_correct" | "grammar_error" | "vocab_error" | "wrong"
    error_type: str | None  # "grammar" | "vocabulary" | "spelling" | None
    suggestion: str


QUIZ_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "question_type": types.Schema(
            type=types.Type.STRING,
            enum=["fill_in_blank", "spelling", "sentence"],
        ),
        "question_text": types.Schema(type=types.Type.STRING),
        "correct_answer": types.Schema(type=types.Type.STRING),
        "hint": types.Schema(type=types.Type.STRING),
    },
    required=["question_type", "question_text", "correct_answer", "hint"],
)

JUDGE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "outcome": types.Schema(
            type=types.Type.STRING,
            enum=[
                "correct",
                "semantic_correct",
                "grammar_error",
                "vocab_error",
                "wrong",
            ],
        ),
        "error_type": types.Schema(
            type=types.Type.STRING,
            enum=["grammar", "vocabulary", "spelling"],
        ),
        "suggestion": types.Schema(type=types.Type.STRING),
    },
    required=["outcome", "suggestion"],
)

REGISTER_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "register": types.Schema(
            type=types.Type.STRING,
            enum=list(get_args(Register)),
        ),
    },
    required=["register"],
)
