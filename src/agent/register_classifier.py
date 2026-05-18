from src.agent.gemini_agent import GeminiAgent
from src.anki.client import AnkiClient
from src.quiz.models import CardData

_TAG_PREFIX = "sensei:"
_REGISTERS = {"formal", "informal", "slang", "literary", "neutral"}


class RegisterClassifier:
    """Classifies a card's formality register via Gemini, caching the result as a sensei: tag on the Anki note."""

    def __init__(self, agent: GeminiAgent, anki: AnkiClient):
        self._agent = agent
        self._anki = anki

    async def classify(self, card: CardData) -> str:
        for tag in card.tags:
            if tag.startswith(_TAG_PREFIX):
                value = tag.removeprefix(_TAG_PREFIX)
                if value in _REGISTERS:
                    return value

        register = await self._agent.classify_register(card)
        tag = f"{_TAG_PREFIX}{register}"
        await self._anki.update_card_tags(card.card_id, [tag])
        card.tags.append(tag)
        return register
