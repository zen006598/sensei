from typing import Callable

from wordfreq import zipf_frequency

from src.agent.gemini_agent import GeminiAgent
from src.anki.card_data import CardData
from src.word_service._awl_data import _AWL_WORDS


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
    """Pure word-classification services. No cache check, no I/O writes, no mutation of inputs.

    Three independent axes:
    - `frequency(word)`: local wordfreq lookup, returns common/rare/obsolete.
    - `is_academic(word)`: local Academic Word List membership.
    - `register(card)`: forwards to the Gemini agent; returns None on failure
      (the agent logs the exception).

    Cache reads/writes against Anki tags belong to the caller (QuizStateMachine).
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

    def is_academic(self, word: str) -> bool:
        """True if the word belongs to Coxhead's Academic Word List. Sub-ms, no I/O."""
        return word.lower() in _AWL_WORDS

    async def register(self, card: CardData) -> str | None:
        """Returns the formality register, or None on LLM failure (logged inside the agent)."""
        return await self._agent.classify_register(card)
