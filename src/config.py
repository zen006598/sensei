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
    tts_daily_hour: int
    piper_voice_path: str
    piper_voice: str
    anki_media_path: str
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

    scheduler_daily_hour = int(os.environ.get("SCHEDULER_DAILY_HOUR", "3"))
    if not 0 <= scheduler_daily_hour <= 23:
        raise ValueError(
            f"SCHEDULER_DAILY_HOUR must be 0–23; got {scheduler_daily_hour}"
        )

    tts_daily_hour = int(os.environ.get("TTS_DAILY_HOUR", "4"))
    if not 0 <= tts_daily_hour <= 23:
        raise ValueError(f"TTS_DAILY_HOUR must be 0–23; got {tts_daily_hour}")

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
            "GEMINI_CLASSIFY_MODEL", "gemini-3.1-flash-lite"
        ),
        gemini_timeout_seconds=int(os.environ.get("GEMINI_TIMEOUT", "30")),
        scheduler_daily_hour=scheduler_daily_hour,
        tts_daily_hour=tts_daily_hour,
        piper_voice_path=os.environ.get(
            "PIPER_VOICE_PATH", "/data/piper/en_US-libritts-high.onnx"
        ),
        piper_voice=os.environ.get("PIPER_VOICE", "en_US-libritts-high"),
        anki_media_path=os.environ.get(
            "ANKI_MEDIA_PATH", "/data/anki/collection.media"
        ),
        max_cards_per_session=int(os.environ.get("MAX_CARDS_PER_SESSION", "20")),
        allowed_user_ids={
            int(uid)
            for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
            if uid.strip()
        },
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
