from src.agent.gemini_agent import GeminiAgent
from src.agent.tools import WordClassification
from src.anki.client import AnkiClient
from src.quiz.models import CardData

_FREQ_TAGS = {"sensei:common", "sensei:rare", "sensei:obsolete"}
_REGISTER_TAGS = {
    "sensei:formal",
    "sensei:informal",
    "sensei:slang",
    "sensei:literary",
    "sensei:neutral",
}


class WordClassifier:
    """Classifies frequency + register for a card in one Gemini call, caching the result as sensei: tags on the Anki note."""

    def __init__(self, agent: GeminiAgent, anki: AnkiClient):
        self._agent = agent
        self._anki = anki

    async def classify(self, card: CardData) -> WordClassification:
        cached_freq = self._cached(card, _FREQ_TAGS)
        cached_reg = self._cached(card, _REGISTER_TAGS)
        if cached_freq and cached_reg:
            return WordClassification(frequency=cached_freq, register=cached_reg)

        fresh = await self._agent.classify_word(card)
        frequency = cached_freq or fresh.frequency
        register = cached_reg or fresh.register

        new_tags = []
        if not cached_freq:
            new_tags.append(f"sensei:{frequency}")
        if not cached_reg:
            new_tags.append(f"sensei:{register}")
        if new_tags:
            await self._anki.update_card_tags(card.card_id, new_tags)
            card.tags.extend(new_tags)

        return WordClassification(frequency=frequency, register=register)

    @staticmethod
    def _cached(card: CardData, known_tags: set[str]) -> str | None:
        return next(
            (t.removeprefix("sensei:") for t in card.tags if t in known_tags), None
        )
