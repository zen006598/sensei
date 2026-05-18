from dataclasses import dataclass

from google.genai import types


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


@dataclass
class WordClassification:
    frequency: str  # "common" | "rare" | "obsolete"
    register: str  # "formal" | "informal" | "slang" | "literary" | "neutral"


QUIZ_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="quiz",
            description="Output a quiz question for the learner",
            parameters=types.Schema(
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
            ),
        )
    ]
)

JUDGE_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="judge_score",
            description="Evaluate the learner's answer and provide a suggestion",
            parameters=types.Schema(
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
            ),
        )
    ]
)

CLASSIFY_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="classify",
            description="Classify the word's usage frequency and formality register",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "frequency": types.Schema(
                        type=types.Type.STRING,
                        enum=["common", "rare", "obsolete"],
                    ),
                    "register": types.Schema(
                        type=types.Type.STRING,
                        enum=["formal", "informal", "slang", "literary", "neutral"],
                    ),
                },
                required=["frequency", "register"],
            ),
        )
    ]
)
