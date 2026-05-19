from unittest.mock import AsyncMock, MagicMock

import pytest

from src.anki.card_data import CardData
from src.word_service.card_tagger import (
    ACADEMICS,
    CardTagger,
    FREQUENCIES,
    REGISTERS,
    TAG_PREFIX,
)


def _tagger():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    return CardTagger(anki, classifier), anki, classifier


def _card(tags=None) -> CardData:
    return CardData(
        card_id=1, front="run", back="走る", tags=list(tags or []), deck_name="EN"
    )


def test_constants_re_exported():
    assert TAG_PREFIX == "sensei:"
    assert "common" in FREQUENCIES
    assert "formal" in REGISTERS
    assert "academic" in ACADEMICS


def test_cached_returns_matching_value():
    tagger, _, _ = _tagger()
    card = _card(tags=["sensei:formal", "other"])
    assert tagger._cached(card, REGISTERS) == "formal"


def test_cached_returns_none_when_absent():
    tagger, _, _ = _tagger()
    card = _card(tags=["other"])
    assert tagger._cached(card, REGISTERS) is None


def test_cached_ignores_unrelated_sensei_tags():
    """sensei:common is a frequency tag, not a register tag."""
    tagger, _, _ = _tagger()
    card = _card(tags=["sensei:common"])
    assert tagger._cached(card, REGISTERS) is None


@pytest.mark.asyncio
async def test_persist_tag_writes_when_missing_and_updates_card():
    tagger, anki, _ = _tagger()
    card = _card(tags=[])
    await tagger._persist_tag(card, "formal")
    anki.update_card_tags.assert_awaited_once_with(1, ["sensei:formal"])
    assert "sensei:formal" in card.tags


@pytest.mark.asyncio
async def test_persist_tag_skips_when_already_present():
    tagger, anki, _ = _tagger()
    card = _card(tags=["sensei:formal"])
    await tagger._persist_tag(card, "formal")
    anki.update_card_tags.assert_not_awaited()
