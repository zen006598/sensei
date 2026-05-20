import pytest

from src.config import load_settings


@pytest.fixture
def base_env(monkeypatch):
    """Minimum env to satisfy load_settings' required-field check."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "fake")
    monkeypatch.setenv("ANKIWEB_EMAIL", "fake")
    monkeypatch.setenv("ANKIWEB_PASSWORD", "fake")
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    return monkeypatch


def test_scheduler_daily_hour_default_is_3(base_env):
    base_env.delenv("SCHEDULER_DAILY_HOUR", raising=False)
    s = load_settings()
    assert s.scheduler_daily_hour == 3


def test_scheduler_daily_hour_rejects_out_of_range(base_env):
    base_env.setenv("SCHEDULER_DAILY_HOUR", "24")
    with pytest.raises(ValueError, match="SCHEDULER_DAILY_HOUR"):
        load_settings()


def test_scheduler_daily_hour_accepts_zero(base_env):
    base_env.setenv("SCHEDULER_DAILY_HOUR", "0")
    s = load_settings()
    assert s.scheduler_daily_hour == 0


def test_scheduler_daily_hour_accepts_twenty_three(base_env):
    base_env.setenv("SCHEDULER_DAILY_HOUR", "23")
    s = load_settings()
    assert s.scheduler_daily_hour == 23


def test_tts_daily_hour_default_is_4(base_env):
    base_env.delenv("TTS_DAILY_HOUR", raising=False)
    s = load_settings()
    assert s.tts_daily_hour == 4


def test_tts_daily_hour_rejects_out_of_range(base_env):
    base_env.setenv("TTS_DAILY_HOUR", "24")
    with pytest.raises(ValueError, match="TTS_DAILY_HOUR"):
        load_settings()


def test_piper_voice_path_default(base_env):
    base_env.delenv("PIPER_VOICE_PATH", raising=False)
    s = load_settings()
    assert s.piper_voice_path == "/data/piper/en_US-libritts-high.onnx"


def test_piper_voice_default(base_env):
    base_env.delenv("PIPER_VOICE", raising=False)
    s = load_settings()
    assert s.piper_voice == "en_US-libritts-high"
