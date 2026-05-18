from typing import Callable

from wordfreq import zipf_frequency


def _bucket(zipf: float) -> str:
    """Map a Zipf score to common/rare/obsolete.

    Zipf reference: 7+ function words, 4-6 everyday, 2-4 specialised, <2 archaic, 0 unknown to corpus.
    Unknown words (phrases, proper nouns, neologisms) default to 'common' to avoid penalising them.
    """
    if zipf == 0.0:
        return "common"
    if zipf >= 4.0:
        return "common"
    if zipf >= 2.5:
        return "rare"
    return "obsolete"


class FrequencyClassifier:
    """Local, deterministic word-frequency lookup via wordfreq. No LLM, no I/O, sub-ms per call."""

    def __init__(self, zipf_fn: Callable[[str, str], float] = zipf_frequency):
        self._zipf_fn = zipf_fn

    def classify(self, word: str) -> str:
        """Returns 'common' | 'rare' | 'obsolete'."""
        return _bucket(self._zipf_fn(word.lower(), "en"))
