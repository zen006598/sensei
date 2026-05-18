import os
import tempfile

from sqlmodel import SQLModel, create_engine

from src.db.user_prefs_store import UserPrefsStore


def _store():
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{tmp}")
    SQLModel.metadata.create_all(engine)
    return UserPrefsStore(engine), tmp


def test_get_deck_returns_none_for_unknown_user():
    store, tmp = _store()
    assert store.get_deck(12345) is None
    os.unlink(tmp)


def test_set_and_get_deck():
    store, tmp = _store()
    store.set_deck(1, "Japanese::N5")
    assert store.get_deck(1) == "Japanese::N5"
    os.unlink(tmp)


def test_set_deck_none_clears_selection():
    store, tmp = _store()
    store.set_deck(1, "Japanese::N5")
    store.set_deck(1, None)
    assert store.get_deck(1) is None
    os.unlink(tmp)


def test_set_deck_overwrites_previous():
    store, tmp = _store()
    store.set_deck(1, "Deck A")
    store.set_deck(1, "Deck B")
    assert store.get_deck(1) == "Deck B"
    os.unlink(tmp)


def test_multiple_users_independent():
    store, tmp = _store()
    store.set_deck(1, "Deck A")
    store.set_deck(2, "Deck B")
    assert store.get_deck(1) == "Deck A"
    assert store.get_deck(2) == "Deck B"
    os.unlink(tmp)


def test_get_mode_returns_default_for_unknown_user():
    store, tmp = _store()
    assert store.get_mode(12345) == "default"
    os.unlink(tmp)


def test_set_and_get_mode():
    store, tmp = _store()
    store.set_mode(1, "due")
    assert store.get_mode(1) == "due"
    os.unlink(tmp)
