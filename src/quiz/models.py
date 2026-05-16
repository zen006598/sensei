from dataclasses import dataclass, field
from enum import Enum


class QuizType(Enum):
    FILL_IN_BLANK = "fill_in_blank"
    SPELLING = "spelling"


@dataclass
class CardData:
    card_id: int
    front: str
    back: str
    tags: list[str]
    deck_name: str


@dataclass
class QuizQuestion:
    card_data: CardData
    quiz_type: QuizType
    question_text: str
    correct_answer: str
    acceptable_alternates: list[str] = field(default_factory=list)
    hint: str = ""


@dataclass
class QuizSession:
    user_id: int
    pending_cards: list[CardData]
    current_question: QuizQuestion | None = None
    cards_done: int = 0
    correct_count: int = 0
    is_active: bool = False
    ease_history: list[int] = field(default_factory=list)


@dataclass
class AnswerResult:
    ease: int
    feedback: str
    correct_answer: str


@dataclass
class SessionSummary:
    user_id: int
    cards_done: int
    correct_count: int
    ease_history: list[int]
