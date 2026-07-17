"""Tests for Scout re-vet note banner presentation clarity.

Regression context: candidate 4671592 (Bhavana Gangireddigari) — auditor
re-screen note mixed historical 92% tech-fit reasoning with a fresh 57%
Not Qualified result, making the current recommendation ambiguous.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from screening.note_builder import NoteBuilderMixin


class _BannerHarness(NoteBuilderMixin):
    """Minimal host for NoteBuilderMixin static/instance helpers."""
    pass


class TestThresholdDeltaPhrase:
    def test_exactly_at_threshold(self):
        phrase = NoteBuilderMixin._threshold_delta_phrase(80.0, 80.0)
        assert phrase == ' (exactly at the 80% threshold)'
        assert '0 points' not in phrase

    def test_just_below(self):
        phrase = NoteBuilderMixin._threshold_delta_phrase(78.0, 80.0)
        assert phrase == ' (just 2 points below the 80% threshold)'

    def test_above(self):
        phrase = NoteBuilderMixin._threshold_delta_phrase(84.0, 80.0)
        assert phrase == ' (4 points above the 80% threshold)'

    def test_near_threshold_counts_as_exact(self):
        phrase = NoteBuilderMixin._threshold_delta_phrase(80.2, 80.0)
        assert 'exactly at' in phrase


class TestScoreChangePhrase:
    def test_drop(self):
        phrase = NoteBuilderMixin._score_change_phrase(80.0, 57.0)
        assert phrase == 'Score change: 80% → 57% (-23 pts)'

    def test_rise(self):
        phrase = NoteBuilderMixin._score_change_phrase(69.0, 84.0)
        assert phrase == 'Score change: 69% → 84% (+15 pts)'

    def test_unchanged(self):
        phrase = NoteBuilderMixin._score_change_phrase(80.0, 80.0)
        assert phrase == 'Score change: 80% → 80% (unchanged)'

    def test_missing_returns_none(self):
        assert NoteBuilderMixin._score_change_phrase(None, 57.0) is None
        assert NoteBuilderMixin._score_change_phrase(80.0, None) is None


class TestTopMatchJob:
    def test_picks_highest_score(self):
        matches = [
            SimpleNamespace(bullhorn_job_id=1, job_title='A', match_score=40),
            SimpleNamespace(bullhorn_job_id=2, job_title='B', match_score=57),
            SimpleNamespace(bullhorn_job_id=3, job_title='C', match_score=None),
        ]
        job_id, title = NoteBuilderMixin._top_match_job(matches)
        assert job_id == 2
        assert title == 'B'

    def test_empty(self):
        assert NoteBuilderMixin._top_match_job([]) == (None, None)


class _CmpCol:
    """Stand-in for SQLAlchemy column ops used in filter()/order_by() expressions."""

    def __eq__(self, other):
        return self

    def __ge__(self, other):
        return self

    def __le__(self, other):
        return self

    def is_(self, other):
        return self

    def desc(self):
        return self

    def asc(self):
        return self


class TestBuildRevetBanner:
    def _audit_row(self, **overrides):
        base = dict(
            original_score=80.0,
            audit_finding=(
                "Inconsistency: tech=92% but final outcome is 'Not Qualified' "
                "at 80% due to a location gap."
            ),
            created_at=datetime(2026, 7, 17, 12, 28),
            job_id=35421,
            job_title='Senior Developer',
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def _patch_audit_query(self, row):
        query = MagicMock()
        query.filter.return_value.order_by.return_value.first.return_value = row
        MockAudit = MagicMock()
        MockAudit.query = query
        for attr in (
            'bullhorn_candidate_id',
            'job_id',
            'action_taken',
            'revet_new_score',
            'created_at',
        ):
            setattr(MockAudit, attr, _CmpCol())
        return patch('models.VettingAuditLog', MockAudit)

    def test_banner_separates_historical_from_current_and_shows_delta(self):
        harness = _BannerHarness()
        row = self._audit_row()

        with self._patch_audit_query(row):
            # VettingConfig import may fail in unit tests; banner falls back to 80%.
            lines = harness._build_revet_banner(
                4671592,
                35421,
                new_score=57.0,
                new_best_job_id=35261,
                new_best_job_title='Related Role',
            )

        text = '\n'.join(lines)
        assert 'WHY A SECOND LOOK HAPPENED (historical)' in text
        assert 'CURRENT SCOUT RECOMMENDATION' in text
        assert 'Historical auditor note (from before re-screen):' in text
        assert 'exactly at the 80% threshold' in text
        assert 'Score change: 80% → 57% (-23 pts)' in text
        assert 'Best job on original screen: #35421 — Senior Developer' in text
        assert 'Best job on re-screen: #35261 — Related Role' in text
        assert 'SELF-CORRECTION RE-EVALUATION' not in text
        assert 'Auditor reasoning:' not in text
        # Historical finding still present, but clearly labeled
        assert 'tech=92%' in text
        assert text.index('WHY A SECOND LOOK') < text.index('CURRENT SCOUT RECOMMENDATION')

    def test_same_best_job_omits_re_screen_job_line(self):
        harness = _BannerHarness()
        row = self._audit_row(job_id=35421, job_title='Same Role')

        with self._patch_audit_query(row):
            lines = harness._build_revet_banner(
                1,
                35421,
                new_score=57.0,
                new_best_job_id=35421,
                new_best_job_title='Same Role',
            )

        text = '\n'.join(lines)
        assert 'Best job on original screen: #35421' in text
        assert 'Best job on re-screen:' not in text

    def test_no_audit_row_returns_empty(self):
        harness = _BannerHarness()

        with self._patch_audit_query(None):
            assert harness._build_revet_banner(1, 2, new_score=50) == []

    def test_missing_ids_return_empty(self):
        harness = _BannerHarness()
        assert harness._build_revet_banner(None, 1) == []
        assert harness._build_revet_banner(1, None) == []
