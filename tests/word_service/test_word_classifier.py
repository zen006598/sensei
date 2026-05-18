from unittest.mock import MagicMock

from src.agent.gemini_agent import GeminiAgent
from src.word_service.word_classifier import WordClassifier


def _classifier() -> WordClassifier:
    return WordClassifier(MagicMock(spec=GeminiAgent), zipf_fn=lambda w, lang: 5.5)


def test_is_academic_headword_hit():
    assert _classifier().is_academic("analysis") is True


def test_is_academic_family_member_hit():
    # awlify expands word families; inflected forms must also resolve to True.
    assert _classifier().is_academic("abandoned") is True


def test_is_academic_case_insensitive():
    assert _classifier().is_academic("ACCESS") is True


def test_is_academic_non_awl_word():
    assert _classifier().is_academic("dog") is False


def test_is_academic_empty_string():
    assert _classifier().is_academic("") is False
