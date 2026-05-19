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


@pytest.mark.asyncio
async def test_get_card_field_returns_value_by_field_name():
    note = MagicMock()
    note.note_type.return_value = {"flds": [{"name": "front"}, {"name": "sound"}]}
    note.fields = ["hello", "[sound:hi.mp3]"]
    card = MagicMock()
    card.note.return_value = note
    col = MagicMock()
    col.get_card.return_value = card
    client = _client_with_col(col)

    assert await client.get_card_field(42, "sound") == "[sound:hi.mp3]"
    assert await client.get_card_field(42, "front") == "hello"


@pytest.mark.asyncio
async def test_get_card_field_returns_empty_when_field_absent():
    note = MagicMock()
    note.note_type.return_value = {"flds": [{"name": "front"}]}
    note.fields = ["hello"]
    card = MagicMock()
    card.note.return_value = note
    col = MagicMock()
    col.get_card.return_value = card
    client = _client_with_col(col)

    assert await client.get_card_field(42, "sound") == ""


@pytest.mark.asyncio
async def test_set_card_field_writes_value_and_persists_note():
    note = MagicMock()
    note.note_type.return_value = {"flds": [{"name": "front"}, {"name": "sound"}]}
    note.fields = ["hello", ""]
    card = MagicMock()
    card.note.return_value = note
    col = MagicMock()
    col.get_card.return_value = card
    client = _client_with_col(col)

    await client.set_card_field(42, "sound", "[sound:sensei_42.mp3]")

    assert note.fields[1] == "[sound:sensei_42.mp3]"
    col.update_note.assert_called_once_with(note)


@pytest.mark.asyncio
async def test_set_card_field_raises_keyerror_for_unknown_field():
    note = MagicMock()
    note.note_type.return_value = {"flds": [{"name": "front"}]}
    note.fields = ["hello"]
    card = MagicMock()
    card.note.return_value = note
    col = MagicMock()
    col.get_card.return_value = card
    client = _client_with_col(col)

    with pytest.raises(KeyError, match="sound"):
        await client.set_card_field(42, "sound", "x")
