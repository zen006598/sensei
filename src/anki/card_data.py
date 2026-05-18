from dataclasses import dataclass


@dataclass
class CardData:
    card_id: int
    front: str
    back: str
    tags: list[str]
    deck_name: str
