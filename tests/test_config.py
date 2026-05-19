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
