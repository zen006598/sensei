from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class ErrorRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int  # soft FK → Anki card id
    error_type: str  # "grammar" | "vocabulary" | "spelling"
    user_answer: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
