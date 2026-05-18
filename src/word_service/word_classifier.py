from typing import Callable

from wordfreq import zipf_frequency

from src.agent.gemini_agent import GeminiAgent
from src.anki.card_data import CardData

TAG_PREFIX = "sensei:"
_REGISTERS = {"formal", "informal", "slang", "literary", "neutral"}


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


class WordClassifier:
    """Pure word classification — no I/O writes, no mutation of inputs.

    `frequency` is a local wordfreq lookup. `register` checks the card's
    existing sensei: tags first; on miss it calls Gemini and returns the
    result without persisting. The caller is responsible for writing the
    cache tag back to Anki.
    """

    def __init__(
        self,
        agent: GeminiAgent,
        zipf_fn: Callable[[str, str], float] = zipf_frequency,
    ):
        self._agent = agent
        self._zipf_fn = zipf_fn

    def frequency(self, word: str) -> str:
        """Returns 'common' | 'rare' | 'obsolete'. Sub-ms, no I/O."""
        return _bucket(self._zipf_fn(word.lower(), "en"))

    async def register(self, card: CardData) -> str:
        """Returns the formality register. Reads cached value from card.tags if present, otherwise calls Gemini. Does NOT persist."""
        for tag in card.tags:
            if tag.startswith(TAG_PREFIX):
                value = tag.removeprefix(TAG_PREFIX)
                if value in _REGISTERS:
                    return value
        return await self._agent.classify_register(card)
