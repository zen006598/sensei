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


def _fake_prefs(deck=None):
    p = MagicMock()
    p.get_deck = MagicMock(return_value=deck)
    return p


def _fake_generator():
    from src.tts.generator import TtsBatchStats

    g = MagicMock()
    g.generate_all = AsyncMock(return_value=TtsBatchStats(cards_scanned=1))
    return g


def test_register_jobs_schedules_daily_with_local_tz():
    app = _fake_app()
    tagger, syncer = _tagger_and_syncer()

    register_jobs(
        app,
        tagger=tagger,
        syncer=syncer,
        generator=_fake_generator(),
        prefs_store=_fake_prefs(deck="English"),
        allowed_user_ids={1},
        retag_hour=3,
        tts_hour=4,
    )

    assert app.job_queue.run_daily.call_count == 2
    retag_call = next(
        c
        for c in app.job_queue.run_daily.call_args_list
        if c.kwargs["name"] == "daily_retag"
    )
    scheduled_time = retag_call.kwargs["time"]
    assert scheduled_time.hour == 3
    # UTC+8 offset (no DST, hard-coded Asia/Taipei in app.py)
    assert scheduled_time.tzinfo.utcoffset(None) == timedelta(hours=8)


def test_register_jobs_callback_runs_full_pipeline_on_happy_path():
    app = _fake_app()
    tagger, syncer = _tagger_and_syncer()

    register_jobs(
        app,
        tagger=tagger,
        syncer=syncer,
        generator=_fake_generator(),
        prefs_store=_fake_prefs(deck="English"),
        allowed_user_ids={1},
        retag_hour=3,
        tts_hour=4,
    )
    retag_call = next(
        c
        for c in app.job_queue.run_daily.call_args_list
        if c.kwargs["name"] == "daily_retag"
    )
    callback = retag_call.args[0]

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

    register_jobs(
        app,
        tagger=tagger,
        syncer=syncer,
        generator=_fake_generator(),
        prefs_store=_fake_prefs(deck="English"),
        allowed_user_ids={1},
        retag_hour=3,
        tts_hour=4,
    )
    retag_call = next(
        c
        for c in app.job_queue.run_daily.call_args_list
        if c.kwargs["name"] == "daily_retag"
    )
    callback = retag_call.args[0]

    with caplog.at_level(logging.WARNING):
        asyncio.run(callback(MagicMock()))

    assert "another batch already running" in caplog.text
    tagger.classify_register_all.assert_not_awaited()


def test_register_jobs_callback_does_not_swallow_other_exceptions():
    app = _fake_app()
    tagger = MagicMock()
    tagger.classify_local_all = AsyncMock(side_effect=RuntimeError("boom"))
    syncer = MagicMock()

    register_jobs(
        app,
        tagger=tagger,
        syncer=syncer,
        generator=_fake_generator(),
        prefs_store=_fake_prefs(deck="English"),
        allowed_user_ids={1},
        retag_hour=3,
        tts_hour=4,
    )
    retag_call = next(
        c
        for c in app.job_queue.run_daily.call_args_list
        if c.kwargs["name"] == "daily_retag"
    )
    callback = retag_call.args[0]

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(callback(MagicMock()))


def test_register_jobs_schedules_both_retag_and_tts():
    app = _fake_app()
    tagger, syncer = _tagger_and_syncer()
    generator = _fake_generator()
    prefs = _fake_prefs(deck="English")

    register_jobs(
        app,
        tagger=tagger,
        syncer=syncer,
        generator=generator,
        prefs_store=prefs,
        allowed_user_ids={1},
        retag_hour=3,
        tts_hour=4,
    )

    assert app.job_queue.run_daily.call_count == 2
    names = [call.kwargs["name"] for call in app.job_queue.run_daily.call_args_list]
    assert "daily_retag" in names and "daily_tts" in names


def test_register_jobs_daily_tts_skips_when_no_deck_selected(caplog):
    app = _fake_app()
    tagger, syncer = _tagger_and_syncer()
    generator = _fake_generator()
    prefs = _fake_prefs(deck=None)

    register_jobs(
        app,
        tagger=tagger,
        syncer=syncer,
        generator=generator,
        prefs_store=prefs,
        allowed_user_ids={1},
        retag_hour=3,
        tts_hour=4,
    )

    # Pull out the daily_tts callback by name
    callbacks = {
        call.kwargs["name"]: call.args[0]
        for call in app.job_queue.run_daily.call_args_list
    }
    tts_cb = callbacks["daily_tts"]

    with caplog.at_level(logging.WARNING):
        asyncio.run(tts_cb(MagicMock()))

    assert "no deck selected" in caplog.text.lower()
    generator.generate_all.assert_not_awaited()


def test_register_jobs_daily_tts_runs_with_selected_deck():
    app = _fake_app()
    tagger, syncer = _tagger_and_syncer()
    generator = _fake_generator()
    prefs = _fake_prefs(deck="English")

    register_jobs(
        app,
        tagger=tagger,
        syncer=syncer,
        generator=generator,
        prefs_store=prefs,
        allowed_user_ids={1},
        retag_hour=3,
        tts_hour=4,
    )

    callbacks = {
        call.kwargs["name"]: call.args[0]
        for call in app.job_queue.run_daily.call_args_list
    }
    asyncio.run(callbacks["daily_tts"](MagicMock()))

    generator.generate_all.assert_awaited_once_with(deck="English")
