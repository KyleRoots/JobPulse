"""
Regression tests for the AI requirements maintenance loop persistence bug.

Bug history (2026-05-11):
  `extract_job_requirements()` returns the AI text but does NOT persist it.
  Two callers in the maintenance loop —
    - JobManagementMixin.extract_requirements_for_jobs (batch new-job extraction)
    - JobManagementMixin.check_and_refresh_changed_jobs (modified-job re-extraction)
  must explicitly call `_save_ai_interpreted_requirements`. Without it, the row
  is never created/updated, the dashboard stays stuck on "Pending", and every
  5-min maintenance cycle re-burns OpenAI tokens for nothing.

Bug history (2026-07-29):
  `check_and_refresh_changed_jobs` re-extracted on every Bullhorn
  `dateLastModified` bump even when the JD text was unchanged (~450 gpt-5.4
  calls/day for 7 jobs). Gate on `source_description_hash` so metadata-only
  bumps skip AI.
"""
from unittest.mock import MagicMock, patch


class TestExtractRequirementsForJobsPersists:
    def test_save_is_called_when_extraction_succeeds(self):
        from screening.job_management import JobManagementMixin

        svc = MagicMock(spec=JobManagementMixin)
        svc.extract_job_requirements = MagicMock(return_value="• Python 5+ years\n• AWS")
        svc._save_ai_interpreted_requirements = MagicMock()

        with patch('screening.job_management.JobVettingRequirements') as MockReq:
            MockReq.query.filter.return_value.all.return_value = []

            jobs = [{
                'id': 34967,
                'title': 'Fullstack Application Developer',
                'description': 'A long enough description ' * 20,
                'location': 'Dallas, TX, US',
                'work_type': 'On-site',
            }]
            JobManagementMixin.extract_requirements_for_jobs(svc, jobs)

        assert svc._save_ai_interpreted_requirements.call_count == 1
        args, kwargs = svc._save_ai_interpreted_requirements.call_args
        assert args[0] == 34967
        assert args[1] == 'Fullstack Application Developer'
        assert args[2] == "• Python 5+ years\n• AWS"
        assert args[3] == 'Dallas, TX, US'
        assert args[4] == 'On-site'
        assert 'description_hash' in kwargs
        assert isinstance(kwargs['description_hash'], str)
        assert len(kwargs['description_hash']) == 64

    def test_save_is_not_called_when_extraction_returns_none(self):
        from screening.job_management import JobManagementMixin

        svc = MagicMock(spec=JobManagementMixin)
        svc.extract_job_requirements = MagicMock(return_value=None)
        svc._save_ai_interpreted_requirements = MagicMock()

        with patch('screening.job_management.JobVettingRequirements') as MockReq:
            MockReq.query.filter.return_value.all.return_value = []

            jobs = [{
                'id': 34968,
                'title': 'Software Developer',
                'description': 'A long enough description ' * 20,
                'location': '',
                'work_type': 'On-site',
            }]
            JobManagementMixin.extract_requirements_for_jobs(svc, jobs)

        svc._save_ai_interpreted_requirements.assert_not_called()

    def test_save_is_skipped_for_jobs_with_existing_ai_requirements(self):
        from screening.job_management import JobManagementMixin

        existing = MagicMock()
        existing.bullhorn_job_id = 35058
        existing.ai_interpreted_requirements = "• Existing reqs"

        svc = MagicMock(spec=JobManagementMixin)
        svc.extract_job_requirements = MagicMock()
        svc._save_ai_interpreted_requirements = MagicMock()

        with patch('screening.job_management.JobVettingRequirements') as MockReq:
            MockReq.query.filter.return_value.all.return_value = [existing]

            jobs = [{
                'id': 35058,
                'title': 'Azure Cloud Migration Engineer',
                'description': 'A long enough description ' * 20,
                'location': '',
                'work_type': 'On-site',
            }]
            result = JobManagementMixin.extract_requirements_for_jobs(svc, jobs)

        svc.extract_job_requirements.assert_not_called()
        svc._save_ai_interpreted_requirements.assert_not_called()
        assert result['skipped'] == 1


