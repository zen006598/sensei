from typing import Callable

from wordfreq import zipf_frequency

from src.agent.gemini_agent import GeminiAgent
from src.agent.schemas import WordClassification
from src.anki.client import AnkiClient
from src.quiz.models import CardData

_REGISTER_TAGS = {
    "sensei:formal",
    "sensei:informal",
    "sensei:slang",
    "sensei:literary",
    "sensei:neutral",
}


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
    """Frequency comes from wordfreq (local, sub-ms, deterministic). Register comes from Gemini and is cached as a sensei: tag on the Anki note."""

    def __init__(
        self,
        agent: GeminiAgent,
        anki: AnkiClient,
        zipf_fn: Callable[[str, str], float] = zipf_frequency,
    ):
        self._agent = agent
        self._anki = anki
        self._zipf_fn = zipf_fn

    async def classify(self, card: CardData) -> WordClassification:
        frequency = _bucket(self._zipf_fn(card.front.lower(), "en"))

        cached_register = next(
            (t.removeprefix("sensei:") for t in card.tags if t in _REGISTER_TAGS),
            None,
        )
        if cached_register:
            register = cached_register
        else:
            register = await self._agent.classify_register(card)
            tag = f"sensei:{register}"
            await self._anki.update_card_tags(card.card_id, [tag])
            card.tags.append(tag)

        return WordClassification(frequency=frequency, register=register)
