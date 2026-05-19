"""Anki tag I/O for the `sensei:*` namespace.

`CardTagger` owns every read and write of the four `sensei:*` axes
(frequency / academic / register). Two call sites:

- Quiz hot path: `state_machine` calls `classify(card)`, which ensures all
  three axes are present (lazy, per-card, may call the LLM for register).
- Background batch: the daily JobQueue and `/retag` handler call
  `classify_local_all()` (no LLM) then `classify_register_all()` (LLM)
  via `AnkiSyncer.try_sync` between them.

Batch methods share `_batch_lock`; only one batch runs at a time.
See docs/superpowers/specs/2026-05-19-tag-backfill-job-design.md for
the full design."""

import asyncio
import logging
from dataclasses import dataclass
from typing import cast, get_args

from src.agent.schemas import Frequency, Register
from src.anki.card_data import CardData
from src.anki.client import AnkiClient
from src.word_service.word_classifier import WordClassifier

logger = logging.getLogger(__name__)

TAG_PREFIX = "sensei:"
FREQUENCIES: frozenset[str] = frozenset(get_args(Frequency))
REGISTERS: frozenset[str] = frozenset(get_args(Register))
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
    """Counters for a `classify_register_all` run.

    `register_failures` counts only LLM-leg failures (transport / parse / None return).
    A card that succeeded on the LLM call but failed during the tag write goes to
    `write_failures` — its LLM cost was still spent. The output formatter
    surfaces this distinction (Task 8)."""

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

    async def classify(self, card: CardData) -> tuple[Frequency, Register | None]:
        """Quiz hot path. Applies all 3 axes if missing.
        Returns (frequency, register). `register` is None if its LLM call failed."""
        await self.classify_local(card)
        # Frequency is always present after classify_local (it ran or was already cached);
        # read it back so callers don't need to compute it themselves.
        frequency = self._cached(card, FREQUENCIES)
        assert frequency is not None, "classify_local must leave a frequency tag"

        register_delta = await self.classify_register(card)
        if register_delta.failed:
            return cast("Frequency", frequency), None
        # classify_register guarantees a register tag is present when failed=False,
        # and _cached only returns values that came from REGISTERS — i.e. Register members.
        cached_register = self._cached(card, REGISTERS)
        assert cached_register is not None
        return cast("Frequency", frequency), cast("Register", cached_register)

    async def classify_local(self, card: CardData) -> LocalDelta:
        """Frequency + academic. No LLM. Idempotent: only writes missing tags.

        The academic axis tags only positives — a False result writes nothing,
        so non-academic cards remain untagged and re-checked cheaply each run."""
        frequency_added = False
        academic_added = False

        if self._cached(card, FREQUENCIES) is None:
            value = self._classifier.frequency(card.front)
            await self._persist_tag(card, value)
            frequency_added = True

        if self._cached(card, ACADEMICS) is None:
            if self._classifier.is_academic(card.front):
                await self._persist_tag(card, "academic")
                academic_added = True

        return LocalDelta(
            frequency_added=frequency_added, academic_added=academic_added
        )

    async def classify_register(self, card: CardData) -> RegisterDelta:
        """Register only. Calls the LLM through WordClassifier; on failure
        leaves the card untouched so a future run retries."""
        if self._cached(card, REGISTERS) is not None:
            return RegisterDelta(register_added=False, failed=False)

        value = await self._classifier.register(card)
        if value is None:
            return RegisterDelta(register_added=False, failed=True)

        await self._persist_tag(card, value)
        return RegisterDelta(register_added=True, failed=False)

    async def classify_local_all(self) -> LocalBatchStats:
        """Iterate every card in the collection, ensure frequency + academic
        tags are present. No LLM. Raises BatchAlreadyRunningError if another
        batch is already in flight."""
        if self._batch_lock.locked():
            raise BatchAlreadyRunningError()
        stats = LocalBatchStats()
        async with self._batch_lock:
            card_ids = await self._anki.get_all_card_ids()
            stats.cards_scanned = len(card_ids)
            for card_id in card_ids:
                try:
                    card = await self._anki.get_card(card_id)
                    delta = await self.classify_local(card)
                    if delta.frequency_added:
                        stats.frequency_added += 1
                    if delta.academic_added:
                        stats.academic_added += 1
                except Exception:
                    logger.warning(
                        "classify_local_all: card %s failed; continuing",
                        card_id,
                        exc_info=True,
                    )
                    stats.write_failures += 1
        return stats

    async def classify_register_all(self) -> RegisterBatchStats:
        """Iterate every card and ensure a register tag is present.
        Uses the LLM via WordClassifier. Shares _batch_lock with classify_local_all."""
        if self._batch_lock.locked():
            raise BatchAlreadyRunningError()
        stats = RegisterBatchStats()
        async with self._batch_lock:
            card_ids = await self._anki.get_all_card_ids()
            stats.cards_scanned = len(card_ids)
            for card_id in card_ids:
                try:
                    card = await self._anki.get_card(card_id)
                    delta = await self.classify_register(card)
                    if delta.register_added:
                        stats.register_added += 1
                    if delta.failed:
                        stats.register_failures += 1
                except Exception:
                    logger.warning(
                        "classify_register_all: card %s failed; continuing",
                        card_id,
                        exc_info=True,
                    )
                    stats.write_failures += 1
        return stats
