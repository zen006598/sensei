import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers import make_handlers
from src.tts.generator import TtsBatchStats
from src.word_service.card_tagger import BatchAlreadyRunningError  # noqa: F401


def _build_handlers(generator, prefs=None, syncer=None, tagger=None):
    sm = MagicMock()
    sm.has_active_session = MagicMock(return_value=False)
    if syncer is None:
        syncer = MagicMock()
        syncer.try_sync = AsyncMock(return_value=True)
    anki = MagicMock()
    if prefs is None:
        prefs = MagicMock()
        prefs.get_deck = MagicMock(return_value="English")
    if tagger is None:
        tagger = MagicMock()
        type(tagger).is_running = property(lambda self: False)
    return (
        make_handlers(sm, syncer, anki, prefs, tagger, generator),
        syncer,
        prefs,
    )


def _update_and_ctx(user_id: int = 1):
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat.id = 555
    update.effective_user.id = user_id
    ctx = MagicMock()
    ctx.bot.send_message = AsyncMock()
    return update, ctx


@pytest.mark.asyncio
async def test_tts_replies_already_running_when_generator_is_running():
    generator = MagicMock()
    type(generator).is_running = property(lambda self: True)
    handlers, _, _ = _build_handlers(generator)
    update, ctx = _update_and_ctx()

    await handlers["tts"](update, ctx)

    update.message.reply_text.assert_awaited_once()
    assert "already running" in update.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_tts_replies_pick_deck_when_no_deck_selected():
    generator = MagicMock()
    type(generator).is_running = property(lambda self: False)
    prefs = MagicMock()
    prefs.get_deck = MagicMock(return_value=None)
    handlers, _, _ = _build_handlers(generator, prefs=prefs)
    update, ctx = _update_and_ctx()

    await handlers["tts"](update, ctx)

    update.message.reply_text.assert_awaited_once()
    assert "decks" in update.message.reply_text.await_args.args[0].lower()


@pytest.mark.asyncio
async def test_tts_runs_pipeline_and_reports_stats():
    generator = MagicMock()
    type(generator).is_running = property(lambda self: False)
    generator.generate_all = AsyncMock(
        return_value=TtsBatchStats(cards_scanned=10, generated=8, skipped=2)
    )
    handlers, syncer, _ = _build_handlers(generator)
    update, ctx = _update_and_ctx()

    await handlers["tts"](update, ctx)

    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for t in pending:
        await t

    generator.generate_all.assert_awaited_once_with(deck="English")
    syncer.try_sync.assert_awaited_once()
    body = (
        ctx.bot.send_message.await_args.kwargs.get("text")
        or ctx.bot.send_message.await_args.args[1]
    )
    assert "generated=8" in body and "skipped=2" in body
