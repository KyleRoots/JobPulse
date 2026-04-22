"""Tests for inline-editable AI requirements on JobVettingRequirements.

Covers:
  - get_active_requirements() priority: edited → AI → legacy custom
  - has_recruiter_edits() flag
  - Legacy data migration (custom_requirements → edited_requirements)
"""
from datetime import datetime

import pytest


def test_priority_edited_over_ai_over_legacy(db_session):
    from extensions import db
    from models import JobVettingRequirements

    row = JobVettingRequirements(
        bullhorn_job_id=999001,
        ai_interpreted_requirements="AI baseline",
        custom_requirements="legacy custom",
        edited_requirements="recruiter edit wins",
    )
    db.session.add(row)
    db.session.flush()

    assert row.get_active_requirements() == "recruiter edit wins"
    assert row.has_recruiter_edits() is True

    row.edited_requirements = None
    assert row.get_active_requirements() == "AI baseline"
    assert row.has_recruiter_edits() is False

    row.ai_interpreted_requirements = None
    assert row.get_active_requirements() == "legacy custom"

    row.custom_requirements = None
    assert row.get_active_requirements() is None

    db.session.rollback()


def test_blank_edited_falls_through_to_ai(db_session):
    from extensions import db
    from models import JobVettingRequirements

    row = JobVettingRequirements(
        bullhorn_job_id=999002,
        ai_interpreted_requirements="AI text",
        edited_requirements="   ",  # whitespace-only counts as empty
    )
    db.session.add(row)
    db.session.flush()

    assert row.get_active_requirements() == "AI text"
    assert row.has_recruiter_edits() is False

    db.session.rollback()


def test_legacy_migration_collapses_custom_into_edited(app):
    """The seed migration should fold legacy custom_requirements into edited_requirements."""
    from extensions import db
    from models import JobVettingRequirements
    from seed_database import migrate_legacy_custom_requirements

    legacy_only = JobVettingRequirements(
        bullhorn_job_id=999003,
        custom_requirements="legacy override only",
        ai_interpreted_requirements=None,
    )
    legacy_with_ai = JobVettingRequirements(
        bullhorn_job_id=999004,
        ai_interpreted_requirements="AI baseline",
        custom_requirements="recruiter additions",
    )
    already_edited = JobVettingRequirements(
        bullhorn_job_id=999005,
        ai_interpreted_requirements="AI",
        custom_requirements="legacy",
        edited_requirements="already edited — leave alone",
        requirements_edited_by="someone@example.com",
    )
    db.session.add_all([legacy_only, legacy_with_ai, already_edited])
    db.session.commit()

    try:
        migrate_legacy_custom_requirements(db)

        db.session.refresh(legacy_only)
        db.session.refresh(legacy_with_ai)
        db.session.refresh(already_edited)

        # legacy-only: copied verbatim
        assert legacy_only.edited_requirements == "legacy override only"
        assert legacy_only.requirements_edited_by == "migrated"

        # legacy + AI: combined
        assert "AI baseline" in (legacy_with_ai.edited_requirements or "")
        assert "recruiter additions" in (legacy_with_ai.edited_requirements or "")
        assert legacy_with_ai.requirements_edited_by == "migrated"

        # already edited: untouched
        assert already_edited.edited_requirements == "already edited — leave alone"
        assert already_edited.requirements_edited_by == "someone@example.com"
    finally:
        for r in (legacy_only, legacy_with_ai, already_edited):
            db.session.delete(r)
        db.session.commit()
