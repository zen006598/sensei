from dataclasses import dataclass
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    telegram_token: str
    ankiweb_email: str
    ankiweb_password: str
    anki_collection_path: str
    db_path: str
    gemini_api_key: str
    gemini_model: str
    gemini_classify_model: str
    gemini_timeout_seconds: int
    scheduler_daily_hour: int
    max_cards_per_session: int
    allowed_user_ids: set[int]
    log_level: str


def load_settings() -> Settings:
    required_fields = {
        "TELEGRAM_TOKEN": "telegram_token",
        "ANKIWEB_EMAIL": "ankiweb_email",
        "ANKIWEB_PASSWORD": "ankiweb_password",
        "GEMINI_API_KEY": "gemini_api_key",
    }

    missing = [key for key in required_fields if not os.environ.get(key)]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return Settings(
        telegram_token=os.environ["TELEGRAM_TOKEN"],
        ankiweb_email=os.environ["ANKIWEB_EMAIL"],
        ankiweb_password=os.environ["ANKIWEB_PASSWORD"],
        anki_collection_path=os.environ.get(
            "ANKI_COLLECTION_PATH", "./data/anki/collection.anki2"
        ),
        db_path=os.environ.get("DB_PATH", "./data/sensei.db"),
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite"),
        gemini_classify_model=os.environ.get(
            "GEMINI_CLASSIFY_MODEL", "gemini-3-flash-lite"
        ),
        gemini_timeout_seconds=int(os.environ.get("GEMINI_TIMEOUT", "30")),
        scheduler_daily_hour=int(os.environ.get("SCHEDULER_DAILY_HOUR", "3")),
        max_cards_per_session=int(os.environ.get("MAX_CARDS_PER_SESSION", "20")),
        allowed_user_ids={
            int(uid)
            for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
            if uid.strip()
        },
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
