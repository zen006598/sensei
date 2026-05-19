import asyncio
import logging
from dataclasses import dataclass

from src.anki.card_data import CardData
from src.anki.client import AnkiClient
from src.word_service.word_classifier import WordClassifier

logger = logging.getLogger(__name__)

TAG_PREFIX = "sensei:"
FREQUENCIES = frozenset({"common", "rare", "obsolete"})
REGISTERS = frozenset({"formal", "informal", "slang", "literary", "neutral"})
ACADEMICS = frozenset({"academic"})  # only the positive case is tagged


class BatchAlreadyRunningError(RuntimeError):
    """Raised when classify_local_all / classify_register_all is invoked while
    another batch is already in flight."""


@dataclass
class LocalDelta:
    frequency_added: bool
    academic_added: bool


@dataclass
class RegisterDelta:
    register_added: bool
    failed: bool  # LLM call failed; tag not written


@dataclass
class LocalBatchStats:
    cards_scanned: int = 0
    frequency_added: int = 0
    academic_added: int = 0
    write_failures: int = 0


@dataclass
class RegisterBatchStats:
    cards_scanned: int = 0
    register_added: int = 0
    register_failures: int = 0
    write_failures: int = 0


class CardTagger:
    """Owns all reads and writes of `sensei:*` tags on Anki cards.
    Quiz flow calls `classify(card)`; daily-job / /retag call
    `classify_local_all` and `classify_register_all`."""

    def __init__(self, anki: AnkiClient, classifier: WordClassifier):
        self._anki = anki
        self._classifier = classifier
        self._batch_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._batch_lock.locked()

    @staticmethod
    def _cached(card: CardData, values: frozenset[str]) -> str | None:
        """Return the `sensei:<value>` tag on `card.tags` whose value is in `values`, or None."""
        for tag in card.tags:
            if tag.startswith(TAG_PREFIX):
                value = tag.removeprefix(TAG_PREFIX)
                if value in values:
                    return value
        return None

    async def _persist_tag(self, card: CardData, value: str) -> None:
        """Write `sensei:<value>` to the note and reflect it in the in-memory `card.tags`."""
        tag = f"{TAG_PREFIX}{value}"
        if tag not in card.tags:
            await self._anki.update_card_tags(card.card_id, [tag])
            card.tags.append(tag)
