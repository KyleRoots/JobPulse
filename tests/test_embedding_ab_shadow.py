"""Tests for the May 2026 Embedding Model A/B shadow infrastructure (S3 Phase A).

Covers:
  - _shadow_enabled() env-var gating (off by default, on via truthy values)
  - _pick_shadow_model() pairing (large→small, small→large)
  - filter_relevant_jobs() shadow-mode behavior:
      * fail-soft when shadow resume embedding fails
      * fail-soft when a single shadow job embedding fails
      * batch-writes EmbeddingABLog rows when shadow succeeds
      * production gate decision is identical regardless of shadow outcome
  - _build_threshold_sweep() concordance math + recommendation pick
  - _pearson() edge cases
"""
import os
import unittest
from unittest.mock import patch, MagicMock

from app import app, db
from embedding_service import EmbeddingService


class TestShadowToggleAndPairing(unittest.TestCase):
    """Pure unit tests — no DB, no app context required."""

    def test_shadow_off_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('EMBEDDING_AB_SHADOW_ENABLED', None)
            self.assertFalse(EmbeddingService._shadow_enabled())

    def test_shadow_on_via_truthy_values(self):
        for val in ('true', 'True', '1', 'yes', 'YES'):
            with patch.dict(os.environ, {'EMBEDDING_AB_SHADOW_ENABLED': val}):
                self.assertTrue(EmbeddingService._shadow_enabled(), f"value={val!r}")

    def test_shadow_off_via_falsy_values(self):
        for val in ('false', '0', 'no', '', 'random'):
            with patch.dict(os.environ, {'EMBEDDING_AB_SHADOW_ENABLED': val}):
                self.assertFalse(EmbeddingService._shadow_enabled(), f"value={val!r}")

    def test_pick_shadow_large_yields_small(self):
        self.assertEqual(
            EmbeddingService._pick_shadow_model('text-embedding-3-large'),
            'text-embedding-3-small',
        )

    def test_pick_shadow_small_yields_large(self):
        self.assertEqual(
            EmbeddingService._pick_shadow_model('text-embedding-3-small'),
            'text-embedding-3-large',
        )

    def test_pick_shadow_unknown_yields_large(self):
        # Anything without "large" in the name pairs with -large for regression watch.
        self.assertEqual(
            EmbeddingService._pick_shadow_model('text-embedding-ada-002'),
            'text-embedding-3-large',
        )

    def test_pick_shadow_handles_none(self):
        self.assertEqual(
            EmbeddingService._pick_shadow_model(None),
            'text-embedding-3-large',
        )