class TestCheckAndRefreshChangedJobsPersists:
    def _make_existing(self, last_interp, source_hash='oldhash'):
        existing = MagicMock()
        existing.bullhorn_job_id = 34967
        existing.last_ai_interpretation = last_interp
        existing.ai_interpreted_requirements = "• old reqs"
        existing.edited_requirements = None
        existing.source_description_hash = source_hash
        return existing

    def test_save_called_when_modified_job_re_extracts(self):
        from datetime import datetime, timedelta, timezone
        from screening.job_management import JobManagementMixin
        from embedding_service import EmbeddingService

        last_interp = datetime(2026, 5, 1, 0, 0, 0)
        modified_ms = int(
            (last_interp.replace(tzinfo=timezone.utc) + timedelta(hours=1)).timestamp() * 1000
        )
        existing = self._make_existing(last_interp, source_hash='different-old-hash')
        desc = 'A long enough description ' * 20

        svc = MagicMock(spec=JobManagementMixin)
        svc.extract_job_requirements = MagicMock(return_value="• Refreshed Python 5+\n• AWS")
        svc._save_ai_interpreted_requirements = MagicMock()

        with patch('screening.job_management.JobVettingRequirements') as MockReq:
            MockReq.query.filter_by.return_value.first.return_value = existing

            jobs = [{
                'id': 34967,
                'title': 'Fullstack Application Developer',
                'description': desc,
                'dateLastModified': modified_ms,
                'address': {'city': 'Dallas', 'state': 'TX', 'countryName': 'US'},
                'onSite': 1,
            }]
            JobManagementMixin.check_and_refresh_changed_jobs(svc, jobs)

        svc._save_ai_interpreted_requirements.assert_called_once_with(
            34967,
            'Fullstack Application Developer',
            "• Refreshed Python 5+\n• AWS",
            'Dallas, TX, US',
            'On-site',
            description_hash=EmbeddingService.compute_description_hash(desc),
        )

    def test_skips_ai_when_jd_text_unchanged_despite_newer_timestamp(self):
        from datetime import datetime, timedelta, timezone
        from screening.job_management import JobManagementMixin
        from embedding_service import EmbeddingService
        from app import db

        last_interp = datetime(2026, 5, 1, 0, 0, 0)
        modified_ms = int(
            (last_interp.replace(tzinfo=timezone.utc) + timedelta(hours=1)).timestamp() * 1000
        )
        desc = 'A long enough description ' * 20
        existing = self._make_existing(
            last_interp,
            source_hash=EmbeddingService.compute_description_hash(desc),
        )

        svc = MagicMock(spec=JobManagementMixin)
        svc.extract_job_requirements = MagicMock()
        svc._save_ai_interpreted_requirements = MagicMock()

        with patch('screening.job_management.JobVettingRequirements') as MockReq:
            MockReq.query.filter_by.return_value.first.return_value = existing
            with patch.object(db.session, 'commit') as mock_commit:
                jobs = [{
                    'id': 34967,
                    'title': 'Fullstack Application Developer',
                    'description': desc,
                    'dateLastModified': modified_ms,
                    'address': {'city': 'Dallas', 'state': 'TX', 'countryName': 'US'},
                    'onSite': 1,
                }]
                result = JobManagementMixin.check_and_refresh_changed_jobs(svc, jobs)

        svc.extract_job_requirements.assert_not_called()
        svc._save_ai_interpreted_requirements.assert_not_called()
        assert result['jobs_skipped_unchanged_text'] == 1
        assert result['jobs_refreshed'] == 0
        mock_commit.assert_called()
        assert existing.source_description_hash == EmbeddingService.compute_description_hash(desc)

    def test_backfills_hash_without_ai_when_hash_missing(self):
        from datetime import datetime, timedelta, timezone
        from screening.job_management import JobManagementMixin
        from embedding_service import EmbeddingService
        from app import db

        last_interp = datetime(2026, 5, 1, 0, 0, 0)
        modified_ms = int(
            (last_interp.replace(tzinfo=timezone.utc) + timedelta(hours=1)).timestamp() * 1000
        )
        desc = 'A long enough description ' * 20
        existing = self._make_existing(last_interp, source_hash=None)

        svc = MagicMock(spec=JobManagementMixin)
        svc.extract_job_requirements = MagicMock()
        svc._save_ai_interpreted_requirements = MagicMock()

        with patch('screening.job_management.JobVettingRequirements') as MockReq:
            MockReq.query.filter_by.return_value.first.return_value = existing
            with patch.object(db.session, 'commit'):
                jobs = [{
                    'id': 34967,
                    'title': 'X',
                    'description': desc,
                    'dateLastModified': modified_ms,
                    'address': {},
                    'onSite': 1,
                }]
                result = JobManagementMixin.check_and_refresh_changed_jobs(svc, jobs)

        svc.extract_job_requirements.assert_not_called()
        svc._save_ai_interpreted_requirements.assert_not_called()
        assert result['jobs_skipped_unchanged_text'] == 1
        assert existing.source_description_hash == EmbeddingService.compute_description_hash(desc)

    def test_save_not_called_when_job_unchanged(self):
        from datetime import datetime, timedelta, timezone
        from screening.job_management import JobManagementMixin

        last_interp = datetime(2026, 5, 1, 0, 0, 0)
        # Build the Bullhorn ms timestamp in UTC explicitly — naive
        # datetime.timestamp() is local-TZ and flips this comparison on EDT hosts.
        unchanged_ms = int(
            (last_interp.replace(tzinfo=timezone.utc) - timedelta(hours=1)).timestamp() * 1000
        )
        existing = self._make_existing(last_interp)

        svc = MagicMock(spec=JobManagementMixin)
        svc.extract_job_requirements = MagicMock()
        svc._save_ai_interpreted_requirements = MagicMock()

        with patch('screening.job_management.JobVettingRequirements') as MockReq:
            MockReq.query.filter_by.return_value.first.return_value = existing

            jobs = [{
                'id': 34967,
                'title': 'X',
                'description': 'A long enough description ' * 20,
                'dateLastModified': unchanged_ms,
                'address': {},
                'onSite': 1,
            }]
            JobManagementMixin.check_and_refresh_changed_jobs(svc, jobs)

        svc.extract_job_requirements.assert_not_called()
        svc._save_ai_interpreted_requirements.assert_not_called()

    def test_save_not_called_when_extraction_returns_none(self):
        from datetime import datetime, timedelta, timezone
        from screening.job_management import JobManagementMixin

        last_interp = datetime(2026, 5, 1, 0, 0, 0)
        modified_ms = int(
            (last_interp.replace(tzinfo=timezone.utc) + timedelta(hours=1)).timestamp() * 1000
        )
        existing = self._make_existing(last_interp, source_hash='different-old-hash')

        svc = MagicMock(spec=JobManagementMixin)
        svc.extract_job_requirements = MagicMock(return_value=None)
        svc._save_ai_interpreted_requirements = MagicMock()

        with patch('screening.job_management.JobVettingRequirements') as MockReq:
            MockReq.query.filter_by.return_value.first.return_value = existing

            jobs = [{
                'id': 34967,
                'title': 'X',
                'description': 'A long enough description ' * 20,
                'dateLastModified': modified_ms,
                'address': {},
                'onSite': 1,
            }]
            JobManagementMixin.check_and_refresh_changed_jobs(svc, jobs)

        svc._save_ai_interpreted_requirements.assert_not_called()
