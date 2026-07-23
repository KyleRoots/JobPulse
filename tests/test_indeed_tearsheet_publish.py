"""Tests for Indeed tearsheet → native Bullhorn JobBoard publish (Plan B)."""

from unittest.mock import MagicMock, patch

from indeed_publish.categories import category_id_by_name, category_choices
from indeed_publish.category_mapper import map_published_category
from indeed_publish.config import config_from_env
from indeed_publish.sync import (
    IndeedTearsheetPublishService,
    _fingerprint,
    _first_assigned_recruiter,
    unpublish_job_after_tearsheet_remove,
)


class TestPublishedCategories:
    def test_preferred_it_software_id(self):
        assert category_id_by_name('IT/Software Development') == 2000021

    def test_choices_dedupe_it_software(self):
        names = [n for _, n in category_choices()]
        assert names.count('IT/Software Development') == 1

    def test_project_management_id(self):
        assert category_id_by_name('Project Management') == 2000032


class TestCategoryMapper:
    def test_exact_category(self):
        job = {'title': 'X', 'categories': {'data': [{'id': 1, 'name': 'Human Resources'}]}}
        cid, name, reason = map_published_category(job)
        assert cid == 2000017
        assert name == 'Human Resources'
        assert reason.startswith('exact:')

    def test_alias_developer_title(self):
        job = {'title': 'Python Developer', 'categories': {'data': []}}
        cid, name, reason = map_published_category(job)
        assert cid == 2000021
        assert 'Software' in name
        assert reason.startswith('alias:') or reason.startswith('fuzzy:')

    def test_fallback_default(self):
        job = {'title': 'ZZZ Unknown Role XYZ', 'categories': {'data': []}}
        cid, name, reason = map_published_category(job)
        assert cid == 2000021
        assert reason.startswith('fallback:') or reason.startswith('fuzzy:')


class TestRecruiterAndFingerprint:
    def test_first_assigned_with_email(self):
        job = {
            'assignedUsers': {
                'data': [
                    {'id': 10, 'email': ''},
                    {'id': 65, 'email': 'adam@example.com', 'firstName': 'Adam'},
                ]
            }
        }
        # First entry has id but empty email — still returned (email filled later)
        user = _first_assigned_recruiter(job)
        assert user['id'] == 10

    def test_fingerprint_changes_with_description(self):
        job_a = {'id': 1, 'title': 'T', 'description': 'A', 'publicDescription': '', 'dateLastModified': 1}
        job_b = {'id': 1, 'title': 'T', 'description': 'B', 'publicDescription': '', 'dateLastModified': 1}
        assert _fingerprint(job_a, 1, 2) != _fingerprint(job_b, 1, 2)


class TestConfig:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv('INDEED_TEARSHEET_PUBLISH_ENABLED', raising=False)
        cfg = config_from_env()
        assert cfg['enabled'] is False
        assert cfg['tearsheet_id'] == 1640


class TestSyncService:
    def test_run_sync_disabled(self, monkeypatch):
        monkeypatch.setenv('INDEED_TEARSHEET_PUBLISH_ENABLED', 'false')
        with patch('indeed_publish.sync._save_last_result'):
            result = IndeedTearsheetPublishService(
                config={'enabled': False, 'tearsheet_id': 1640}
            ).run_sync()
        assert result['enabled'] is False
        assert 'disabled' in (result.get('message') or '')

    def test_publish_requires_recruiter_email(self):
        ui = MagicMock()
        bh = MagicMock()
        job = {
            'id': 35233,
            'title': 'Python Test Developer',
            'description': '<p>hi</p>',
            'publicDescription': '',
            'categories': {'data': [{'name': 'IT/Software Development'}]},
            'assignedUsers': {'data': [{'id': 65, 'email': ''}]},
            'dateLastModified': 1,
        }
        bh.get_user_emails.return_value = {}
        svc = IndeedTearsheetPublishService(
            config={
                'enabled': True,
                'tearsheet_id': 1640,
                'job_url_template': 'https://myticas.com/jobs/{job_id}',
            },
            ui_client=ui,
        )
        result = {'skipped': [], 'errors': [], 'published': []}
        try:
            svc._publish_one(ui, bh, job, result, operation='PUBLISH')
            assert False, 'expected error'
        except Exception as exc:
            assert 'email' in str(exc).lower() or 'recruiter' in str(exc).lower()
        ui.publish_boards.assert_not_called()

    def test_membership_add_calls_publish(self):
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        job = {
            'id': 35233,
            'title': 'Python Test Developer',
            'description': '<p>hi</p>',
            'publicDescription': '<p>hi</p>',
            'categories': {'data': [{'name': 'IT/Software Development'}]},
            'assignedUsers': {
                'data': [{'id': 65, 'email': 'adam@example.com', 'firstName': 'Adam'}]
            },
            'dateLastModified': 99,
        }
        cfg = {
            'enabled': True,
            'username': 'u',
            'password': 'p',
            'base_url': 'https://cls45.bullhornstaffing.com',
            'private_label_id': '52989',
            'encryption_key': 'novo',
            'job_url_template': 'https://myticas.com/jobs/{job_id}',
            'notify_email': 'kroots@myticas.com',
            'current_user_id': '25',
            'tearsheet_id': 1640,
        }
        svc = IndeedTearsheetPublishService(config=cfg, ui_client=ui)
        with patch.object(IndeedTearsheetPublishService, '_fetch_tearsheet_jobs', return_value=[job]), \
             patch('indeed_publish.sync._load_state', return_value={'job_ids': [], 'fingerprints': {}}), \
             patch('indeed_publish.sync._save_state') as save_state, \
             patch('indeed_publish.sync._save_last_result'), \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
            result = svc.run_sync()

        assert 35233 in result['published']
        ui.publish_boards.assert_called()
        save_state.assert_called()
        saved = save_state.call_args[0][0]
        assert 35233 in saved['job_ids']


class TestAutoRemoveHook:
    def test_skips_non_1640(self):
        assert unpublish_job_after_tearsheet_remove(1, 1531) is False

    def test_skips_when_disabled(self, monkeypatch):
        monkeypatch.setenv('INDEED_TEARSHEET_PUBLISH_ENABLED', 'false')
        assert unpublish_job_after_tearsheet_remove(1, 1640) is False
