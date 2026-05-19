from unittest.mock import MagicMock

import pytest

from src.anki.client import AnkiClient


def _client_with_col(col_mock: MagicMock) -> AnkiClient:
    client = AnkiClient("/dev/null")
    client._with_collection = lambda fn: fn(col_mock)  # bypass real Collection open

    async def _run_locked(fn):
        return client._with_collection(fn)

    client._run_locked = _run_locked  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_get_all_card_ids_returns_collection_ids():
    col = MagicMock()
    col.find_cards.return_value = [10, 20, 30]
    client = _client_with_col(col)

    ids = await client.get_all_card_ids()

    assert ids == [10, 20, 30]
    col.find_cards.assert_called_once_with("")


@pytest.mark.asyncio
async def test_get_card_returns_card_data_via_card_to_data():
    col = MagicMock()
    note = MagicMock()
    note.tags = ["sensei:formal"]
    note.fields = ["<b>hello</b>", "<i>greeting</i>"]
    card = MagicMock()
    card.did = 1
    card.note.return_value = note
    col.get_card.return_value = card
    col.decks.name.return_value = "Default"

    client = _client_with_col(col)
    data = await client.get_card(42)

    assert data.card_id == 42
    assert data.front == "hello"
    assert data.back == "greeting"
    assert data.tags == ["sensei:formal"]
    assert data.deck_name == "Default"
