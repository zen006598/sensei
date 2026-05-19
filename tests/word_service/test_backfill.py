from unittest.mock import AsyncMock, MagicMock

import pytest

from src.word_service.backfill import BackfillResult, run_full_backfill
from src.word_service.card_tagger import (
    BatchAlreadyRunningError,
    LocalBatchStats,
    RegisterBatchStats,
)


def _tagger(local_stats=None, register_stats=None, local_raises=None):
    tagger = MagicMock()
    if local_raises is not None:
        tagger.classify_local_all = AsyncMock(side_effect=local_raises)
    else:
        tagger.classify_local_all = AsyncMock(
            return_value=local_stats or LocalBatchStats(cards_scanned=1)
        )
    tagger.classify_register_all = AsyncMock(
        return_value=register_stats or RegisterBatchStats(cards_scanned=1)
    )
    return tagger


def _syncer(try_sync_results=None):
    syncer = MagicMock()
    if try_sync_results is None:
        syncer.try_sync = AsyncMock(return_value=True)
    else:
        syncer.try_sync = AsyncMock(side_effect=try_sync_results)
    return syncer


@pytest.mark.asyncio
async def test_run_full_backfill_returns_result_with_all_four_fields():
    tagger = _tagger(
        local_stats=LocalBatchStats(cards_scanned=5, frequency_added=2),
        register_stats=RegisterBatchStats(cards_scanned=5, register_added=3),
    )
    syncer = _syncer()

    result = await run_full_backfill(tagger, syncer)

    assert isinstance(result, BackfillResult)
    assert result.local.frequency_added == 2
    assert result.register.register_added == 3
    assert result.local_sync_ok is True
    assert result.register_sync_ok is True


@pytest.mark.asyncio
async def test_run_full_backfill_sequence_is_local_sync_register_sync():
    """Order matters: the spec requires the first sync to push local tags
    BEFORE the LLM pass runs (so a register-pass failure doesn't lose local progress)."""
    calls = []
    tagger = MagicMock()
    tagger.classify_local_all = AsyncMock(
        side_effect=lambda: calls.append("local") or LocalBatchStats()
    )
    tagger.classify_register_all = AsyncMock(
        side_effect=lambda: calls.append("register") or RegisterBatchStats()
    )
    syncer = MagicMock()
    syncer.try_sync = AsyncMock(
        side_effect=lambda label: calls.append(f"sync:{label}") or True
    )

    await run_full_backfill(tagger, syncer)

    assert calls == [
        "local",
        "sync:after local pass",
        "register",
        "sync:after register pass",
    ]


@pytest.mark.asyncio
async def test_run_full_backfill_sync_failure_does_not_abort_second_pass():
    tagger = _tagger()
    syncer = _syncer(try_sync_results=[False, True])

    result = await run_full_backfill(tagger, syncer)

    assert result.local_sync_ok is False
    assert result.register_sync_ok is True
    tagger.classify_register_all.assert_awaited_once()  # ran despite first sync failure


@pytest.mark.asyncio
async def test_run_full_backfill_propagates_batch_already_running():
    tagger = _tagger(local_raises=BatchAlreadyRunningError())
    syncer = _syncer()

    with pytest.raises(BatchAlreadyRunningError):
        await run_full_backfill(tagger, syncer)

    syncer.try_sync.assert_not_awaited()
    tagger.classify_register_all.assert_not_awaited()