class TestShadowFilterIntegration(unittest.TestCase):
    """Tests for filter_relevant_jobs shadow path. Uses in-process app + DB."""

    @classmethod
    def setUpClass(cls):
        cls.app_ctx = app.app_context()
        cls.app_ctx.push()
        # Make sure schema exists (covers fresh test DB)
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        try:
            db.session.remove()
        except Exception:
            pass
        cls.app_ctx.pop()

    def setUp(self):
        from models import EmbeddingABLog
        # Clean slate per test
        try:
            db.session.query(EmbeddingABLog).delete()
            db.session.commit()
        except Exception:
            db.session.rollback()

    def _build_service_with_mock_client(self, shadow_resume_emb=None,
                                         shadow_job_emb_factory=None):
        """Construct an EmbeddingService where the OpenAI client is mocked.

        - generate_embedding() will return a fixed primary resume embedding
          and primary job embedding through the normal flow (we patch the
          openai_client.embeddings.create method).
        - shadow_resume_emb: vector to return for the shadow resume call (or None to fail)
        - shadow_job_emb_factory: callable(job_id) → vector or None
        """
        svc = EmbeddingService()
        svc.openai_client = MagicMock()
        return svc

    def test_shadow_off_writes_no_ab_rows(self):
        from models import EmbeddingABLog
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('EMBEDDING_AB_SHADOW_ENABLED', None)
            svc = EmbeddingService()
            # Stub out the heavy methods so we exercise the shadow branch only
            with patch.object(svc, 'generate_embedding', return_value=[0.1] * 8), \
                 patch.object(svc, 'get_job_embedding', return_value=[0.1] * 8), \
                 patch.object(svc, 'is_filter_enabled', return_value=True), \
                 patch.object(svc, 'get_similarity_threshold', return_value=0.25), \
                 patch.object(svc, '_save_filter_logs'), \
                 patch.object(svc, '_save_ab_log_batch') as save_ab:
                jobs = [{'id': 1, 'title': 'Eng', 'description': 'desc'}]
                svc.filter_relevant_jobs('resume', jobs, {'id': 99, 'name': 'X'}, 1)
                save_ab.assert_not_called()
                # And no rows in table
                self.assertEqual(db.session.query(EmbeddingABLog).count(), 0)

    def test_shadow_on_failsoft_when_shadow_resume_fails(self):
        """If shadow resume embedding fails, no shadow comparisons are attempted
        but production decision is still made."""
        from models import EmbeddingABLog
        with patch.dict(os.environ, {'EMBEDDING_AB_SHADOW_ENABLED': 'true'}):
            svc = EmbeddingService()
            with patch.object(svc, 'generate_embedding', return_value=[1.0] + [0.0] * 7), \
                 patch.object(svc, 'get_job_embedding', return_value=[1.0] + [0.0] * 7), \
                 patch.object(svc, 'is_filter_enabled', return_value=True), \
                 patch.object(svc, 'get_similarity_threshold', return_value=0.25), \
                 patch.object(svc, '_save_filter_logs'), \
                 patch.object(svc, '_generate_with_model', return_value=None) as gen_with:
                jobs = [{'id': 1, 'title': 'Eng', 'description': 'desc'}]
                relevant, filtered = svc.filter_relevant_jobs(
                    'resume', jobs, {'id': 99, 'name': 'X'}, 1
                )
                # Production path unaffected
                self.assertEqual(len(relevant), 1)
                self.assertEqual(filtered, 0)
                # Shadow resume call attempted exactly once, then bailed
                self.assertEqual(gen_with.call_count, 1)
                # No rows
                self.assertEqual(db.session.query(EmbeddingABLog).count(), 0)

    def test_shadow_on_writes_ab_rows_when_succeeds(self):
        """Happy path: both resume + job shadow embeddings succeed → AB rows written."""
        from models import EmbeddingABLog
        with patch.dict(os.environ, {'EMBEDDING_AB_SHADOW_ENABLED': 'true'}):
            svc = EmbeddingService()
            # Identical vectors → similarity = 1.0 for both
            vec = [1.0] + [0.0] * 7
            with patch.object(svc, 'generate_embedding', return_value=vec), \
                 patch.object(svc, 'get_job_embedding', return_value=vec), \
                 patch.object(svc, 'is_filter_enabled', return_value=True), \
                 patch.object(svc, 'get_similarity_threshold', return_value=0.25), \
                 patch.object(svc, '_save_filter_logs'), \
                 patch.object(svc, '_generate_with_model', return_value=vec):
                jobs = [
                    {'id': 1, 'title': 'Eng', 'description': 'desc1'},
                    {'id': 2, 'title': 'PM',  'description': 'desc2'},
                ]
                svc.filter_relevant_jobs('resume', jobs, {'id': 99, 'name': 'X'}, 42)
                logs = db.session.query(EmbeddingABLog).all()
                self.assertEqual(len(logs), 2)
                for l in logs:
                    self.assertEqual(l.bullhorn_candidate_id, 99)
                    self.assertAlmostEqual(l.primary_score, 1.0, places=4)
                    self.assertAlmostEqual(l.shadow_score, 1.0, places=4)
                    self.assertTrue(l.primary_passed)
                    self.assertTrue(l.shadow_would_pass)
                    self.assertEqual(l.threshold_used, 0.25)

    def test_shadow_on_per_job_failure_does_not_break_others(self):
        """If a single job's shadow embedding fails, others still get logged."""
        from models import EmbeddingABLog
        with patch.dict(os.environ, {'EMBEDDING_AB_SHADOW_ENABLED': 'true'}):
            svc = EmbeddingService()
            vec = [1.0] + [0.0] * 7

            # First call (shadow resume) returns vec; per-job calls: job1=None, job2=vec
            shadow_calls = {'n': 0}
            def shadow_side_effect(text, model, site_id):
                shadow_calls['n'] += 1
                if shadow_calls['n'] == 1:
                    return vec  # resume
                if shadow_calls['n'] == 2:
                    return None  # job1 fails
                return vec  # job2

            with patch.object(svc, 'generate_embedding', return_value=vec), \
                 patch.object(svc, 'get_job_embedding', return_value=vec), \
                 patch.object(svc, 'is_filter_enabled', return_value=True), \
                 patch.object(svc, 'get_similarity_threshold', return_value=0.25), \
                 patch.object(svc, '_save_filter_logs'), \
                 patch.object(svc, '_generate_with_model', side_effect=shadow_side_effect):
                jobs = [
                    {'id': 1, 'title': 'Fails', 'description': 'd1'},
                    {'id': 2, 'title': 'Works', 'description': 'd2'},
                ]
                svc.filter_relevant_jobs('resume', jobs, {'id': 7, 'name': 'Y'}, 100)
                logs = db.session.query(EmbeddingABLog).all()
                self.assertEqual(len(logs), 1)
                self.assertEqual(logs[0].bullhorn_job_id, 2)


