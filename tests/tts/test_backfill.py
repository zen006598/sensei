from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tts.backfill import TtsBackfillResult, run_tts_backfill
from src.tts.generator import TtsBatchStats
from src.word_service.card_tagger import BatchAlreadyRunningError


def _gen(stats=None, raises=None):
    g = MagicMock()
    if raises is not None:
        g.generate_all = AsyncMock(side_effect=raises)
    else:
        g.generate_all = AsyncMock(return_value=stats or TtsBatchStats(cards_scanned=1))
    return g


def _syncer(try_sync_result=True):
    s = MagicMock()
    s.try_sync = AsyncMock(return_value=try_sync_result)
    return s


@pytest.mark.asyncio
async def test_run_tts_backfill_happy_path_returns_result():
    gen = _gen(TtsBatchStats(cards_scanned=5, generated=3, skipped=2))
    syncer = _syncer()

    result = await run_tts_backfill(gen, syncer, deck="English")

    assert isinstance(result, TtsBackfillResult)
    assert result.stats.generated == 3
    assert result.sync_ok is True
    gen.generate_all.assert_awaited_once_with(deck="English")


@pytest.mark.asyncio
async def test_run_tts_backfill_reflects_sync_failure_without_raising():
    gen = _gen()
    syncer = _syncer(try_sync_result=False)

    result = await run_tts_backfill(gen, syncer, deck="English")

    assert result.sync_ok is False


@pytest.mark.asyncio
async def test_run_tts_backfill_propagates_batch_already_running():
    gen = _gen(raises=BatchAlreadyRunningError())
    syncer = _syncer()

    with pytest.raises(BatchAlreadyRunningError):
        await run_tts_backfill(gen, syncer, deck="English")

    syncer.try_sync.assert_not_awaited()
