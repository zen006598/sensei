from src.agent.gemini_agent import GeminiAgent
from src.anki.client import AnkiClient
from src.quiz.models import CardData

_REGISTER_TAGS = {
    "sensei:formal",
    "sensei:informal",
    "sensei:slang",
    "sensei:literary",
    "sensei:neutral",
}


class RegisterClassifier:
    """Classifies a card's formality register via Gemini, caching the result as a sensei: tag on the Anki note."""

    def __init__(self, agent: GeminiAgent, anki: AnkiClient):
        self._agent = agent
        self._anki = anki

    async def classify(self, card: CardData) -> str:
        cached = next(
            (t.removeprefix("sensei:") for t in card.tags if t in _REGISTER_TAGS),
            None,
        )
        if cached:
            return cached

        register = await self._agent.classify_register(card)
        tag = f"sensei:{register}"
        await self._anki.update_card_tags(card.card_id, [tag])
        card.tags.append(tag)
        return register