class TestThresholdSweep(unittest.TestCase):
    def test_empty_returns_empty(self):
        from routes.ai_cost import _build_threshold_sweep
        self.assertEqual(_build_threshold_sweep([]), [])

    def test_perfect_concordance(self):
        from routes.ai_cost import _build_threshold_sweep
        # All pairs: primary passes (score 0.5, threshold 0.25), shadow score 0.5 too.
        rows = [{'primary_score': 0.5, 'shadow_score': 0.5, 'primary_passed': True}] * 10
        sweep = _build_threshold_sweep(rows)
        # At threshold 0.25 → shadow passes (0.5 >= 0.25) → matches primary → 100% concordance
        t25 = next(r for r in sweep if r['threshold'] == 0.25)
        self.assertEqual(t25['concordance_pct'], 100.0)
        self.assertEqual(t25['false_negative_pct'], 0.0)

    def test_recommendation_picks_low_fn(self):
        from routes.ai_cost import _build_threshold_sweep
        # Mix: some pairs primary passed at score 0.30, shadow scored 0.20 (would fail at high threshold)
        rows = [
            {'primary_score': 0.30, 'shadow_score': 0.20, 'primary_passed': True}
        ] * 10
        sweep = _build_threshold_sweep(rows)
        recs = [r for r in sweep if r['recommended']]
        self.assertEqual(len(recs), 1)
        # The recommended threshold should keep FN <= 2%, i.e. threshold <= 0.20
        self.assertLessEqual(recs[0]['threshold'], 0.20)

    def test_no_recommendation_when_no_threshold_qualifies(self):
        """If every threshold in the sweep produces FN > 2%, no row should be
        marked recommended — operators must conclude the small model is too
        divergent at any tested threshold."""
        from routes.ai_cost import _build_threshold_sweep
        # Primary passes at 0.30; shadow scores 0.05 (below every sweep threshold).
        # Every threshold ≥ 0.15 produces 100% FN.
        rows = [
            {'primary_score': 0.30, 'shadow_score': 0.05, 'primary_passed': True}
        ] * 10
        sweep = _build_threshold_sweep(rows)
        recs = [r for r in sweep if r['recommended']]
        self.assertEqual(len(recs), 0, "No threshold should be recommended when all FN > 2%")


class TestShadowJobCap(unittest.TestCase):
    """Verify EMBEDDING_AB_SHADOW_MAX_JOBS env-var cap on per-call shadow comparisons."""

    def test_default_cap_is_25(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('EMBEDDING_AB_SHADOW_MAX_JOBS', None)
            self.assertEqual(EmbeddingService._shadow_max_jobs(), 25)

    def test_zero_means_unlimited(self):
        with patch.dict(os.environ, {'EMBEDDING_AB_SHADOW_MAX_JOBS': '0'}):
            self.assertEqual(EmbeddingService._shadow_max_jobs(), 0)

    def test_invalid_falls_back_to_default(self):
        with patch.dict(os.environ, {'EMBEDDING_AB_SHADOW_MAX_JOBS': 'banana'}):
            self.assertEqual(EmbeddingService._shadow_max_jobs(), 25)

    def test_negative_falls_back_to_default(self):
        with patch.dict(os.environ, {'EMBEDDING_AB_SHADOW_MAX_JOBS': '-1'}):
            self.assertEqual(EmbeddingService._shadow_max_jobs(), 25)

    def test_cap_limits_shadow_writes(self):
        """When cap=2 and 5 jobs come in, only the first 2 produce AB rows."""
        from app import app, db
        from models import EmbeddingABLog
        with app.app_context():
            db.create_all()
            db.session.query(EmbeddingABLog).delete()
            db.session.commit()
            with patch.dict(os.environ, {
                'EMBEDDING_AB_SHADOW_ENABLED': 'true',
                'EMBEDDING_AB_SHADOW_MAX_JOBS': '2',
            }):
                svc = EmbeddingService()
                vec = [1.0] + [0.0] * 7
                with patch.object(svc, 'generate_embedding', return_value=vec), \
                     patch.object(svc, 'get_job_embedding', return_value=vec), \
                     patch.object(svc, 'is_filter_enabled', return_value=True), \
                     patch.object(svc, 'get_similarity_threshold', return_value=0.25), \
                     patch.object(svc, '_save_filter_logs'), \
                     patch.object(svc, '_generate_with_model', return_value=vec):
                    jobs = [
                        {'id': i, 'title': f'J{i}', 'description': f'd{i}'}
                        for i in range(1, 6)
                    ]
                    svc.filter_relevant_jobs('resume', jobs, {'id': 555, 'name': 'Capped'}, 9001)
                    rows = db.session.query(EmbeddingABLog).filter_by(bullhorn_candidate_id=555).all()
                    self.assertEqual(len(rows), 2)


class TestPearson(unittest.TestCase):
    def test_perfect_correlation(self):
        from routes.ai_cost import _pearson
        self.assertAlmostEqual(_pearson([1, 2, 3, 4], [2, 4, 6, 8]), 1.0, places=5)

    def test_no_correlation_constant(self):
        from routes.ai_cost import _pearson
        # When one series is constant, correlation is undefined → returns 0
        self.assertEqual(_pearson([1, 1, 1, 1], [1, 2, 3, 4]), 0.0)

    def test_too_few_points(self):
        from routes.ai_cost import _pearson
        self.assertEqual(_pearson([1.0], [1.0]), 0.0)
        self.assertEqual(_pearson([], []), 0.0)


if __name__ == '__main__':
    unittest.main()
