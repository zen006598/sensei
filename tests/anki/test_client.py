from unittest.mock import MagicMock

import pytest

from src.anki.client import AnkiClient, _strip_html


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


def test_strip_html_returns_plaintext_unchanged_without_warning(recwarn):
    """Plain-text fields must not trigger MarkupResemblesLocatorWarning."""
    assert _strip_html("run") == "run"
    assert _strip_html("  trailing whitespace  ") == "trailing whitespace"
    assert len(recwarn) == 0


def test_strip_html_parses_actual_html():
    assert _strip_html("<b>hello</b>") == "hello"
    assert _strip_html("<p>a</p><p>b</p>") == "a b"


@pytest.mark.asyncio
async def test_get_all_card_ids_filters_by_deck_when_given():
    col = MagicMock()
    col.find_cards.return_value = [10, 20]
    client = _client_with_col(col)

    ids = await client.get_all_card_ids(deck="English")

    assert ids == [10, 20]
    col.find_cards.assert_called_once_with('deck:"English"')


@pytest.mark.asyncio
async def test_get_all_card_ids_unfiltered_when_deck_is_none():
    col = MagicMock()
    col.find_cards.return_value = [1, 2, 3]
    client = _client_with_col(col)

    ids = await client.get_all_card_ids()  # default deck=None

    assert ids == [1, 2, 3]
    col.find_cards.assert_called_once_with("")
