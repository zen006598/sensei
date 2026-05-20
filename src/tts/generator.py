"""Piper TTS pronunciation generation.

`TTSGenerator` owns Piper invocation + per-card audio I/O. Two callers:

- Quiz-time `/tts` Telegram command (per-user trigger)
- Daily JobQueue at 04:00 (background trigger)

Both go through `generate_all(deck)`, which uses an instance-scoped
`_tts_lock` to serialize batches. The per-card `generate()` method is
also available for one-off use and does NOT touch the lock.

See docs/superpowers/specs/2026-05-19-tts-pronunciation-design.md."""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from src.anki.card_data import CardData
from src.anki.client import AnkiClient
from src.word_service.card_tagger import BatchAlreadyRunningError

logger = logging.getLogger(__name__)


@dataclass
class TtsResult:
    generated: bool  # MP3 newly written
    skipped: bool  # sound field already non-empty
    failed: bool  # piper / media / field write failed
    failure_reason: str | None = None  # "synth" | "media_write" | "field_write" | None


@dataclass
class TtsBatchStats:
    cards_scanned: int = 0
    generated: int = 0
    skipped: int = 0
    tts_failures: int = 0  # piper synth failed
    write_failures: int = 0  # media or field write failed


def _synthesize_to_mp3_bytes(model_path: str, voice_name: str, text: str) -> bytes:
    """Production seam between TTSGenerator and the actual TTS toolchain.

    Synthesises `text` via Piper, encodes the PCM stream as MP3, returns bytes.
    Tests monkey-patch this function, so its signature is the stable contract.

    The body below targets piper-tts 1.x + lameenc. If the installed piper-tts
    has a different API (the package has churned across versions), adjust this
    body — tests don't depend on it."""
    from piper import PiperVoice  # type: ignore[import-untyped]
    import lameenc  # type: ignore[import-untyped]

    voice = PiperVoice.load(model_path)
    pcm = b"".join(chunk.audio_int16_bytes for chunk in voice.synthesize(text))
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(96)
    encoder.set_in_sample_rate(voice.config.sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(2)
    return encoder.encode(pcm) + encoder.flush()


class TTSGenerator:
    def __init__(
        self,
        anki: AnkiClient,
        piper_model_path: str,
        voice_name: str = "en_US-libritts-high",
        sound_field: str = "Sound",
    ):
        self._anki = anki
        self._model_path = piper_model_path
        self._voice_name = voice_name
        self._sound_field = sound_field
        self._tts_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._tts_lock.locked()

    def ensure_voice_available(self) -> None:
        """Download the Piper voice (model + config) into the model directory
        if it isn't already there. Idempotent — piper's downloader skips files
        that already exist and are non-empty, so this is a no-op on warm volumes.

        Best-effort: a download failure is logged and swallowed rather than
        crashing the bot at boot. The bot still serves quizzes without TTS, and
        per-card synthesis already fails gracefully if the voice never lands."""
        from piper.download_voices import download_voice  # type: ignore[import-untyped]

        model_dir = Path(self._model_path).parent
        try:
            model_dir.mkdir(parents=True, exist_ok=True)
            download_voice(self._voice_name, model_dir)
        except Exception:
            logger.warning(
                "piper voice %r could not be ensured at %s; TTS stays unavailable "
                "until the model is present",
                self._voice_name,
                model_dir,
                exc_info=True,
            )

    async def generate(self, card: CardData) -> TtsResult:
        """Per-card. Idempotent: skips if `sound` field is non-empty."""
        existing = (
            await self._anki.get_card_field(card.card_id, self._sound_field)
        ).strip()
        if existing:
            return TtsResult(generated=False, skipped=True, failed=False)

        try:
            mp3_bytes = await asyncio.to_thread(
                _synthesize_to_mp3_bytes,
                self._model_path,
                self._voice_name,
                card.front,
            )
        except Exception:
            logger.warning(
                "tts synthesis failed for card %s (front=%r)",
                card.card_id,
                card.front,
                exc_info=True,
            )
            return TtsResult(
                generated=False, skipped=False, failed=True, failure_reason="synth"
            )

        try:
            filename = await self._anki.add_media(
                f"sensei_{card.card_id}.mp3", mp3_bytes
            )
        except Exception:
            logger.warning(
                "tts media write failed for card %s", card.card_id, exc_info=True
            )
            return TtsResult(
                generated=False,
                skipped=False,
                failed=True,
                failure_reason="media_write",
            )

        try:
            await self._anki.set_card_field(
                card.card_id, self._sound_field, f"[sound:{filename}]"
            )
        except Exception:
            logger.warning(
                "tts field write failed for card %s", card.card_id, exc_info=True
            )
            try:
                await self._anki.trash_media([filename])
            except Exception:
                pass
            return TtsResult(
                generated=False,
                skipped=False,
                failed=True,
                failure_reason="field_write",
            )

        return TtsResult(generated=True, skipped=False, failed=False)

    async def generate_all(self, deck: str) -> TtsBatchStats:
        """Iterate every card in `deck`, ensure sound field is set.
        Raises BatchAlreadyRunningError if another batch is in flight."""
        if self._tts_lock.locked():
            raise BatchAlreadyRunningError()
        stats = TtsBatchStats()
        async with self._tts_lock:
            card_ids = await self._anki.get_all_card_ids(deck=deck)
            stats.cards_scanned = len(card_ids)
            for card_id in card_ids:
                try:
                    card = await self._anki.get_card(card_id)
                    result = await self.generate(card)
                except Exception:
                    logger.warning(
                        "generate_all: card %s failed before generate; continuing",
                        card_id,
                        exc_info=True,
                    )
                    stats.write_failures += 1
                    continue
                if result.generated:
                    stats.generated += 1
                if result.skipped:
                    stats.skipped += 1
                if result.failed:
                    if result.failure_reason == "synth":
                        stats.tts_failures += 1
                    else:
                        # "media_write" or "field_write" — both are post-synth
                        # write failures per the spec table.
                        stats.write_failures += 1
        return stats
