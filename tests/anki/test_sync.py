from unittest.mock import AsyncMock, MagicMock

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


def test_sync_media_blocking_waits_until_inactive(monkeypatch):
    """Must poll media_sync_status until the background sync goes inactive, so
    the caller's close() doesn't abort an in-flight upload."""
    monkeypatch.setattr("src.anki.sync.time.sleep", lambda *_: None)
    col = MagicMock()
    # active for one poll, then done
    col.media_sync_status = MagicMock(
        side_effect=[MagicMock(active=True), MagicMock(active=False)]
    )

    AnkiSyncer._sync_media_blocking(col, object())

    col.media.check.assert_called_once()  # reconcile folder ⇄ DB before upload
    col.sync_media.assert_called_once()
    assert col.media_sync_status.call_count == 2
    col.abort_media_sync.assert_not_called()


def test_sync_media_blocking_aborts_on_timeout(monkeypatch):
    """If media sync never finishes within the timeout, abort it cleanly rather
    than letting close() kill it mid-flight."""
    monkeypatch.setattr("src.anki.sync.time.sleep", lambda *_: None)
    monkeypatch.setattr(
        "src.anki.sync.time.monotonic", MagicMock(side_effect=[100.0, 101.0])
    )
    col = MagicMock()
    col.media_sync_status = MagicMock(return_value=MagicMock(active=True))

    AnkiSyncer._sync_media_blocking(col, object(), timeout=0.0)

    col.sync_media.assert_called_once()
    col.abort_media_sync.assert_called_once()
