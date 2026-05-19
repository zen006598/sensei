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


@pytest.mark.asyncio
async def test_classify_local_writes_missing_frequency_and_academic_no_llm():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="common")
    classifier.is_academic = MagicMock(return_value=True)
    classifier.register = AsyncMock()  # must NOT be called
    tagger = CardTagger(anki, classifier)
    card = _card(tags=[])

    delta = await tagger.classify_local(card)

    assert delta.frequency_added is True
    assert delta.academic_added is True
    assert {"sensei:common", "sensei:academic"} <= set(card.tags)
    classifier.register.assert_not_awaited()


@pytest.mark.asyncio
async def test_classify_local_skips_already_tagged_axes():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="common")
    classifier.is_academic = MagicMock(return_value=False)
    tagger = CardTagger(anki, classifier)
    card = _card(tags=["sensei:rare"])  # frequency already cached as rare

    delta = await tagger.classify_local(card)

    assert delta.frequency_added is False
    assert delta.academic_added is False
    anki.update_card_tags.assert_not_awaited()


@pytest.mark.asyncio
async def test_classify_local_omits_academic_tag_when_negative():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="common")
    classifier.is_academic = MagicMock(return_value=False)
    tagger = CardTagger(anki, classifier)
    card = _card(tags=[])

    delta = await tagger.classify_local(card)

    assert delta.frequency_added is True
    assert delta.academic_added is False
    assert "sensei:common" in card.tags
    assert "sensei:academic" not in card.tags


@pytest.mark.asyncio
async def test_classify_local_preserves_stale_academic_tag_when_classifier_flips():
    """Spec: fill-only, no overwrite. If sensei:academic is cached and
    is_academic now returns False (e.g. AWL data updated), the tag stays."""
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="common")
    classifier.is_academic = MagicMock()  # must NOT be called
    tagger = CardTagger(anki, classifier)
    card = _card(tags=["sensei:common", "sensei:academic"])

    delta = await tagger.classify_local(card)

    assert delta.academic_added is False
    assert "sensei:academic" in card.tags
    classifier.is_academic.assert_not_called()  # short-circuited by _cached


@pytest.mark.asyncio
async def test_classify_register_writes_when_missing():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.register = AsyncMock(return_value="formal")
    tagger = CardTagger(anki, classifier)
    card = _card(tags=[])

    delta = await tagger.classify_register(card)

    assert delta.register_added is True
    assert delta.failed is False
    assert "sensei:formal" in card.tags


@pytest.mark.asyncio
async def test_classify_register_skips_when_cached():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.register = AsyncMock()
    tagger = CardTagger(anki, classifier)
    card = _card(tags=["sensei:informal"])

    delta = await tagger.classify_register(card)

    assert delta.register_added is False
    assert delta.failed is False
    classifier.register.assert_not_awaited()


@pytest.mark.asyncio
async def test_classify_register_records_failure_on_llm_none():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.register = AsyncMock(return_value=None)
    tagger = CardTagger(anki, classifier)
    card = _card(tags=[])

    delta = await tagger.classify_register(card)

    assert delta.register_added is False
    assert delta.failed is True
    anki.update_card_tags.assert_not_awaited()


@pytest.mark.asyncio
async def test_classify_returns_frequency_and_register_on_cold_card():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="common")
    classifier.is_academic = MagicMock(return_value=False)
    classifier.register = AsyncMock(return_value="neutral")
    tagger = CardTagger(anki, classifier)
    card = _card(tags=[])

    frequency, register = await tagger.classify(card)

    assert frequency == "common"
    assert register == "neutral"
    assert "sensei:common" in card.tags
    assert "sensei:neutral" in card.tags


@pytest.mark.asyncio
async def test_classify_returns_none_register_when_llm_fails():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="rare")
    classifier.is_academic = MagicMock(return_value=False)
    classifier.register = AsyncMock(return_value=None)
    tagger = CardTagger(anki, classifier)
    card = _card(tags=[])

    frequency, register = await tagger.classify(card)

    assert frequency == "rare"
    assert register is None
    assert "sensei:rare" in card.tags
    assert all(not t.startswith("sensei:") or t == "sensei:rare" for t in card.tags)


@pytest.mark.asyncio
async def test_classify_uses_cache_for_all_three_axes():
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock()
    classifier.is_academic = MagicMock()
    classifier.register = AsyncMock()
    tagger = CardTagger(anki, classifier)
    card = _card(tags=["sensei:common", "sensei:formal", "sensei:academic"])

    frequency, register = await tagger.classify(card)

    assert frequency == "common"
    assert register == "formal"
    classifier.frequency.assert_not_called()
    classifier.is_academic.assert_not_called()
    classifier.register.assert_not_awaited()
    anki.update_card_tags.assert_not_awaited()
