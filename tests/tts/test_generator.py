from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.anki.card_data import CardData
from src.tts.generator import (
    TTSGenerator,
    TtsBatchStats,
    TtsResult,
)
from src.word_service.card_tagger import (
    BatchAlreadyRunningError,
)


def _card(card_id: int = 1, front: str = "hello") -> CardData:
    return CardData(card_id=card_id, front=front, back="", tags=[], deck_name="d")


def _generator(
    sound_field: str = "",
    field_writer=None,
    media_dir: str = "/tmp/media",
):
    anki = MagicMock()
    anki.get_card_field = AsyncMock(return_value=sound_field)
    anki.set_card_field = AsyncMock(side_effect=field_writer)
    gen = TTSGenerator(
        anki=anki,
        piper_model_path="/dev/null/voice.onnx",
        media_dir=media_dir,
        voice_name="en_US-libritts-high",
    )
    return gen, anki


@pytest.mark.asyncio
async def test_generate_skips_when_sound_field_already_set():
    gen, anki = _generator(sound_field="[sound:user_pronounce.mp3]")
    result = await gen.generate(_card())

    assert result == TtsResult(generated=False, skipped=True, failed=False)
    anki.set_card_field.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_synthesises_writes_media_and_sets_field(tmp_path):
    gen, anki = _generator(media_dir=str(tmp_path))

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        return_value=b"FAKE_MP3_BYTES",
    ):
        result = await gen.generate(_card(card_id=42, front="hello"))

    assert result == TtsResult(generated=True, skipped=False, failed=False)
    expected_path = tmp_path / "sensei_42.mp3"
    assert expected_path.exists()
    assert expected_path.read_bytes() == b"FAKE_MP3_BYTES"
    anki.set_card_field.assert_awaited_once_with(42, "sound", "[sound:sensei_42.mp3]")


@pytest.mark.asyncio
async def test_generate_reports_failure_when_synthesis_raises(tmp_path):
    gen, anki = _generator(media_dir=str(tmp_path))

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        side_effect=RuntimeError("piper crashed"),
    ):
        result = await gen.generate(_card())

    assert result.generated is False
    assert result.failed is True
    anki.set_card_field.assert_not_awaited()
    assert not any(tmp_path.iterdir())  # no partial files left behind


@pytest.mark.asyncio
async def test_generate_reports_failure_when_field_write_raises(tmp_path):
    gen, anki = _generator(media_dir=str(tmp_path))
    anki.set_card_field = AsyncMock(side_effect=KeyError("sound"))

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        return_value=b"X",
    ):
        result = await gen.generate(_card())

    assert result.failed is True
    assert not any(tmp_path.iterdir()), "orphan MP3 should have been cleaned up"


def _generator_with_cards(
    card_map: dict[int, CardData],
    sound_fields: dict[int, str] | None = None,
    media_dir: str = "/tmp/media",
):
    sound_fields = sound_fields or {}
    anki = MagicMock()
    anki.get_all_card_ids = AsyncMock(return_value=list(card_map.keys()))
    anki.get_card = AsyncMock(side_effect=lambda cid: card_map[cid])
    anki.get_card_field = AsyncMock(
        side_effect=lambda cid, name: sound_fields.get(cid, "")
    )
    anki.set_card_field = AsyncMock()
    gen = TTSGenerator(
        anki=anki,
        piper_model_path="/dev/null/voice.onnx",
        media_dir=media_dir,
        voice_name="en_US-libritts-high",
    )
    return gen, anki


@pytest.mark.asyncio
async def test_generate_all_filters_by_deck_and_accumulates_stats(tmp_path):
    cards = {
        1: _card(card_id=1, front="alpha"),
        2: _card(card_id=2, front="beta"),
        3: _card(card_id=3, front="gamma"),
    }
    sounds = {2: "[sound:user_2.mp3]"}  # card 2 pre-tagged
    gen, anki = _generator_with_cards(
        cards, sound_fields=sounds, media_dir=str(tmp_path)
    )

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        return_value=b"BYTES",
    ):
        stats = await gen.generate_all(deck="English")

    assert isinstance(stats, TtsBatchStats)
    assert stats.cards_scanned == 3
    assert stats.generated == 2  # 1 and 3
    assert stats.skipped == 1  # 2
    assert stats.tts_failures == 0
    assert stats.write_failures == 0
    anki.get_all_card_ids.assert_awaited_once_with(deck="English")


@pytest.mark.asyncio
async def test_generate_all_swallows_per_card_failures(tmp_path):
    cards = {1: _card(card_id=1), 2: _card(card_id=2)}
    gen, _ = _generator_with_cards(cards, media_dir=str(tmp_path))

    call_count = {"n": 0}

    def fake_synth(model, voice, text):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("piper crashed")
        return b"OK"

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        side_effect=fake_synth,
    ):
        stats = await gen.generate_all(deck="English")

    assert stats.cards_scanned == 2
    assert stats.generated == 1
    assert stats.tts_failures == 1


@pytest.mark.asyncio
async def test_generate_all_raises_when_batch_already_running(tmp_path):
    cards = {1: _card(card_id=1)}
    gen, _ = _generator_with_cards(cards, media_dir=str(tmp_path))

    # Hold the lock via an explicit acquire to simulate "another batch running"
    await gen._tts_lock.acquire()
    try:
        with pytest.raises(BatchAlreadyRunningError):
            await gen.generate_all(deck="English")
    finally:
        gen._tts_lock.release()
