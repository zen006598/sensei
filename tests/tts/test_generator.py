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
    anki.set_card_field.assert_awaited_once_with(42, "Sound", "[sound:sensei_42.mp3]")


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
    assert result.failure_reason == "synth"
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
    assert result.failure_reason == "field_write"
    assert not any(tmp_path.iterdir()), "orphan MP3 should have been cleaned up"


@pytest.mark.asyncio
async def test_generate_reports_failure_when_media_write_raises(monkeypatch, tmp_path):
    """Disk write failure routes through 'media_write' reason."""
    gen, _ = _generator(media_dir=str(tmp_path))

    def broken_write_bytes(self, data):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.write_bytes", broken_write_bytes)

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        return_value=b"X",
    ):
        result = await gen.generate(_card())

    assert result.failed is True
    assert result.failure_reason == "media_write"


@pytest.mark.asyncio
async def test_generate_uses_configured_sound_field_name(tmp_path):
    """The Anki note's audio field name is configurable (note types vary, e.g.
    'Sound' vs 'Audio'); generate() must read and write that field, not a
    hardcoded one."""
    anki = MagicMock()
    anki.get_card_field = AsyncMock(return_value="")
    anki.set_card_field = AsyncMock()
    gen = TTSGenerator(
        anki=anki,
        piper_model_path="/dev/null/voice.onnx",
        media_dir=str(tmp_path),
        sound_field="Audio",
    )

    with patch("src.tts.generator._synthesize_to_mp3_bytes", return_value=b"X"):
        await gen.generate(_card(card_id=7))

    anki.get_card_field.assert_awaited_once_with(7, "Audio")
    anki.set_card_field.assert_awaited_once_with(7, "Audio", "[sound:sensei_7.mp3]")


@pytest.mark.asyncio
async def test_ensure_voice_available_downloads_into_model_dir(tmp_path):
    gen, _ = _generator()
    gen._model_path = str(tmp_path / "voices" / "en_US-libritts-high.onnx")

    with patch("piper.download_voices.download_voice") as download:
        gen.ensure_voice_available()

    download.assert_called_once_with("en_US-libritts-high", tmp_path / "voices")
    assert (tmp_path / "voices").is_dir()  # model dir created for the download


@pytest.mark.asyncio
async def test_ensure_voice_available_swallows_download_failure(tmp_path):
    gen, _ = _generator()
    gen._model_path = str(tmp_path / "en_US-libritts-high.onnx")

    with patch(
        "piper.download_voices.download_voice",
        side_effect=OSError("network down"),
    ):
        gen.ensure_voice_available()  # must not raise — best-effort at boot


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


@pytest.mark.asyncio
async def test_generate_all_counts_get_card_exception_as_write_failure(tmp_path):
    """If get_card itself raises (e.g. card deleted between get_all_card_ids and
    get_card), the failure is captured as write_failures in the outer try/except,
    not tts_failures."""
    cards = {1: _card(card_id=1)}
    gen, anki = _generator_with_cards(cards, media_dir=str(tmp_path))
    anki.get_card = AsyncMock(side_effect=RuntimeError("card vanished"))

    stats = await gen.generate_all(deck="English")

    assert stats.cards_scanned == 1
    assert stats.write_failures == 1
    assert stats.tts_failures == 0
    assert stats.generated == 0


@pytest.mark.asyncio
async def test_generate_all_field_write_failure_counted_as_write_failure(tmp_path):
    """Field-write failures inside generate() should land in write_failures
    (not tts_failures) per the spec."""
    cards = {1: _card(card_id=1)}
    gen, anki = _generator_with_cards(cards, media_dir=str(tmp_path))
    anki.set_card_field = AsyncMock(side_effect=KeyError("sound"))

    with patch(
        "src.tts.generator._synthesize_to_mp3_bytes",
        return_value=b"X",
    ):
        stats = await gen.generate_all(deck="English")

    assert stats.write_failures == 1
    assert stats.tts_failures == 0
