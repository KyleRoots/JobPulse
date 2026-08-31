"""Tests for Email note dedup (Outlook sync twin cleanup)."""
from __future__ import annotations

from services.email_note_dedup import find_duplicate_notes, EmailNoteDedupService


def _note(nid, author_id, comments, date_added, action="Email", first="Anita", last="Barker"):
    return {
        "id": nid,
        "action": action,
        "comments": comments,
        "dateAdded": date_added,
        "commentingPerson": {"id": author_id, "firstName": first, "lastName": last},
    }


class TestFindDuplicateNotes:
    def test_keeps_newest_deletes_older_twin(self):
        body = "Hello,\r\n\r\nI do hope you had a nice weekend!"
        notes = [
            _note(1, 10, body, 1000),
            _note(2, 10, body, 1000),  # same ms
        ]
        twins = find_duplicate_notes(notes, time_window_minutes=60)
        assert len(twins) == 1
        assert twins[0]["id"] == 1  # older / first when equal sort stability — actually same ts, sort stable so first is older kept as delete? group.sort by dateAdded, newest is last = note 2, delete note 1
        assert twins[0]["id"] == 1

    def test_ignores_non_email_actions(self):
        body = "same body"
        notes = [
            _note(1, 10, body, 1000, action="Scout Screen - Not Qualified"),
            _note(2, 10, body, 1000, action="Scout Screen - Not Qualified"),
            _note(3, 10, body, 2000, action="Email"),
            _note(4, 10, body, 2000, action="Email"),
        ]
        twins = find_duplicate_notes(notes)
        assert len(twins) == 1
        assert twins[0]["id"] == 3

    def test_ignores_different_authors(self):
        body = "Teams link"
        notes = [
            _note(1, 10, body, 1000, first="Anita"),
            _note(2, 11, body, 1000, first="Mohanned"),
        ]
        assert find_duplicate_notes(notes) == []

    def test_outside_window_kept(self):
        body = "follow up"
        # 2 hours apart
        notes = [
            _note(1, 10, body, 0),
            _note(2, 10, body, 2 * 60 * 60 * 1000),
        ]
        assert find_duplicate_notes(notes, time_window_minutes=60) == []


class TestEmailNoteDedupServiceDisabled:
    def test_disabled_skips(self):
        svc = EmailNoteDedupService(enabled=False)
        out = svc.run()
        assert out["enabled"] is False
        assert out["candidates_checked"] == 0
        assert "disabled" in (out.get("message") or "")
