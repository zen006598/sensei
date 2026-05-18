from src.agent.gemini_agent import GeminiAgent
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
    """Classifies frequency/register for a card, caching the result as a sensei: tag on the Anki note."""

    def __init__(self, agent: GeminiAgent, anki: AnkiClient):
        self._agent = agent
        self._anki = anki

    async def frequency(self, card: CardData) -> str:
        return await self._classify(
            card, _FREQ_TAGS, self._agent.classify_word_frequency
        )

    async def register(self, card: CardData) -> str:
        return await self._classify(
            card, _REGISTER_TAGS, self._agent.classify_word_register
        )

    async def _classify(self, card: CardData, known_tags: set[str], classify_fn) -> str:
        existing = {t for t in card.tags if t in known_tags}
        if existing:
            return existing.pop().removeprefix("sensei:")
        value = await classify_fn(card)
        tag = f"sensei:{value}"
        await self._anki.update_card_tags(card.card_id, [tag])
        card.tags.append(tag)
        return value
