from unittest.mock import MagicMock

import pytest
from telegram.ext import ApplicationHandlerStop

from src.bot.app import _make_auth_gate


async def test_allows_listed_user():
    gate = _make_auth_gate({123, 456})
    update = MagicMock()
    update.effective_user.id = 123

    await gate(update, None)


async def test_blocks_unlisted_user():
    gate = _make_auth_gate({123})
    update = MagicMock()
    update.effective_user.id = 999

    with pytest.raises(ApplicationHandlerStop):
        await gate(update, None)


async def test_blocks_update_with_no_user():
    gate = _make_auth_gate({123})
    update = MagicMock()
    update.effective_user = None

    with pytest.raises(ApplicationHandlerStop):
        await gate(update, None)


async def test_empty_allowlist_blocks_all():
    """Gate is only registered when allowlist is non-empty, but defensive: empty set blocks everyone."""
    gate = _make_auth_gate(set())
    update = MagicMock()
    update.effective_user.id = 123

    with pytest.raises(ApplicationHandlerStop):
        await gate(update, None)
