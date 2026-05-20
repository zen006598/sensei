"""TTS backfill orchestration: generate_all → sync.

Mirrors `src/word_service/backfill.py`. Trigger sites (the daily PTB
JobQueue and the `/tts` command handler) wrap this with their own
presentation; the pipeline only sequences generate + sync."""

from dataclasses import dataclass

from src.anki.sync import AnkiSyncer
from src.tts.generator import TTSGenerator, TtsBatchStats


@dataclass
class TtsBackfillResult:
    stats: TtsBatchStats
    sync_ok: bool


async def run_tts_backfill(
    generator: TTSGenerator, syncer: AnkiSyncer, deck: str
) -> TtsBackfillResult:
    """Single-pass backfill: generate_all → sync.

    Raises BatchAlreadyRunningError if a TTS batch is already in flight.
    A sync failure is absorbed by AnkiSyncer.try_sync and surfaced as
    sync_ok=False — it does not raise."""
    stats = await generator.generate_all(deck=deck)
    sync_ok = await syncer.try_sync("after tts")
    return TtsBackfillResult(stats=stats, sync_ok=sync_ok)
