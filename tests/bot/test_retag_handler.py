import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers import make_handlers
from src.word_service.card_tagger import (
    BatchAlreadyRunningError,
    LocalBatchStats,
    RegisterBatchStats,
)


def _build_handlers(tagger, syncer=None, prefs=None, generator=None):
    sm = MagicMock()
    sm.has_active_session = MagicMock(return_value=False)
    if syncer is None:
        syncer = MagicMock()
        syncer.try_sync = AsyncMock(return_value=True)
    anki = MagicMock()
    if prefs is None:
        prefs = MagicMock()
        prefs.get_deck = MagicMock(return_value="English")
    if generator is None:
        generator = MagicMock()
        type(generator).is_running = property(lambda self: False)
    return make_handlers(sm, syncer, anki, prefs, tagger, generator), syncer


def _update_and_ctx(user_id: int = 1):
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 555
    update.effective_user.id = user_id
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


@pytest.mark.asyncio
async def test_retag_final_message_includes_scanned_count_and_per_phase_failures():
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: False)
    tagger.classify_local_all = AsyncMock(
        return_value=LocalBatchStats(
            cards_scanned=10, frequency_added=3, academic_added=1, write_failures=2
        )
    )
    tagger.classify_register_all = AsyncMock(
        return_value=RegisterBatchStats(
            cards_scanned=10,
            register_added=4,
            register_failures=1,
            write_failures=1,
        )
    )
    handlers, _ = _build_handlers(tagger)
    update, ctx = _update_and_ctx()

    await handlers["retag"](update, ctx)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        await t

    body = (
        ctx.bot.send_message.await_args.kwargs.get("text")
        or ctx.bot.send_message.await_args.args[1]
    )
    assert "Scanned 10 card(s)" in body
    assert "write_failures=2" in body  # local-phase write failures surfaced
    assert "write_failures=1" in body  # register-phase write failures surfaced
    assert "llm_failures=1" in body


@pytest.mark.asyncio
async def test_retag_replies_pick_deck_when_no_deck_selected():
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: False)
    syncer = MagicMock()
    syncer.try_sync = AsyncMock(return_value=True)
    prefs = MagicMock()
    prefs.get_deck = MagicMock(return_value=None)
    sm = MagicMock()
    sm.has_active_session = MagicMock(return_value=False)
    anki = MagicMock()
    generator = MagicMock()
    type(generator).is_running = property(lambda self: False)
    handlers = make_handlers(sm, syncer, anki, prefs, tagger, generator)

    update, ctx = _update_and_ctx()
    await handlers["retag"](update, ctx)

    update.message.reply_text.assert_awaited_once()
    assert "decks" in update.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_retag_passes_user_selected_deck_to_full_backfill():
    tagger = MagicMock()
    type(tagger).is_running = property(lambda self: False)
    tagger.classify_local_all = AsyncMock(return_value=LocalBatchStats(cards_scanned=1))
    tagger.classify_register_all = AsyncMock(
        return_value=RegisterBatchStats(cards_scanned=1)
    )
    syncer = MagicMock()
    syncer.try_sync = AsyncMock(return_value=True)
    prefs = MagicMock()
    prefs.get_deck = MagicMock(return_value="English")
    sm = MagicMock()
    sm.has_active_session = MagicMock(return_value=False)
    anki = MagicMock()
    generator = MagicMock()
    type(generator).is_running = property(lambda self: False)
    handlers = make_handlers(sm, syncer, anki, prefs, tagger, generator)

    update, ctx = _update_and_ctx()
    await handlers["retag"](update, ctx)

    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        await t

    tagger.classify_local_all.assert_awaited_once_with(deck="English")
    tagger.classify_register_all.assert_awaited_once_with(deck="English")
    prefs.get_deck.assert_called_once_with(1)
