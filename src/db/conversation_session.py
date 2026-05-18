from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class ConversationSession(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    card_id: int
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    outcome: str | None = None  # "perfect" | "skipped" | "stopped"
    summary: str | None = None  # Gemini NL summary
    messages: str | None = None  # JSON: last 3 user messages
    attempt_count: int = 0
