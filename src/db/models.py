from datetime import datetime

from sqlmodel import Field, SQLModel


class ConversationSession(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    outcome: str | None = None  # "perfect" | "skipped" | "stopped"
    summary: str | None = None  # Gemini NL summary
    messages: str | None = None  # JSON: last 3 user messages
    attempt_count: int = 0


class ErrorRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int  # soft FK → Anki card id
    error_type: str  # "grammar" | "vocabulary" | "spelling"
    user_answer: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
