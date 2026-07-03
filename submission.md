# Project 5: Mixtape Bug Hunt — Submission

## AI Usage

I used AI (Claude) in two ways on this project:

- **Writing test cases** — I asked AI to help draft `pytest` test cases that reproduce
  each bug and confirm the fix, covering both the failing scenario and the boundary
  conditions around it (e.g. the Sunday streak edge case, the last-song-in-playlist case).
- **Explaining code** — I used AI to walk through unfamiliar parts of the codebase,
  trace the route → service call chains, and clarify how SQLAlchemy relationships and
  the association tables (`friendships`, `playlist_entries`, `song_tags`) behave.

All fixes and final decisions are my own; AI was used as an assistant for test scaffolding
and comprehension, not to make architectural choices for me.

---

## Codebase Map

Mixtape is a **Flask + SQLAlchemy** JSON API. There is no frontend — every feature is an
HTTP endpoint that returns JSON. The code is organized in three clear layers:

```
HTTP request → routes/ (blueprints) → services/ (business logic) → models.py (SQLAlchemy) → SQLite
```

### Main files and what each does

**Application core**

- [app.py](app.py) — The Flask **application factory** (`create_app`). Sets up the
  SQLAlchemy `db` object, reads config (database URL, secret key) from environment
  variables, registers the four route blueprints under their URL prefixes
  (`/songs`, `/playlists`, `/users`, `/feed`), and calls `db.create_all()`.
- [models.py](models.py) — All **SQLAlchemy models** and the many-to-many association
  tables. Entities: `User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`,
  `Notification`. Association tables: `friendships` (symmetric user↔user),
  `song_tags` (song↔tag), and `playlist_entries` (playlist↔song, carrying extra
  columns `position`, `added_by`, `added_at`). Every model has a `to_dict()` used for
  JSON serialization.
- [seed_data.py](seed_data.py) — Populates the database with test users, songs,
  playlists, and events so the endpoints have data to return.

**Routes (`routes/`)** — thin HTTP layer. Each file is a Flask blueprint that parses
the request, calls a service function, and serializes the result to JSON. Routes do
**not** contain business logic; they translate `ValueError` from services into 400/404
responses.

- [routes/songs.py](routes/songs.py) — `GET /songs/search`, `GET /songs/<id>`,
  `POST /songs/<id>/rate`, `POST /songs/<id>/listen`.
- [routes/playlists.py](routes/playlists.py) — `POST /playlists/`,
  `GET /playlists/<id>`, `GET /playlists/<id>/songs`, `POST /playlists/<id>/songs`.
- [routes/users.py](routes/users.py) — `GET /users/<id>`, `GET /users/<id>/streak`,
  `GET /users/<id>/notifications`, `POST /users/notifications/<id>/read`.
- [routes/feed.py](routes/feed.py) — `GET /feed/<id>/listening-now`,
  `GET /feed/<id>/activity`.

**Services (`services/`)** — where all the business logic (and all five bugs) live.

- [services/streak_service.py](services/streak_service.py) — Records listening events
  and updates a user's consecutive-day listening streak.
- [services/feed_service.py](services/feed_service.py) — Builds the "Friends Listening
  Now" feed (friends active within the last 24h, one entry per friend) and the general
  activity feed.
- [services/search_service.py](services/search_service.py) — Case-insensitive song
  search over title/artist, joining in tags.
- [services/notification_service.py](services/notification_service.py) — Creates and
  retrieves notifications; also owns `add_to_playlist` and `rate_song`, which are the
  two actions that generate notifications.
- [services/playlist_service.py](services/playlist_service.py) — Creates playlists and
  returns a playlist's songs in position order.

**Tests (`tests/`)** — `test_streaks.py`, `test_search.py`, `test_playlists.py`
(pytest).

### Data flow: adding a song to a playlist triggers a notification

This is the clearest example of the layered flow and how a notification gets created.

1. A client sends **`POST /playlists/<playlist_id>/songs`** with a JSON body
   `{ "song_id": ..., "added_by": ... }`.
2. [routes/playlists.py](routes/playlists.py) `add_song()` validates the body and calls
   `add_to_playlist(playlist_id, song_id, added_by)`.
3. [services/notification_service.py](services/notification_service.py) `add_to_playlist()`:
   - Loads the `Song`, the adding `User`, and the `Playlist` (raising `ValueError` if
     any is missing — which the route turns into a 400).
   - Appends the song to `playlist.songs` (writing a row into the `playlist_entries`
     association table) and commits.
   - Checks `song.shared_by != added_by_user_id` — i.e. _don't notify yourself_.
   - If it was a different user, calls `create_notification(...)` with type
     `"song_added_to_playlist"` and a message like
     _"Alice added your song 'X' to the playlist 'Y'."_
