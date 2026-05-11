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

These tests assert the save call happens for both paths.
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

        svc._save_ai_interpreted_requirements.assert_called_once_with(
            34967,
            'Fullstack Application Developer',
            "• Python 5+ years\n• AWS",
            'Dallas, TX, US',
            'On-site',
        )

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
    def _make_existing(self, last_interp):
        existing = MagicMock()
        existing.bullhorn_job_id = 34967
        existing.last_ai_interpretation = last_interp
        existing.ai_interpreted_requirements = "• old reqs"
        existing.edited_requirements = None
        return existing

    def test_save_called_when_modified_job_re_extracts(self):
        from datetime import datetime, timedelta
        from screening.job_management import JobManagementMixin

        last_interp = datetime(2026, 5, 1, 0, 0, 0)
        modified_ms = int((last_interp + timedelta(hours=1)).timestamp() * 1000)
        existing = self._make_existing(last_interp)

        svc = MagicMock(spec=JobManagementMixin)
        svc.extract_job_requirements = MagicMock(return_value="• Refreshed Python 5+\n• AWS")
        svc._save_ai_interpreted_requirements = MagicMock()

        with patch('screening.job_management.JobVettingRequirements') as MockReq:
            MockReq.query.filter_by.return_value.first.return_value = existing

            jobs = [{
                'id': 34967,
                'title': 'Fullstack Application Developer',
                'description': 'A long enough description ' * 20,
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
        )

    def test_save_not_called_when_job_unchanged(self):
        from datetime import datetime, timedelta
        from screening.job_management import JobManagementMixin

        last_interp = datetime(2026, 5, 1, 0, 0, 0)
        unchanged_ms = int((last_interp - timedelta(hours=1)).timestamp() * 1000)
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
        from datetime import datetime, timedelta
        from screening.job_management import JobManagementMixin

        last_interp = datetime(2026, 5, 1, 0, 0, 0)
        modified_ms = int((last_interp + timedelta(hours=1)).timestamp() * 1000)
        existing = self._make_existing(last_interp)

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
