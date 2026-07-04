"""
tests/test_feed.py — Mixtape

Tests for the "Friends Listening Now" feed logic.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def social_graph(app):
    """A user with one friend and one shared song to listen to."""
    with app.app_context():
        me = User(username="me", email="me@example.com")
        friend = User(username="friend", email="friend@example.com")
        db.session.add_all([me, friend])
        db.session.flush()

        db.session.execute(friendships.insert().values(user_id=me.id, friend_id=friend.id))
        db.session.execute(friendships.insert().values(user_id=friend.id, friend_id=me.id))

        song = Song(title="Test Track", artist="Test Artist", shared_by=me.id)
        db.session.add(song)
        db.session.commit()

        yield {"me_id": me.id, "friend_id": friend.id, "song_id": song.id}


def test_recent_listen_appears(app, social_graph):
    """A friend who listened 10 minutes ago shows up in 'listening now'."""
    with app.app_context():
        now = datetime.now(timezone.utc)
        db.session.add(ListeningEvent(
            user_id=social_graph["friend_id"],
            song_id=social_graph["song_id"],
            listened_at=now - timedelta(minutes=10),
        ))
        db.session.commit()

        feed = get_friends_listening_now(social_graph["me_id"])
        assert len(feed) == 1
        assert feed[0]["friend"]["username"] == "friend"


def test_yesterdays_listen_does_not_appear(app, social_graph):
    """
    A friend who listened several hours ago (i.e. yesterday) must NOT show up
    in 'listening now' — the feed is for people listening *right now*.
    """
    with app.app_context():
        now = datetime.now(timezone.utc)
        db.session.add(ListeningEvent(
            user_id=social_graph["friend_id"],
            song_id=social_graph["song_id"],
            listened_at=now - timedelta(hours=5),
        ))
        db.session.commit()

        feed = get_friends_listening_now(social_graph["me_id"])
        assert feed == []