4. `create_notification()` inserts a `Notification` row addressed to the song's original
   sharer (`song.shared_by`) and commits.
5. Later, the sharer fetches **`GET /users/<id>/notifications`** →
   `get_notifications()` returns their notifications, newest first.

The `rate_song()` flow (`POST /songs/<id>/rate`) is meant to work the same way — a rating
should notify the song's sharer — which ties directly into Issue #4.

### Patterns I noticed

- **Strict route/service/model layering.** Routes never touch the database directly;
  they only call services. Services never build HTTP responses; they return plain data
  or raise `ValueError`. This makes bugs easy to localize: a broken endpoint is almost
  always a broken service function.
- **`ValueError` as the error channel.** Services signal "not found" / "bad input" by
  raising `ValueError`, and every route wraps service calls in `try/except ValueError`
  to emit the right status code. There's one consistent error-handling convention across
  the whole app.
- **`to_dict()` everywhere.** Serialization lives on the models, so services return
  lists/dicts and routes just `jsonify()` them.
- **UUID string primary keys** generated in Python (`generate_uuid`) rather than
  autoincrement integers.
- **Timezone-aware UTC timestamps** via `datetime.now(timezone.utc)`, though some stored
  values can come back naive — the streak logic explicitly re-attaches `timezone.utc`
  before comparing, which is exactly the kind of place edge-case bugs hide.
- **Association tables with payload.** `playlist_entries` carries `position`, `added_by`,
  and `added_at`, so playlist ordering is a property of the join row, not the song —
  relevant to the playlist ordering/last-song issue.

---

## Root Cause Analysis

### Issue #1 — My listening streak keeps resetting

**How I reproduced it**

Before touching any code, I ran the existing streak tests:

```bash
pytest tests/test_streaks.py -v
```

Four tests passed but one failed: `test_streak_increments_on_sunday`. That test listens on
Saturday (`2024-06-15`) and then Sunday (`2024-06-16`) and expects the streak to go from
1 → 2. Instead it stayed at 1:

```
E   assert 1 == 2
```

So the trigger condition is precise: a user listens on two consecutive calendar days where
**the second day is a Sunday**. On any other pair of consecutive days the streak increments
correctly, which is why the bug looks intermittent to users — it silently resets their
streak once a week.

**How I found the root cause**

The failing test pointed straight at [services/streak_service.py](services/streak_service.py),
specifically `update_listening_streak()`. I read the streak-decision block (lines 70–78) and
compared each branch against the docstring rules just above it. The docstring says the streak
should increment "if the user listened yesterday" — with no mention of any weekday exception.
The branch, however, read:

```python
elif days_since_last == 1 and today.weekday() != 6:
    user.listening_streak += 1
else:
    user.listening_streak = 1
```

The moment I saw `today.weekday() != 6` I was confident this was the exact cause, not just a
suspicious area: `weekday() == 6` is Python's value for Sunday, and the test that failed is
the one whose second day is a Sunday. The extra condition has no justification in the
docstring or anywhere else — it's a stray predicate that only affects Sundays.

**The root cause**

The "consecutive day" branch had an extra condition, `today.weekday() != 6`. When a user
listened yesterday **and today is Sunday**, `days_since_last == 1` was true but
`today.weekday() != 6` was false, so the `elif` was skipped and control fell through to the
`else` branch, which resets `listening_streak = 1`. In other words: any streak that carried
into a Sunday was wiped out instead of being incremented. It was not a date-math or timezone
problem — it was a spurious weekday check that only ever fired on Sundays.

**My fix and side-effect check**

I removed the weekday condition so the branch matches the documented rule — increment whenever
exactly one day has passed, regardless of which day of the week it is:

```python
elif days_since_last == 1:
    user.listening_streak += 1
```

This fixes the root cause because the increment branch now depends only on the day gap
(`days_since_last == 1`), which is the actual definition of a consecutive-day streak.

Side-effect check: I re-ran the full streak suite and all five tests pass, including the
same-day (no double count), skipped-day (reset to 1), and new-user (start at 1) cases — so
the fix didn't weaken the reset logic, it only removed the erroneous Sunday reset. I also ran
the whole test suite (`pytest tests/`); the only remaining failures are the two
`test_playlists.py` tests, which belong to the separate, still-open Issue #5 (last song in a
playlist) and are unrelated to this change.

---
