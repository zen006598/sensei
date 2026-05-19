import asyncio
import logging
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.app import register_jobs
from src.word_service.card_tagger import (
    BatchAlreadyRunningError,
    LocalBatchStats,
    RegisterBatchStats,
)


def _fake_app():
    app = MagicMock()
    app.job_queue.run_daily = MagicMock()
    return app


def _tagger_and_syncer():
    tagger = MagicMock()
    tagger.classify_local_all = AsyncMock(return_value=LocalBatchStats(cards_scanned=1))
    tagger.classify_register_all = AsyncMock(
        return_value=RegisterBatchStats(cards_scanned=1)
    )
    syncer = MagicMock()
    syncer.try_sync = AsyncMock(return_value=True)
    return tagger, syncer


def test_register_jobs_schedules_daily_with_local_tz():
    app = _fake_app()
    tagger, syncer = _tagger_and_syncer()

    register_jobs(app, tagger=tagger, syncer=syncer, daily_hour=3)

    app.job_queue.run_daily.assert_called_once()
    kwargs = app.job_queue.run_daily.call_args.kwargs
    assert kwargs["name"] == "daily_retag"
    scheduled_time = kwargs["time"]
    assert scheduled_time.hour == 3
    # UTC+8 offset (no DST, hard-coded Asia/Taipei in app.py)
    assert scheduled_time.tzinfo.utcoffset(None) == timedelta(hours=8)


def test_register_jobs_callback_runs_full_pipeline_on_happy_path():
    app = _fake_app()
    tagger, syncer = _tagger_and_syncer()

    register_jobs(app, tagger=tagger, syncer=syncer, daily_hour=3)
    callback = app.job_queue.run_daily.call_args.args[0]

    asyncio.run(callback(MagicMock()))

    tagger.classify_local_all.assert_awaited_once()
    tagger.classify_register_all.assert_awaited_once()
    assert syncer.try_sync.await_count == 2


def test_register_jobs_callback_swallows_batch_already_running(caplog):
    app = _fake_app()
    tagger = MagicMock()
    tagger.classify_local_all = AsyncMock(side_effect=BatchAlreadyRunningError())
    tagger.classify_register_all = AsyncMock()
    syncer = MagicMock()
    syncer.try_sync = AsyncMock(return_value=True)

    register_jobs(app, tagger=tagger, syncer=syncer, daily_hour=3)
    callback = app.job_queue.run_daily.call_args.args[0]

    with caplog.at_level(logging.WARNING):
        asyncio.run(callback(MagicMock()))

    assert "another batch already running" in caplog.text
    tagger.classify_register_all.assert_not_awaited()


def test_register_jobs_callback_does_not_swallow_other_exceptions():
    app = _fake_app()
    tagger = MagicMock()
    tagger.classify_local_all = AsyncMock(side_effect=RuntimeError("boom"))
    syncer = MagicMock()

    register_jobs(app, tagger=tagger, syncer=syncer, daily_hour=3)
    callback = app.job_queue.run_daily.call_args.args[0]

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(callback(MagicMock()))
