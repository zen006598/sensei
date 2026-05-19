from unittest.mock import AsyncMock

import pytest

from src.anki.sync import AnkiSyncer, SyncResult


def _syncer_with_result(result: SyncResult) -> AnkiSyncer:
    syncer = AnkiSyncer.__new__(AnkiSyncer)
    syncer._collection_path = "/dev/null"
    syncer._email = ""
    syncer._password = ""
    syncer.async_sync = AsyncMock(return_value=result)  # type: ignore[method-assign]
    return syncer


@pytest.mark.asyncio
async def test_try_sync_returns_true_on_success():
    syncer = _syncer_with_result(SyncResult(success=True, message="Sync complete"))
    ok = await syncer.try_sync("after local pass")
    assert ok is True


@pytest.mark.asyncio
async def test_try_sync_returns_false_on_failure():
    syncer = _syncer_with_result(SyncResult(success=False, message="network error"))
    ok = await syncer.try_sync("after register pass")
    assert ok is False


@pytest.mark.asyncio
async def test_try_sync_swallows_unexpected_exception():
    syncer = AnkiSyncer.__new__(AnkiSyncer)
    syncer._collection_path = "/dev/null"
    syncer._email = ""
    syncer._password = ""
    syncer.async_sync = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    ok = await syncer.try_sync("boom case")
    assert ok is False
