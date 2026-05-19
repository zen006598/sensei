"""Tag-backfill orchestration: local pass → sync → register pass → sync.

The pipeline is shared by two triggers — the daily PTB JobQueue (`src/main.py`)
and the manual `/retag` command (`src/bot/handlers.py`). Each consumer wraps
this with its own presentation/error reporting; the pipeline itself only
sequences the two batch passes and the two syncs."""

from dataclasses import dataclass

from src.anki.sync import AnkiSyncer
from src.word_service.card_tagger import (
    CardTagger,
    LocalBatchStats,
    RegisterBatchStats,
)


@dataclass
class BackfillResult:
    local: LocalBatchStats
    register: RegisterBatchStats
    local_sync_ok: bool
    register_sync_ok: bool


async def run_full_backfill(
    tagger: CardTagger, syncer: AnkiSyncer, deck: str | None = None
) -> BackfillResult:
    """Two-pass backfill: local → sync → register → sync.

    Raises `BatchAlreadyRunningError` (from card_tagger) if a batch is already
    in flight. Sync failures are absorbed by `AnkiSyncer.try_sync` and reflected
    as `False` flags on the returned result — they do not abort the second pass."""
    local = await tagger.classify_local_all(deck=deck)
    local_sync_ok = await syncer.try_sync("after local pass")
    register = await tagger.classify_register_all(deck=deck)
    register_sync_ok = await syncer.try_sync("after register pass")
    return BackfillResult(
        local=local,
        register=register,
        local_sync_ok=local_sync_ok,
        register_sync_ok=register_sync_ok,
    )
