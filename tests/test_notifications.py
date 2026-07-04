"""
tests/test_notifications.py — Mixtape

Tests for notification creation when friends interact with a user's shared songs.
"""

import pytest
from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, add_to_playlist, get_notifications
from services.playlist_service import create_playlist


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def sharer_and_song(app):
    """A user who shared a song, plus a second user who can interact with it."""
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        friend = User(username="friend", email="friend@example.com")
        db.session.add_all([sharer, friend])
        db.session.flush()

        song = Song(title="Test Track", artist="Test Artist", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        yield {"sharer_id": sharer.id, "friend_id": friend.id, "song_id": song.id}


def test_rating_a_song_notifies_the_sharer(app, sharer_and_song):
    """
    When a friend rates a song, the person who shared it should get a
    'song_rated' notification — mirroring the playlist-add behavior.
    """
    with app.app_context():
        rate_song(sharer_and_song["friend_id"], sharer_and_song["song_id"], 5)

        notifs = get_notifications(sharer_and_song["sharer_id"])
        rated = [n for n in notifs if n["type"] == "song_rated"]
        assert len(rated) == 1
        assert "friend" in rated[0]["body"]


def test_rating_your_own_song_does_not_notify(app, sharer_and_song):
    """A user rating their own shared song should not notify themselves."""
    with app.app_context():
        rate_song(sharer_and_song["sharer_id"], sharer_and_song["song_id"], 4)

        notifs = get_notifications(sharer_and_song["sharer_id"])
        assert [n for n in notifs if n["type"] == "song_rated"] == []
