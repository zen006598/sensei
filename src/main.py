import asyncio
import logging
from datetime import datetime, time
from pathlib import Path

from sqlmodel import SQLModel, create_engine
from telegram.ext import ContextTypes

from src.agent.gemini_agent import GeminiAgent
from src.agent.state_machine import QuizStateMachine
from src.anki.client import AnkiClient
from src.anki.sync import AnkiSyncer
from src.bot.app import build_app
from src.bot.handlers import make_handlers
from src.config import load_settings
from src.db.conversation_session import ConversationSession  # noqa: F401 — ensure table registered
from src.db.conversation_session_store import ConversationSessionStore
from src.db.error_record import ErrorRecord  # noqa: F401 — ensure table registered
from src.db.error_record_store import ErrorRecordStore
from src.db.user_prefs_store import UserPrefsStore
from src.db.user_prefs import UserPrefs  # noqa: F401 — ensure table registered
from src.word_service.card_tagger import BatchAlreadyRunningError, CardTagger
from src.word_service.word_classifier import WordClassifier


def main() -> None:
    settings = load_settings()

    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("google_genai").setLevel(logging.WARNING)

    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    db_engine = create_engine(f"sqlite:///{settings.db_path}")
    SQLModel.metadata.create_all(db_engine)

    anki_client = AnkiClient(settings.anki_collection_path)
    anki_syncer = AnkiSyncer(
        settings.anki_collection_path,
        settings.ankiweb_email,
        settings.ankiweb_password,
    )
    prefs_store = UserPrefsStore(db_engine)
    errors_store = ErrorRecordStore(db_engine)
    sessions_store = ConversationSessionStore(db_engine)
    agent = GeminiAgent(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        classify_model=settings.gemini_classify_model,
    )
    classifier = WordClassifier(agent)
    tagger = CardTagger(anki_client, classifier)
    state_machine = QuizStateMachine(
        anki_client,
        anki_syncer,
        agent,
        tagger,
        prefs_store,
        errors_store,
        sessions_store,
    )

    handlers = make_handlers(
        state_machine, anki_syncer, anki_client, prefs_store, tagger
    )
    app = build_app(settings.telegram_token, settings.allowed_user_ids, handlers)

    async def _daily_retag(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            local = await tagger.classify_local_all()
            await anki_syncer.try_sync("after local pass")
            register = await tagger.classify_register_all()
            await anki_syncer.try_sync("after register pass")
            logging.info("daily retag done: local=%s register=%s", local, register)
        except BatchAlreadyRunningError:
            logging.warning("daily retag skipped: another batch already running")

    # JobQueue interprets time as UTC unless tzinfo is set; derive it from the
    # host's local zone (e.g. TZ=Asia/Taipei in docker-compose) so 03:00 means
    # 03:00 *there*, not 03:00 UTC.
    local_tz = datetime.now().astimezone().tzinfo
    app.job_queue.run_daily(
        _daily_retag,
        time=time(hour=settings.scheduler_daily_hour, tzinfo=local_tz),
        name="daily_retag",
    )

    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
