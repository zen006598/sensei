from sqlmodel import Field, SQLModel


class UserPrefs(SQLModel, table=True):
    user_id: int = Field(primary_key=True)
    selected_deck: str | None = None
    quiz_mode: str = "default"  # "default" | "due" | "new"
