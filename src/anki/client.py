import asyncio
from contextlib import contextmanager

from anki.collection import Collection
from bs4 import BeautifulSoup

from src.quiz.models import CardData

_MODE_QUERIES = {
    "default": "is:due OR is:new",
    "due": "is:due",
    "new": "is:new",
}


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
        with self._get_collection() as col:
            return sorted(d["name"] for d in col.decks.all())

    def get_due_cards(
        self, limit: int = 20, deck: str | None = None, mode: str = "default"
    ) -> list[CardData]:
        with self._get_collection() as col:
            mode_query = _MODE_QUERIES.get(mode, _MODE_QUERIES["default"])
            if deck:
                query = f'deck:"{deck}" ({mode_query})'
            else:
                query = mode_query
            card_ids = col.find_cards(query)[:limit]
            cards = [self._card_to_data(col, cid) for cid in card_ids]
            return [c for c in cards if c.front or c.back]

    def answer_card(self, card_id: int, ease: int) -> None:
        with self._get_collection() as col:
            card = col.get_card(card_id)
            card.start_timer()
            col.sched.answerCard(card, ease)

    def update_card_tags(self, card_id: int, tags_to_add: list[str]) -> None:
        with self._get_collection() as col:
            card = col.get_card(card_id)
            note = card.note()
            for tag in tags_to_add:
                if tag not in note.tags:
                    note.tags.append(tag)
            col.update_note(note)

    def get_due_count(self) -> int:
        with self._get_collection() as col:
            return len(col.find_cards("is:due"))

    def _card_to_data(self, col, card_id: int) -> CardData:
        card = col.get_card(card_id)
        note = card.note()
        deck_name = col.decks.name(card.did)
        fields = note.fields
        front = _strip_html(fields[0]) if fields else ""
        back = _strip_html(fields[1]) if len(fields) > 1 else ""
        return CardData(
            card_id=card_id,
            front=front,
            back=back,
            tags=note.tags,
            deck_name=deck_name,
        )


def _strip_html(html: str) -> str:
    return BeautifulSoup(html, "lxml").get_text(separator=" ").strip()
