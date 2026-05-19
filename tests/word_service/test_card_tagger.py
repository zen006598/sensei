import asyncio as _asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.anki.card_data import CardData
from src.word_service.card_tagger import (
    ACADEMICS,
    BatchAlreadyRunningError,
    CardTagger,
    FREQUENCIES,
    LocalBatchStats,
    REGISTERS,
    RegisterBatchStats,
    TAG_PREFIX,
    _REGISTER_BATCH_SIZE,
    _REGISTER_BATCH_SLEEP_SECONDS,
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


@pytest.mark.asyncio
async def test_classify_with_frequency_cached_skips_frequency_lookup_and_calls_llm():
    """Post-batch state: frequency is cached by classify_local_all; register
    is filled lazily by the quiz path. The frequency classifier must NOT
    re-run; the LLM must."""
    anki = MagicMock()
    anki.update_card_tags = AsyncMock()
    classifier = MagicMock()
    classifier.frequency = MagicMock()  # must NOT be called
    classifier.is_academic = MagicMock(return_value=False)
    classifier.register = AsyncMock(return_value="formal")
    tagger = CardTagger(anki, classifier)
    card = _card(tags=["sensei:common"])

    frequency, register = await tagger.classify(card)

    assert frequency == "common"
    assert register == "formal"
    classifier.frequency.assert_not_called()
    classifier.register.assert_awaited_once_with(card)
    assert "sensei:formal" in card.tags


def _tagger_with_cards(card_map: dict[int, CardData], classifier=None):
    """Build a CardTagger whose AnkiClient returns the supplied cards."""
    anki = MagicMock()
    anki.get_all_card_ids = AsyncMock(return_value=list(card_map.keys()))
    anki.get_card = AsyncMock(side_effect=lambda cid: card_map[cid])
    anki.update_card_tags = AsyncMock()
    if classifier is None:
        classifier = MagicMock()
        classifier.frequency = MagicMock(return_value="common")
        classifier.is_academic = MagicMock(return_value=False)
        classifier.register = AsyncMock(return_value="neutral")
    return CardTagger(anki, classifier), anki, classifier


@pytest.mark.asyncio
async def test_classify_local_all_iterates_all_cards_and_accumulates_stats():
    cards = {
        1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d"),
        2: CardData(card_id=2, front="b", back="", tags=["sensei:rare"], deck_name="d"),
        3: CardData(card_id=3, front="c", back="", tags=[], deck_name="d"),
    }
    classifier = MagicMock()
    classifier.frequency = MagicMock(side_effect=lambda w: "common")
    classifier.is_academic = MagicMock(side_effect=lambda w: w == "a")
    classifier.register = AsyncMock()  # MUST NOT be called
    tagger, _, _ = _tagger_with_cards(cards, classifier)

    stats = await tagger.classify_local_all()

    assert isinstance(stats, LocalBatchStats)
    assert stats.cards_scanned == 3
    assert stats.frequency_added == 2  # card 2 already had sensei:rare
    assert stats.academic_added == 1  # only card 1 is academic
    assert stats.write_failures == 0
    classifier.register.assert_not_awaited()


@pytest.mark.asyncio
async def test_classify_local_all_swallows_per_card_exceptions():
    cards = {
        1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d"),
        2: CardData(card_id=2, front="b", back="", tags=[], deck_name="d"),
    }
    classifier = MagicMock()
    classifier.frequency = MagicMock(return_value="common")
    classifier.is_academic = MagicMock(return_value=False)
    tagger, anki, _ = _tagger_with_cards(cards, classifier)

    async def write(card_id, tags):
        if card_id == 1:
            raise RuntimeError("disk full")

    anki.update_card_tags = AsyncMock(side_effect=write)

    stats = await tagger.classify_local_all()

    assert stats.cards_scanned == 2
    assert stats.write_failures == 1
    assert stats.frequency_added == 1  # card 2 succeeded


@pytest.mark.asyncio
async def test_classify_local_all_raises_when_batch_already_running():
    cards = {1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d")}
    tagger, _, _ = _tagger_with_cards(cards)

    async def gate(card_id, tags):
        await _asyncio.sleep(
            0.01
        )  # hold the batch open so the second call sees the lock

    tagger._anki.update_card_tags = AsyncMock(side_effect=gate)

    task = _asyncio.create_task(tagger.classify_local_all())
    await _asyncio.sleep(0)  # let `task` enter the lock
    with pytest.raises(BatchAlreadyRunningError):
        await tagger.classify_local_all()
    await task


@pytest.mark.asyncio
async def test_classify_register_all_writes_register_for_missing_cards():
    cards = {
        1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d"),
        2: CardData(
            card_id=2, front="b", back="", tags=["sensei:formal"], deck_name="d"
        ),
    }
    classifier = MagicMock()
    classifier.register = AsyncMock(return_value="neutral")
    tagger, _, _ = _tagger_with_cards(cards, classifier)

    stats = await tagger.classify_register_all()

    assert isinstance(stats, RegisterBatchStats)
    assert stats.cards_scanned == 2
    assert stats.register_added == 1  # card 2 already had sensei:formal
    assert stats.register_failures == 0
    assert stats.write_failures == 0


@pytest.mark.asyncio
async def test_classify_register_all_counts_llm_failures_without_writing():
    cards = {1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d")}
    classifier = MagicMock()
    classifier.register = AsyncMock(return_value=None)
    tagger, anki, _ = _tagger_with_cards(cards, classifier)

    stats = await tagger.classify_register_all()

    assert stats.register_added == 0
    assert stats.register_failures == 1
    anki.update_card_tags.assert_not_awaited()


@pytest.mark.asyncio
async def test_classify_register_all_raises_when_batch_already_running():
    cards = {1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d")}
    classifier = MagicMock()

    async def slow_register(card):
        await _asyncio.sleep(0.01)
        return "neutral"

    classifier.register = AsyncMock(side_effect=slow_register)
    tagger, _, _ = _tagger_with_cards(cards, classifier)

    task = _asyncio.create_task(tagger.classify_register_all())
    await _asyncio.sleep(0)
    with pytest.raises(BatchAlreadyRunningError):
        await tagger.classify_register_all()
    await task


@pytest.mark.asyncio
async def test_local_and_register_batches_share_one_lock():
    """Starting local_all then immediately register_all raises — the lock is shared."""
    cards = {1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d")}
    tagger, anki, _ = _tagger_with_cards(cards)

    async def slow_write(card_id, tags):
        await _asyncio.sleep(0.01)

    anki.update_card_tags = AsyncMock(side_effect=slow_write)

    task = _asyncio.create_task(tagger.classify_local_all())
    await _asyncio.sleep(0)
    with pytest.raises(BatchAlreadyRunningError):
        await tagger.classify_register_all()
    await task


@pytest.mark.asyncio
async def test_classify_register_all_throttles_after_every_n_llm_calls(monkeypatch):
    """Avoid Gemini rate limits: sleep _REGISTER_BATCH_SLEEP_SECONDS after every
    _REGISTER_BATCH_SIZE actual LLM calls. Cached cards do NOT count toward
    the throttle, and no trailing sleep happens on the last card."""
    # 12 cards: first 4 already cached (sensei:formal), last 8 need LLM.
    cards = {}
    for cid in range(12):
        tags = ["sensei:formal"] if cid < 4 else []
        cards[cid] = CardData(
            card_id=cid, front=f"w{cid}", back="", tags=tags, deck_name="d"
        )
    tagger, _, _ = _tagger_with_cards(cards)

    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("src.word_service.card_tagger.asyncio.sleep", fake_sleep)

    stats = await tagger.classify_register_all()

    assert stats.register_added == 8  # 4 cached + 8 newly tagged
    # 8 LLM calls / 5 per batch → one pause after call 5; call 8 is last → no pause
    assert sleep_calls == [_REGISTER_BATCH_SLEEP_SECONDS]


@pytest.mark.asyncio
async def test_classify_register_all_no_throttle_when_all_cards_cached(monkeypatch):
    cards = {
        cid: CardData(
            card_id=cid, front=f"w{cid}", back="", tags=["sensei:formal"], deck_name="d"
        )
        for cid in range(_REGISTER_BATCH_SIZE * 3)
    }
    tagger, _, _ = _tagger_with_cards(cards)

    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("src.word_service.card_tagger.asyncio.sleep", fake_sleep)

    await tagger.classify_register_all()

    assert sleep_calls == []


@pytest.mark.asyncio
async def test_classify_local_all_passes_deck_to_anki(monkeypatch):
    cards = {1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d")}
    tagger, anki, _ = _tagger_with_cards(cards)

    await tagger.classify_local_all(deck="English")

    anki.get_all_card_ids.assert_awaited_once_with(deck="English")


@pytest.mark.asyncio
async def test_classify_register_all_passes_deck_to_anki(monkeypatch):
    cards = {1: CardData(card_id=1, front="a", back="", tags=[], deck_name="d")}
    tagger, anki, _ = _tagger_with_cards(cards)

    await tagger.classify_register_all(deck="English")

    anki.get_all_card_ids.assert_awaited_once_with(deck="English")
