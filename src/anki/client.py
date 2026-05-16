import asyncio
from contextlib import contextmanager

from anki.collection import Collection
from bs4 import BeautifulSoup

from src.quiz.models import CardData


class AnkiClient:
    _collection_lock = asyncio.Lock()

    def __init__(self, collection_path: str):
        self._collection_path = collection_path

    @contextmanager
    def _get_collection(self):
        col = Collection(self._collection_path)
        try:
            yield col
        finally:
            col.close()

    def get_deck_names(self) -> list[str]:
        """Synchronous. Call via run_in_executor."""
        with self._get_collection() as col:
            return sorted(d["name"] for d in col.decks.all())

    def get_due_cards(self, limit: int = 20, deck: str | None = None) -> list[CardData]:
        """Synchronous. Call via run_in_executor."""
        with self._get_collection() as col:
            if deck:
                query = f'deck:"{deck}" (is:due OR is:new)'
            else:
                query = "is:due"
            card_ids = col.find_cards(query)[:limit]
            cards = [self._card_to_data(col, cid) for cid in card_ids]
            return [c for c in cards if c.front or c.back]

    def answer_card(self, card_id: int, ease: int) -> None:
        """Synchronous. Call via run_in_executor. ease: 1=Again, 2=Hard, 3=Good, 4=Easy."""
        with self._get_collection() as col:
            card = col.get_card(card_id)
            card.start_timer()
            col.sched.answerCard(card, ease)

    def get_due_count(self) -> int:
        """Synchronous. Call via run_in_executor."""
        with self._get_collection() as col:
            return len(col.find_cards("is:due"))

    def _card_to_data(self, col, card_id: int) -> CardData:
        card = col.get_card(card_id)
        note = card.note()
        deck_name = col.decks.name(card.did)
        fields = note.fields
        front = _strip_html(fields[0]) if fields else ""
        back = _strip_html(fields[1]) if len(fields) > 1 else ""
        tags = note.tags
        return CardData(
            card_id=card_id,
            front=front,
            back=back,
            tags=tags,
            deck_name=deck_name,
        )


def _strip_html(html: str) -> str:
    return BeautifulSoup(html, "lxml").get_text(separator=" ").strip()
