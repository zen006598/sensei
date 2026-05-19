import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers import make_handlers
from src.word_service.card_tagger import (
    BatchAlreadyRunningError,
    LocalBatchStats,
    RegisterBatchStats,
)


def _build_handlers(tagger, syncer=None):
    sm = MagicMock()
    sm.has_active_session = MagicMock(return_value=False)
    if syncer is None:
        syncer = MagicMock()
        syncer.try_sync = AsyncMock(return_value=True)
    anki = MagicMock()
    prefs = MagicMock()
    return make_handlers(sm, syncer, anki, prefs, tagger), syncer


def _update_and_ctx():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 555
    ctx = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return update, ctx


@pytest.mark.asyncio
async def test_retag_replies_already_running_when_tagger_is_running():
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: True)
    handlers, _ = _build_handlers(tagger)
    update, ctx = _update_and_ctx()

    await handlers["retag"](update, ctx)

    update.message.reply_text.assert_awaited_once()
    assert "already running" in update.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_retag_runs_full_pipeline_and_reports_stats():
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: False)
    tagger.classify_local_all = AsyncMock(
        return_value=LocalBatchStats(
            cards_scanned=2, frequency_added=1, academic_added=1
        )
    )
    tagger.classify_register_all = AsyncMock(
        return_value=RegisterBatchStats(cards_scanned=2, register_added=2)
    )
    handlers, syncer = _build_handlers(tagger)
    update, ctx = _update_and_ctx()

    await handlers["retag"](update, ctx)

    # Let the spawned create_task finish
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        await t

    update.message.reply_text.assert_awaited_once()
    assert "started" in update.message.reply_text.await_args.args[0].lower()
    tagger.classify_local_all.assert_awaited_once()
    tagger.classify_register_all.assert_awaited_once()
    assert syncer.try_sync.await_count == 2
    ctx.bot.send_message.assert_awaited_once()
    body = (
        ctx.bot.send_message.await_args.kwargs.get("text")
        or ctx.bot.send_message.await_args.args[1]
    )
    assert "frequency=1" in body and "register=2" in body


@pytest.mark.asyncio
async def test_retag_continues_after_first_sync_failure():
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: False)
    tagger.classify_local_all = AsyncMock(return_value=LocalBatchStats(cards_scanned=1))
    tagger.classify_register_all = AsyncMock(
        return_value=RegisterBatchStats(cards_scanned=1)
    )
    syncer = MagicMock()
    syncer.try_sync = AsyncMock(side_effect=[False, True])
    handlers, _ = _build_handlers(tagger, syncer)
    update, ctx = _update_and_ctx()

    await handlers["retag"](update, ctx)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        await t

    # Register batch still ran despite first sync failure
    tagger.classify_register_all.assert_awaited_once()
    # Final message mentions the failed sync
    final = (
        ctx.bot.send_message.await_args.kwargs.get("text")
        or ctx.bot.send_message.await_args.args[1]
    )
    assert "sync" in final.lower() and "failed" in final.lower()


@pytest.mark.asyncio
async def test_retag_handles_batch_already_running_race_inside_task():
    """Backstop: even if the up-front is_running check missed it."""
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: False)
    tagger.classify_local_all = AsyncMock(side_effect=BatchAlreadyRunningError())
    handlers, _ = _build_handlers(tagger)
    update, ctx = _update_and_ctx()

    await handlers["retag"](update, ctx)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        await t

    final = (
        ctx.bot.send_message.await_args.kwargs.get("text")
        or ctx.bot.send_message.await_args.args[1]
    )
    assert "already running" in final.lower()
