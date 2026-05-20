from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.anki.card_data import CardData
from src.tts.generator import (
    TTSGenerator,
    TtsBatchStats,  # noqa: F401  # smoke-tests public surface for Task 7
    TtsResult,
)
from src.word_service.card_tagger import (  # noqa: F401  # smoke-tests re-export for Task 7
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
