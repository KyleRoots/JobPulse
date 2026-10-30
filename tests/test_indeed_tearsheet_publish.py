"""Tests for Indeed tearsheet → native Bullhorn JobBoard publish (Plan B)."""

import pytest
from unittest.mock import MagicMock, patch

from indeed_publish.categories import category_id_by_name, category_choices
from indeed_publish.category_mapper import map_published_category
from indeed_publish.config import config_from_env
from indeed_publish.sync import (
    INDSHOW_TAG,
    IndeedTearsheetPublishService,
    _fingerprint,
    _first_assigned_recruiter,
    ensure_indshow_tag,
    force_unpublish_jobs,
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
        assert name == 'IT/Software Development'
        assert reason.startswith('fallback:') or reason.startswith('fuzzy:')

    def test_qualified_unknown_falls_back_to_manufacturing(self):
        job = {'title': 'ZZZ Unknown Role XYZ', 'categories': {'data': []}}
        cid, name, reason = map_published_category(job, qualified=True)
        assert cid == 2000025
        assert name == 'Manufacturing'
        assert reason == 'fallback:qualified-manufacturing'

    def test_qualified_industrial_title_aliases(self):
        cases = [
            ('Material Handler', 'Warehouse'),
            ('Cherry Picker Operator', 'Warehouse'),
            ('Machine Operator - Vertical Form Fill & Seal', 'Manufacturing'),
            ('Remote Call Center (TX)', 'Customer Service'),
            ('Route Driver - Appliance Installation - Direct Hire', 'Logistics/Transportation'),
            ('MIG WELDERS| Corsair | 1st and 2nd Shift Openings', 'Manufacturing'),
            ('Food Production', 'Food Services/Hospitality'),
        ]
        for title, expected in cases:
            cid, name, reason = map_published_category(
                {'title': title, 'categories': {'data': []}},
                qualified=True,
            )
            assert name == expected, (title, name, reason)
            assert cid == {
                'Warehouse': 2000039,
                'Manufacturing': 2000025,
                'Customer Service': 2000010,
                'Logistics/Transportation': 2000023,
                'Food Services/Hospitality': 2000015,
            }[expected]
            assert reason.startswith('qualified-alias:')

    def test_qualified_ignores_stale_it_category(self):
        job = {
            'title': 'Material Handler',
            'categories': {'data': [{'id': 2000040, 'name': 'Web Development'}]},
            'publishedCategory': {'id': 2000040, 'name': 'Web Development'},
        }
        cid, name, reason = map_published_category(job, qualified=True)
        assert name == 'Warehouse'
        assert cid == 2000039
        assert reason.startswith('qualified-alias:')

    def test_myticas_keeps_exact_it_category(self):
        job = {
            'title': 'Material Handler',
            'categories': {'data': [{'id': 2000021, 'name': 'IT/Software Development'}]},
        }
        cid, name, reason = map_published_category(job, qualified=False)
        assert cid == 2000021
        assert name == 'IT/Software Development'
        assert reason.startswith('exact:')

    def test_myticas_material_handler_does_not_use_qualified_alias(self):
        job = {'title': 'Material Handler', 'categories': {'data': []}}
        _, name, reason = map_published_category(job, qualified=False)
        assert not reason.startswith('qualified-alias:')
        assert name == 'IT/Software Development'


class TestIndShowTag:
    def test_append_when_missing(self):
        assert ensure_indshow_tag('<p>hi</p>') == f'<p>hi</p>   {INDSHOW_TAG}'

    def test_no_double_append(self):
        tagged = f'<p>hi</p>   {INDSHOW_TAG}'
        assert ensure_indshow_tag(tagged) == tagged

    def test_spaces_format(self):
        out = ensure_indshow_tag('lastword.')
        assert out.endswith(f'   {INDSHOW_TAG}')
        assert out == f'lastword.   {INDSHOW_TAG}'

    def test_empty_unchanged(self):
        assert ensure_indshow_tag('') == ''
        assert ensure_indshow_tag('   ') == '   '

    def test_case_sensitive_presence(self):
        # Legacy lowercase markers are stripped, then the canonical tag is applied
        assert ensure_indshow_tag('x #indshow') == f'x   {INDSHOW_TAG}'


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

    def test_fingerprint_stable_without_date(self):
        job_a = {'id': 1, 'title': 'T', 'description': 'A', 'publicDescription': '', 'dateLastModified': 1}
        job_b = {'id': 1, 'title': 'T', 'description': 'A', 'publicDescription': '', 'dateLastModified': 999}
        assert _fingerprint(job_a, 1, 2, INDSHOW_TAG) == _fingerprint(job_b, 1, 2, INDSHOW_TAG)

    def test_fingerprint_changes_with_description(self):
        job_a = {'id': 1, 'title': 'T', 'description': 'A', 'publicDescription': '', 'dateLastModified': 1}
        job_b = {'id': 1, 'title': 'T', 'description': 'B', 'publicDescription': '', 'dateLastModified': 1}
        assert _fingerprint(job_a, 1, 2, INDSHOW_TAG) != _fingerprint(job_b, 1, 2, INDSHOW_TAG)

    def test_fingerprint_stable_with_or_without_indshow(self):
        """Tagged and untagged sources hash the same (final desc includes tag)."""
        job_a = {'id': 1, 'title': 'T', 'description': 'A', 'publicDescription': '', 'dateLastModified': 1}
        job_b = {
            'id': 1,
            'title': 'T',
            'description': f'A   {INDSHOW_TAG}',
            'publicDescription': '',
            'dateLastModified': 1,
        }
        assert _fingerprint(job_a, 1, 2, INDSHOW_TAG) == _fingerprint(job_b, 1, 2, INDSHOW_TAG)


class TestResponseUserFallback:
    def _svc(self):
        return IndeedTearsheetPublishService(config={
            'enabled': True,
            'tearsheet_id': 2,
            'username': 'u',
            'password': 'p',
        })

    def test_prefers_assigned_recruiter_over_owner(self):
        job = {
            'id': 79302,
            'assignedUsers': {'data': [{'id': 65, 'email': 'recruiter@example.com'}]},
            'owner': {'id': 99, 'email': 'owner@example.com', 'firstName': 'Pat', 'lastName': 'Owner'},
        }
        uid, email, err = self._svc()._resolve_response_user(MagicMock(), job)
        assert err is None
        assert uid == 65
        assert email == 'recruiter@example.com'

    def test_falls_back_to_job_owner_when_no_recruiter(self):
        job = {
            'id': 79302,
            'assignedUsers': {'data': []},
            'owner': {'id': 99, 'email': 'owner@example.com', 'firstName': 'Pat', 'lastName': 'Owner'},
        }
        uid, email, err = self._svc()._resolve_response_user(MagicMock(), job)
        assert err is None
        assert uid == 99
        assert email == 'owner@example.com'

    def test_skips_unassigned_owner_placeholder(self):
        job = {
            'id': 79302,
            'assignedUsers': {'data': []},
            'owner': {'id': 1, 'email': 'x@y.com', 'firstName': 'Unassigned', 'lastName': 'User'},
        }
        uid, email, err = self._svc()._resolve_response_user(MagicMock(), job)
        assert uid is None
        assert email is None
        assert 'owner' in (err or '').lower() or 'recruiter' in (err or '').lower()

    def test_owner_email_lookup_via_bullhorn(self):
        job = {
            'id': 79303,
            'assignedUsers': {'data': []},
            'owner': {'id': 42, 'email': '', 'firstName': 'Sam', 'lastName': 'Lead'},
        }
        bh = MagicMock()
        bh.get_user_emails.return_value = {42: {'email': 'sam@q-staffing.com'}}
        uid, email, err = self._svc()._resolve_response_user(bh, job)
        assert err is None
        assert uid == 42
        assert email == 'sam@q-staffing.com'


class TestConfig:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv('INDEED_TEARSHEET_PUBLISH_ENABLED', raising=False)
        monkeypatch.delenv('SCOUT_TENANT', raising=False)
        cfg = config_from_env()
        assert cfg['enabled'] is False
        assert cfg['tearsheet_id'] == 1640
        assert cfg['membership_publish_operation'] == 'REPUBLISH'

    def test_qualified_tearsheet_and_addchange(self, monkeypatch):
        monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
        monkeypatch.setenv('INDEED_TEARSHEET_PUBLISH_ENABLED', 'true')
        monkeypatch.delenv('BH_CAREER_PORTAL_JOB_URL_TEMPLATE', raising=False)
        monkeypatch.delenv('BH_UI_PRIVATE_LABEL_ID', raising=False)
        cfg = config_from_env()
        assert cfg['enabled'] is True
        assert cfg['tearsheet_id'] == 2
        assert cfg['state_key'] == 'indeed_tearsheet_publish_state_2'
        assert cfg['private_label_id'] == '51284'
        assert cfg['job_url_template'] == 'https://jobs.q-staffing.com/jobs/{job_id}'
        assert cfg['membership_publish_operation'] == 'ADDCHANGE'
        assert cfg['republish_operation'] == 'REPUBLISH'


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
            svc._publish_one(ui, bh, job, result, operation='REPUBLISH')
            assert False, 'expected error'
        except Exception as exc:
            assert 'email' in str(exc).lower() or 'recruiter' in str(exc).lower()
        ui.publish_boards.assert_not_called()

    def test_membership_add_calls_republish_operation(self):
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        bh.update_job_order.return_value = True
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
        assert ui.publish_boards.call_args.kwargs.get('operation') == 'REPUBLISH'
        tagged = f'<p>hi</p>   {INDSHOW_TAG}'
        assert ui.publish_boards.call_args.kwargs.get('description_html') == tagged
        bh.update_job_order.assert_called_once_with(
            35233, {'publicDescription': tagged}
        )
        save_state.assert_called()
        saved = save_state.call_args[0][0]
        assert 35233 in saved['job_ids']
        assert saved.get('pending_unpublish') == []

    def test_publish_one_uses_tagged_description_and_persists(self):
        ui = MagicMock()
        bh = MagicMock()
        bh.update_job_order.return_value = True
        job = {
            'id': 35233,
            'title': 'Python Test Developer',
            'description': '<p>internal</p>',
            'publicDescription': '<p>public</p>',
            'categories': {'data': [{'name': 'IT/Software Development'}]},
            'assignedUsers': {
                'data': [{'id': 65, 'email': 'adam@example.com', 'firstName': 'Adam'}]
            },
            'dateLastModified': 1,
        }
        svc = IndeedTearsheetPublishService(
            config={
                'enabled': True,
                'tearsheet_id': 1640,
                'job_url_template': 'https://myticas.com/jobs/{job_id}',
            },
            ui_client=ui,
        )
        result = {'skipped': [], 'errors': [], 'published': []}
        fp = svc._publish_one(ui, bh, job, result, operation='REPUBLISH')
        tagged = f'<p>public</p>   {INDSHOW_TAG}'
        assert ui.publish_boards.call_args.kwargs['description_html'] == tagged
        bh.update_job_order.assert_called_once_with(
            35233, {'publicDescription': tagged}
        )
        assert job['publicDescription'] == tagged
        assert fp == _fingerprint(job, 2000021, 65, INDSHOW_TAG)

    def test_publish_one_skips_persist_when_tag_present(self):
        ui = MagicMock()
        bh = MagicMock()
        tagged = f'<p>public</p>   {INDSHOW_TAG}'
        job = {
            'id': 35233,
            'title': 'Python Test Developer',
            'description': '',
            'publicDescription': tagged,
            'categories': {'data': [{'name': 'IT/Software Development'}]},
            'assignedUsers': {
                'data': [{'id': 65, 'email': 'adam@example.com', 'firstName': 'Adam'}]
            },
            'dateLastModified': 1,
        }
        svc = IndeedTearsheetPublishService(
            config={
                'enabled': True,
                'tearsheet_id': 1640,
                'job_url_template': 'https://myticas.com/jobs/{job_id}',
            },
            ui_client=ui,
        )
        svc._publish_one(ui, bh, job, {}, operation='REPUBLISH')
        assert ui.publish_boards.call_args.kwargs['description_html'] == tagged
        bh.update_job_order.assert_not_called()

    def test_appleton_publish_keeps_manual_hashtag(self, monkeypatch):
        """Appleton description, including whatever hashtag they typed, is sent as saved."""
        monkeypatch.setattr('indeed_publish.sync.is_qualified_tenant', lambda: True)
        ui = MagicMock()
        bh = MagicMock()
        saved = '<p>Qualifications</p>\n#IND-W'
        job = {
            'id': 79398,
            'title': 'Production Worker',
            'description': saved,
            'publicDescription': saved,
            'correlatedCustomText1': 'Appleton',
            'categories': {'data': [{'name': 'Manufacturing'}]},
            'assignedUsers': {
                'data': [{'id': 65, 'email': 'elena@example.com', 'firstName': 'Elena'}]
            },
            'dateLastModified': 1,
        }
        svc = IndeedTearsheetPublishService(
            config={
                'enabled': True,
                'tearsheet_id': 2,
                'job_url_template': 'https://jobs.q-staffing.com/jobs/{job_id}',
            },
            ui_client=ui,
        )
        svc._publish_one(ui, bh, job, {}, operation='ADDCHANGE')
        assert ui.publish_boards.call_args.kwargs['description_html'] == saved
        assert '#IND-WH' not in ui.publish_boards.call_args.kwargs['description_html']
        bh.update_job_order.assert_not_called()
        assert job['publicDescription'] == saved

    def test_appleton_wi_without_hashtag_still_publishes(self, monkeypatch):
        monkeypatch.setattr('indeed_publish.sync.is_qualified_tenant', lambda: True)
        ui = MagicMock()
        bh = MagicMock()
        saved = '<p>body</p>'
        job = {
            'id': 79399,
            'title': 'Production Worker',
            'description': saved,
            'publicDescription': saved,
            'correlatedCustomText1': 'Appleton (WI)',
            'categories': {'data': [{'name': 'Manufacturing'}]},
            'assignedUsers': {
                'data': [{'id': 65, 'email': 'elena@example.com', 'firstName': 'Elena'}]
            },
        }
        svc = IndeedTearsheetPublishService(
            config={
                'enabled': True,
                'tearsheet_id': 2,
                'job_url_template': 'https://jobs.q-staffing.com/jobs/{job_id}',
            },
            ui_client=ui,
        )
        svc._publish_one(ui, bh, job, {}, operation='ADDCHANGE')
        assert ui.publish_boards.call_args.kwargs['description_html'] == saved
        bh.update_job_order.assert_not_called()

    def test_calhoun_still_gets_automatic_tag(self, monkeypatch):
        monkeypatch.setattr('indeed_publish.sync.is_qualified_tenant', lambda: True)
        ui = MagicMock()
        bh = MagicMock()
        bh.update_job_order.return_value = True
        job = {
            'id': 79400,
            'title': 'Production Worker',
            'description': '<p>body</p>',
            'publicDescription': '<p>body</p>',
            'correlatedCustomText1': 'Calhoun',
            'categories': {'data': [{'name': 'Manufacturing'}]},
            'assignedUsers': {
                'data': [{'id': 65, 'email': 'elena@example.com', 'firstName': 'Elena'}]
            },
        }
        svc = IndeedTearsheetPublishService(
            config={
                'enabled': True,
                'tearsheet_id': 2,
                'job_url_template': 'https://jobs.q-staffing.com/jobs/{job_id}',
            },
            ui_client=ui,
        )
        svc._publish_one(ui, bh, job, {}, operation='ADDCHANGE')
        sent = ui.publish_boards.call_args.kwargs['description_html']
        assert sent.endswith('   #INDCal')
        bh.update_job_order.assert_called_once()

    def _appleton_member(self, desc, job_id=79398, department='Appleton'):
        return {
            'id': job_id,
            'title': 'Production Worker',
            'description': desc,
            'publicDescription': desc,
            'correlatedCustomText1': department,
            'categories': {'data': [{'name': 'Manufacturing'}]},
            'assignedUsers': {
                'data': [{'id': 65, 'email': 'elena@example.com', 'firstName': 'Elena'}]
            },
            'dateLastModified': 1,
        }

    def test_appleton_hashtag_edit_republishes_without_rewriting_description(self, monkeypatch):
        monkeypatch.setattr('indeed_publish.sync.is_qualified_tenant', lambda: True)
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        desc = '<p>Qualifications</p>\n#IND-WH'
        job = self._appleton_member(desc)
        cat_id, _, _ = map_published_category(job, qualified=True)
        old_fp = _fingerprint(self._appleton_member('<p>Qualifications</p>'), cat_id, 65, '')
        cfg = self._sync_cfg()
        cfg['tearsheet_id'] = 2
        svc = IndeedTearsheetPublishService(config=cfg, ui_client=ui)
        with patch.object(IndeedTearsheetPublishService, '_fetch_tearsheet_jobs', return_value=[job]), \
             patch('indeed_publish.sync._load_state', return_value={
                 'job_ids': [79398],
                 'fingerprints': {'79398': old_fp},
                 'pending_unpublish': [],
             }), \
             patch('indeed_publish.sync._save_state'), \
             patch('indeed_publish.sync._save_last_result'), \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
            result = svc.run_sync()
        assert 79398 in result['republished']
        assert ui.publish_boards.call_args.kwargs['description_html'] == desc
        bh.update_job_order.assert_not_called()

    def test_appleton_unchanged_description_is_not_republished(self, monkeypatch):
        monkeypatch.setattr('indeed_publish.sync.is_qualified_tenant', lambda: True)
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        desc = '<p>Qualifications</p>\n#IND-W'
        job = self._appleton_member(desc)
        cat_id, _, _ = map_published_category(job, qualified=True)
        fp = _fingerprint(job, cat_id, 65, '')
        cfg = self._sync_cfg()
        cfg['tearsheet_id'] = 2
        svc = IndeedTearsheetPublishService(config=cfg, ui_client=ui)
        with patch.object(IndeedTearsheetPublishService, '_fetch_tearsheet_jobs', return_value=[job]), \
             patch('indeed_publish.sync._load_state', return_value={
                 'job_ids': [79398],
                 'fingerprints': {'79398': fp},
                 'pending_unpublish': [],
             }), \
             patch('indeed_publish.sync._save_state'), \
             patch('indeed_publish.sync._save_last_result'), \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
            result = svc.run_sync()
        assert result['republished'] == []
        assert result['published'] == []
        ui.publish_boards.assert_not_called()
        bh.update_job_order.assert_not_called()

    def test_appleton_removed_from_tearsheet_still_unpublishes(self, monkeypatch):
        monkeypatch.setattr('indeed_publish.sync.is_qualified_tenant', lambda: True)
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        cfg = self._sync_cfg()
        cfg['tearsheet_id'] = 2
        svc = IndeedTearsheetPublishService(config=cfg, ui_client=ui)
        with patch.object(IndeedTearsheetPublishService, '_fetch_tearsheet_jobs', return_value=[]), \
             patch.object(
                 IndeedTearsheetPublishService,
                 '_fetch_job_detail',
                 return_value=self._appleton_member('<p>x</p> #IND-WH'),
             ), \
             patch('indeed_publish.sync._load_state', return_value={
                 'job_ids': [79398],
                 'fingerprints': {'79398': 'old'},
                 'pending_unpublish': [],
                 'absent_once': [79398],
             }), \
             patch.object(IndeedTearsheetPublishService, '_confirm_absent', return_value=True), \
             patch('indeed_publish.sync._save_state'), \
             patch('indeed_publish.sync._save_last_result'), \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
            result = svc.run_sync()
        assert 79398 in result['unpublished']
        ui.unpublish_boards.assert_called()
        bh.update_job_order.assert_not_called()

    def test_unmapped_department_is_still_skipped(self, monkeypatch):
        monkeypatch.setattr('indeed_publish.sync.is_qualified_tenant', lambda: True)
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        job = self._appleton_member('<p>body</p>', department='Unknown Town')
        cfg = self._sync_cfg()
        cfg['tearsheet_id'] = 2
        svc = IndeedTearsheetPublishService(config=cfg, ui_client=ui)
        with patch.object(IndeedTearsheetPublishService, '_fetch_tearsheet_jobs', return_value=[job]), \
             patch('indeed_publish.sync._load_state', return_value={
                 'job_ids': [],
                 'fingerprints': {},
                 'pending_unpublish': [],
             }), \
             patch('indeed_publish.sync._save_state'), \
             patch('indeed_publish.sync._save_last_result'), \
             patch('indeed_publish.sync._notify_failure'), \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
            result = svc.run_sync()
        assert result['published'] == []
        assert any(item['job_id'] == 79398 for item in result['skipped'])
        ui.publish_boards.assert_not_called()

    def test_unpublish_does_not_append_indshow(self):
        ui = MagicMock()
        bh = MagicMock()
        job = {
            'id': 35410,
            'description': '<p>x</p>',
            'publicDescription': '',
            'publishedCategory': {'id': 2000021},
            'responseUser': {'id': 65},
            'categories': {'data': []},
            'assignedUsers': {'data': [{'id': 65, 'email': 'adam@example.com'}]},
        }
        svc = IndeedTearsheetPublishService(
            config={'enabled': True, 'tearsheet_id': 1640, 'job_url_template': 'https://x/{job_id}'},
            ui_client=ui,
        )
        result = {'unpublished': []}
        svc._unpublish_one(ui, bh, job, result)
        desc = ui.unpublish_boards.call_args.kwargs['description_html']
        assert INDSHOW_TAG not in desc
        assert desc == '<p>x</p>'
        bh.update_job_order.assert_not_called()

    def _sync_cfg(self):
        return {
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

    def test_failed_unpublish_retains_pending_retry(self):
        ui = MagicMock()
        ui.current_user_id = '25'
        ui.unpublish_boards.side_effect = RuntimeError('CFC boom')
        bh = MagicMock()
        bh.authenticate.return_value = True
        cfg = self._sync_cfg()
        svc = IndeedTearsheetPublishService(config=cfg, ui_client=ui)
        with patch.object(IndeedTearsheetPublishService, '_fetch_tearsheet_jobs', return_value=[]), \
             patch.object(IndeedTearsheetPublishService, '_fetch_job_detail', return_value={'id': 35409}), \
             patch('indeed_publish.sync._load_state', return_value={
                 'job_ids': [35409],
                 'fingerprints': {'35409': 'abc'},
                 'pending_unpublish': [],
                 'absent_once': [35409],
             }), \
             patch.object(IndeedTearsheetPublishService, '_confirm_absent', return_value=True), \
             patch('indeed_publish.sync._save_state') as save_state, \
             patch('indeed_publish.sync._save_last_result'), \
             patch('indeed_publish.sync._notify_failure'), \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
            result = svc.run_sync()

        assert 35409 not in result['unpublished']
        assert any('35409' in e for e in result['errors'])
        saved = save_state.call_args[0][0]
        assert saved['job_ids'] == []
        assert 35409 in saved['pending_unpublish']
        assert result['pending_unpublish'] == [35409]

    def test_successful_unpublish_clears_pending(self):
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        cfg = self._sync_cfg()
        svc = IndeedTearsheetPublishService(config=cfg, ui_client=ui)
        with patch.object(IndeedTearsheetPublishService, '_fetch_tearsheet_jobs', return_value=[]), \
             patch.object(
                 IndeedTearsheetPublishService,
                 '_fetch_job_detail',
                 return_value={'id': 35410, 'description': '<p>x</p>'},
             ), \
             patch('indeed_publish.sync._load_state', return_value={
                 'job_ids': [],
                 'fingerprints': {'35410': 'old'},
                 'pending_unpublish': [35410],
             }), \
             patch.object(IndeedTearsheetPublishService, '_confirm_absent', return_value=True), \
             patch('indeed_publish.sync._save_state') as save_state, \
             patch('indeed_publish.sync._save_last_result'), \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
            result = svc.run_sync()

        assert 35410 in result['unpublished']
        ui.unpublish_boards.assert_called()
        saved = save_state.call_args[0][0]
        assert saved['job_ids'] == []
        assert saved['pending_unpublish'] == []
        assert '35410' not in saved['fingerprints']
        assert result['pending_unpublish'] == []

    def test_state_save_does_not_drop_failed_removals(self):
        """Regression: failed unpublish must not be forgotten after save."""
        ui = MagicMock()
        ui.current_user_id = '25'
        ui.unpublish_boards.side_effect = RuntimeError('fail')
        bh = MagicMock()
        bh.authenticate.return_value = True
        member = {
            'id': 34829,
            'title': 'Keep Me',
            'description': '<p>hi</p>',
            'publicDescription': '<p>hi</p>',
            'categories': {'data': [{'name': 'IT/Software Development'}]},
            'assignedUsers': {
                'data': [{'id': 65, 'email': 'adam@example.com', 'firstName': 'Adam'}]
            },
            'dateLastModified': 1,
        }
        cfg = self._sync_cfg()
        svc = IndeedTearsheetPublishService(config=cfg, ui_client=ui)
        with patch.object(
                IndeedTearsheetPublishService, '_fetch_tearsheet_jobs', return_value=[member]
             ), \
             patch.object(
                 IndeedTearsheetPublishService,
                 '_fetch_job_detail',
                 return_value={'id': 35409, 'description': '<p>x</p>'},
             ), \
             patch('indeed_publish.sync._load_state', return_value={
                 'job_ids': [34829, 35409],
                 'fingerprints': {'34829': 'same', '35409': 'gone'},
                 'pending_unpublish': [],
                 'absent_once': [35409],
             }), \
             patch.object(IndeedTearsheetPublishService, '_confirm_absent', return_value=True), \
             patch('indeed_publish.sync._save_state') as save_state, \
             patch('indeed_publish.sync._save_last_result'), \
             patch('indeed_publish.sync._notify_failure'), \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
            # Fingerprint match so 34829 is not republished
            with patch('indeed_publish.sync._fingerprint', return_value='same'):
                result = svc.run_sync()

        saved = save_state.call_args[0][0]
        assert saved['job_ids'] == [34829]
        assert 35409 in saved['pending_unpublish']
        assert '35409' not in saved['fingerprints']
        assert any('35409' in e for e in result['errors'])


    def test_bullhorn_outage_stops_cycle_with_one_email(self):
        from indeed_publish.ui_client import BullhornPublishUnavailable

        ui = MagicMock()
        ui.current_user_id = '25'
        ui.unpublish_boards.side_effect = BullhornPublishUnavailable('careerPortal DELETE HTTP 503')
        bh = MagicMock()
        bh.authenticate.return_value = True
        member = {
            'id': 34829,
            'title': 'New Job',
            'description': '<p>hi</p>',
            'publicDescription': '<p>hi</p>',
            'categories': {'data': [{'name': 'IT/Software Development'}]},
            'assignedUsers': {
                'data': [{'id': 65, 'email': 'adam@example.com', 'firstName': 'Adam'}]
            },
        }
        cfg = self._sync_cfg()
        svc = IndeedTearsheetPublishService(config=cfg, ui_client=ui)
        with patch.object(
                IndeedTearsheetPublishService, '_fetch_tearsheet_jobs', return_value=[member]
             ), \
             patch.object(
                 IndeedTearsheetPublishService,
                 '_fetch_job_detail',
                 side_effect=lambda _bh, jid: {'id': jid, 'description': '<p>x</p>'},
             ), \
             patch('indeed_publish.sync._load_state', return_value={
                 'job_ids': [35409, 35410],
                 'fingerprints': {'35409': 'a', '35410': 'b'},
                 'pending_unpublish': [],
                 'absent_once': [35409, 35410],
             }), \
             patch.object(IndeedTearsheetPublishService, '_confirm_absent', return_value=True), \
             patch('indeed_publish.sync._save_state') as save_state, \
             patch('indeed_publish.sync._save_last_result'), \
             patch('indeed_publish.sync._notify_failure') as notify, \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
            result = svc.run_sync()

        assert ui.unpublish_boards.call_count == 1
        ui.publish_boards.assert_not_called()
        assert result['outage'] is True
        assert notify.call_count == 1
        assert 'unavailable' in notify.call_args[0][0]
        saved = save_state.call_args[0][0]
        assert saved['pending_unpublish'] == [35409, 35410]
        assert saved['job_ids'] == [34829]
        assert '34829' not in saved['fingerprints']


class TestShortReadAndSelfHeal:
    """A partial Bullhorn membership must not DELETE tearsheet jobs."""

    def _member(self, job_id, department='', desc='<p>hi</p>'):
        return {
            'id': job_id,
            'title': 'Python Test Developer',
            'description': desc,
            'publicDescription': desc,
            'correlatedCustomText1': department,
            'categories': {'data': [{'name': 'IT/Software Development'}]},
            'assignedUsers': {
                'data': [{'id': 65, 'email': 'elena@example.com', 'firstName': 'Elena'}]
            },
        }

    def _cfg(self, tearsheet_id=1640):
        return {
            'enabled': True,
            'username': 'u',
            'password': 'p',
            'base_url': 'https://rest.example',
            'private_label_id': '51284',
            'encryption_key': 'novo',
            'job_url_template': 'https://jobs.q-staffing.com/jobs/{job_id}',
            'notify_email': 'kroots@myticas.com',
            'current_user_id': '25',
            'tearsheet_id': tearsheet_id,
        }

    def _run(self, svc, ui, bh, jobs, state):
        with patch.object(
                IndeedTearsheetPublishService, '_fetch_tearsheet_jobs', return_value=jobs
             ), \
             patch('indeed_publish.sync._load_state', return_value=state), \
             patch('indeed_publish.sync._save_state') as save_state, \
             patch('indeed_publish.sync._save_last_result'), \
             patch('indeed_publish.sync._notify_failure') as notify, \
             patch('indeed_publish.sync._fingerprint', return_value='fp'), \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
            result = svc.run_sync()
        saved = save_state.call_args[0][0] if save_state.called else None
        return result, saved, notify

    def test_oct30_short_read_then_full_read_does_not_delete_or_readd(self):
        """18-of-a-larger-set shape: short read, then the same jobs return."""
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        full_ids = list(range(79344, 79344 + 100))
        short_ids = full_ids[:82]  # 18 missing: more than 10% and at least 5
        state = {
            'job_ids': full_ids,
            'fingerprints': {str(jid): 'fp' for jid in full_ids},
            'pending_unpublish': [],
            'absent_once': [],
        }
        svc = IndeedTearsheetPublishService(config=self._cfg(), ui_client=ui)
        short_jobs = [self._member(jid) for jid in short_ids]
        result, saved, notify = self._run(svc, ui, bh, short_jobs, state)
        assert result['unpublished'] == []
        ui.unpublish_boards.assert_not_called()
        assert result['guardrail'] == 'membership_drop'
        assert saved['absent_once'] == []
        assert saved['job_ids'] == full_ids
        assert result['published'] == []
        notify.assert_called_once()

        state = saved
        full_jobs = [self._member(jid) for jid in full_ids]
        result, saved, notify = self._run(svc, ui, bh, full_jobs, state)
        assert result['unpublished'] == []
        assert result['published'] == []
        assert result.get('guardrail') is None
        ui.unpublish_boards.assert_not_called()
        ui.publish_boards.assert_not_called()
        assert saved['job_ids'] == full_ids

    def test_genuine_removal_deletes_on_the_second_absence(self):
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        stay = self._member(34829)
        gone = self._member(35409)
        svc = IndeedTearsheetPublishService(config=self._cfg(), ui_client=ui)
        state = {
            'job_ids': [34829, 35409],
            'fingerprints': {'34829': 'fp', '35409': 'fp'},
            'pending_unpublish': [],
            'absent_once': [],
        }
        with patch.object(IndeedTearsheetPublishService, '_confirm_absent') as confirm:
            confirm.return_value = True
            result, saved, _notify = self._run(svc, ui, bh, [stay], state)
        assert result['unpublished'] == []
        ui.unpublish_boards.assert_not_called()
        confirm.assert_not_called()
        assert saved['job_ids'] == [34829, 35409]
        assert saved['absent_once'] == [35409]

        state = saved
        with patch.object(
            IndeedTearsheetPublishService, '_confirm_absent', return_value=True
        ) as confirm:
            result, saved, _notify = self._run(svc, ui, bh, [stay], state)
        confirm.assert_called()
        assert 35409 in result['unpublished']
        ui.unpublish_boards.assert_called_once()
        assert saved['job_ids'] == [34829]
        assert saved['pending_unpublish'] == []
        assert saved['absent_once'] == []

    def test_unexpected_404_body_does_not_delete(self):
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        stay = self._member(34829)
        svc = IndeedTearsheetPublishService(config=self._cfg(tearsheet_id=2), ui_client=ui)
        state = {
            'job_ids': [34829, 35409],
            'fingerprints': {'34829': 'fp', '35409': 'fp'},
            'pending_unpublish': [],
            'absent_once': [35409],
        }
        bad = _response(404, {
            'errorMessage': 'Unknown or badly structured command: /entity/JobOrder/35409/tearsheets/2.',
            'errorMessageKey': 'errors.badCommand',
        })
        with patch('indeed_publish.sync._rest_get', return_value=bad):
            assert svc._confirm_absent(bh, 35409) is None
            result, saved, _notify = self._run(svc, ui, bh, [stay], state)
        assert result['unpublished'] == []
        ui.unpublish_boards.assert_not_called()
        assert 35409 in saved['job_ids']
        assert any(
            row.get('reason') == 'membership reconfirm failed'
            for row in result['skipped']
        )

    def test_job_tearsheets_association_tells_members_from_non_members(self):
        ui = MagicMock()
        bh = MagicMock()
        svc = IndeedTearsheetPublishService(config=self._cfg(tearsheet_id=2), ui_client=ui)
        member = _response(200, {
            'start': 0,
            'count': 2,
            'data': [
                {'id': 3, 'name': 'Sponsored - ZipRecruiter'},
                {'id': 2, 'name': 'Sponsored - Indeed'},
            ],
        })
        other = _response(200, {
            'start': 0,
            'count': 1,
            'data': [{'id': 3, 'name': 'Sponsored - ZipRecruiter'}],
        })
        empty = _response(404, {
            'errorMessage': 'Tearsheet association is empty.',
            'errorMessageKey': 'errors.associationNotFound',
            'data': [],
        })
        surprise = _response(404, {'errorMessage': 'nope'})
        empty_page = _response(200, {'start': 0, 'count': 0, 'data': []})
        with patch('indeed_publish.sync._rest_get', return_value=member):
            assert svc._confirm_absent(bh, 79512) is False
        with patch('indeed_publish.sync._rest_get', return_value=other):
            assert svc._confirm_absent(bh, 49606) is True
        with patch('indeed_publish.sync._rest_get', return_value=empty_page):
            assert svc._confirm_absent(bh, 49606) is True
        with patch('indeed_publish.sync._rest_get', return_value=empty):
            assert svc._confirm_absent(bh, 49606) is True
        with patch('indeed_publish.sync._rest_get', return_value=surprise):
            assert svc._confirm_absent(bh, 49606) is None

    def test_reconfirm_still_present_skips_delete(self):
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        stay = self._member(34829)
        svc = IndeedTearsheetPublishService(config=self._cfg(), ui_client=ui)
        state = {
            'job_ids': [34829, 35409],
            'fingerprints': {'34829': 'fp', '35409': 'fp'},
            'pending_unpublish': [],
            'absent_once': [35409],
        }
        with patch.object(
            IndeedTearsheetPublishService, '_confirm_absent', return_value=False
        ):
            result, saved, _notify = self._run(svc, ui, bh, [stay], state)
        assert result['unpublished'] == []
        ui.unpublish_boards.assert_not_called()
        assert 35409 in saved['job_ids']
        assert saved['absent_once'] == []

    def test_guardrail_still_publishes_additions_and_emails_once_a_day(self):
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        bh.update_job_order.return_value = True
        prev = list(range(1, 51))
        missing = prev[:6]
        present = prev[6:]
        new_job = self._member(999)
        jobs = [self._member(jid) for jid in present] + [new_job]
        state = {
            'job_ids': prev,
            'fingerprints': {str(jid): 'fp' for jid in prev},
            'pending_unpublish': [],
            'absent_once': [],
        }
        svc = IndeedTearsheetPublishService(config=self._cfg(), ui_client=ui)
        result, saved, notify = self._run(svc, ui, bh, jobs, state)
        assert result['guardrail'] == 'membership_drop'
        assert result['unpublished'] == []
        ui.unpublish_boards.assert_not_called()
        assert 999 in result['published']
        assert ui.publish_boards.call_args.kwargs['operation'] == 'REPUBLISH'
        assert saved['absent_once'] == []
        for jid in missing:
            assert jid in saved['job_ids']
        assert notify.call_count == 1
        assert 'large membership drop' in notify.call_args[0][0]

        state = saved
        ui.publish_boards.reset_mock()
        result, saved, notify = self._run(svc, ui, bh, jobs, state)
        assert result['unpublished'] == []
        assert notify.call_count == 0

    def test_detail_fetch_failure_is_not_a_removal(self):
        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        member = self._member(34829)
        svc = IndeedTearsheetPublishService(config=self._cfg(), ui_client=ui)

        def fetch(self, _bh):
            self._membership_ids = {34829, 35409}
            self._detail_failed = {35409}
            return [member]

        state = {
            'job_ids': [34829, 35409],
            'fingerprints': {'34829': 'fp', '35409': 'fp'},
            'pending_unpublish': [],
            'absent_once': [],
        }
        with patch.object(IndeedTearsheetPublishService, '_fetch_tearsheet_jobs', fetch), \
             patch('indeed_publish.sync._load_state', return_value=state), \
             patch('indeed_publish.sync._save_state') as save_state, \
             patch('indeed_publish.sync._save_last_result'), \
             patch('indeed_publish.sync._fingerprint', return_value='fp'), \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
            result = svc.run_sync()
        saved = save_state.call_args[0][0]
        assert result['unpublished'] == []
        ui.unpublish_boards.assert_not_called()
        assert saved['job_ids'] == [34829, 35409]
        assert result['detail_failed'] == [35409]
        assert result['published'] == []

    def test_failed_membership_read_does_not_change_state(self):
        from indeed_publish.sync import TearsheetMembershipError

        ui = MagicMock()
        ui.current_user_id = '25'
        bh = MagicMock()
        bh.authenticate.return_value = True
        svc = IndeedTearsheetPublishService(config=self._cfg(), ui_client=ui)
        with patch.object(
                IndeedTearsheetPublishService,
                '_fetch_tearsheet_jobs',
                side_effect=TearsheetMembershipError('tearsheet read count 80 != total 137'),
             ), \
             patch('indeed_publish.sync._save_state') as save_state, \
             patch('indeed_publish.sync._save_last_result'), \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
            result = svc.run_sync()
        assert result['membership_read'] == 'failed'
        assert result['unpublished'] == []
        save_state.assert_not_called()
        ui.unpublish_boards.assert_not_called()
        ui.login_with_retry.assert_not_called()

    def test_membership_read_paginates_and_rejects_a_short_page(self):
        svc = IndeedTearsheetPublishService(config=self._cfg(tearsheet_id=2), ui_client=MagicMock())
        bh = MagicMock()
        pages = [
            _response(200, {'total': 3, 'data': [{'id': 1}, {'id': 2}]}),
            _response(200, {'total': 3, 'data': [{'id': 3}]}),
        ]

        def rest_get(_bh, path, params, timeout):
            assert 'BhRestToken' not in params
            assert params['count'] == 2
            assert path.endswith('/jobOrders')
            return pages.pop(0)

        with patch('indeed_publish.sync.MEMBERSHIP_PAGE_SIZE', 2), \
             patch('indeed_publish.sync._rest_get', side_effect=rest_get):
            assert svc._read_member_ids(bh) == {1, 2, 3}

        short = _response(200, {'total': 137, 'data': [{'id': 1}]})
        with patch('indeed_publish.sync._rest_get', return_value=short):
            with pytest.raises(Exception, match='short page'):
                svc._read_member_ids(bh)

        missing_total = _response(200, {'data': [{'id': 1}]})
        with patch('indeed_publish.sync._rest_get', return_value=missing_total):
            with pytest.raises(Exception, match='missing total'):
                svc._read_member_ids(bh)

        http_error = _response(500, {})
        with patch('indeed_publish.sync._rest_get', return_value=http_error):
            with pytest.raises(Exception, match='HTTP 500'):
                svc._read_member_ids(bh)

        # Duplicates must not satisfy total.
        dupes = [
            _response(200, {'total': 2, 'data': [{'id': 7}, {'id': 7}]}),
        ]
        with patch('indeed_publish.sync.MEMBERSHIP_PAGE_SIZE', 2), \
             patch('indeed_publish.sync._rest_get', side_effect=dupes):
            with pytest.raises(Exception, match='count 1 != total 2'):
                svc._read_member_ids(bh)

    def test_job_detail_sends_the_token_as_a_header(self):
        svc = IndeedTearsheetPublishService(config=self._cfg(), ui_client=MagicMock())
        bh = MagicMock()
        bh.base_url = 'https://rest.example/'
        bh.rest_token = 'secret-token'
        resp = _response(200, {'data': {'id': 5, 'title': 'T'}})
        with patch('indeed_publish.sync._rest_get', return_value=resp) as rest:
            job = svc._fetch_job_detail(bh, 5)
        assert job['id'] == 5
        assert 'BhRestToken' not in rest.call_args[0][2]
        assert 'secret-token' not in str(rest.call_args[0][2])

    def test_self_heal_sends_addchange_once_a_day_and_reports_after_two_days(self, monkeypatch):
        from datetime import datetime, timedelta, timezone

        monkeypatch.setattr('indeed_publish.sync.is_qualified_tenant', lambda: True)
        ui = MagicMock()
        ui.current_user_id = '25'
        ui.get_publication_status.return_value = {
            'indeed_published': False,
            'job_id': 79605,
        }
        bh = MagicMock()
        bh.authenticate.return_value = True
        bh.update_job_order.return_value = True
        job = self._member(79605, department='Richmond', desc='<p>body</p>   #INDRic')
        job['title'] = 'Warehouse Associate'
        job['categories'] = {'data': [{'name': 'Warehouse'}]}
        svc = IndeedTearsheetPublishService(config=self._cfg(tearsheet_id=2), ui_client=ui)
        store = {
            'job_ids': [79605],
            'fingerprints': {'79605': 'fp'},
            'pending_unpublish': [],
            'absent_once': [],
            'self_heal': {},
        }
        clock = {'at': datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)}

        def utc_now():
            return clock['at'].strftime('%Y-%m-%dT%H:%M:%SZ')

        def eastern_now():
            from zoneinfo import ZoneInfo
            return clock['at'].astimezone(ZoneInfo('America/New_York'))

        def load_state(_cfg=None):
            return {
                'job_ids': list(store['job_ids']),
                'fingerprints': dict(store['fingerprints']),
                'pending_unpublish': list(store['pending_unpublish']),
                'absent_once': list(store['absent_once']),
                'self_heal': {
                    'last_run_at': (store.get('self_heal') or {}).get('last_run_at'),
                    'jobs': dict((store.get('self_heal') or {}).get('jobs') or {}),
                },
            }

        def save_state(state, _cfg=None):
            store.clear()
            store.update(state)

        def run():
            with patch.object(
                    IndeedTearsheetPublishService, '_fetch_tearsheet_jobs', return_value=[job]
                 ), \
                 patch('indeed_publish.sync._load_state', side_effect=load_state), \
                 patch('indeed_publish.sync._save_state', side_effect=save_state), \
                 patch('indeed_publish.sync._save_last_result'), \
                 patch('indeed_publish.sync._notify_failure') as notify, \
                 patch('indeed_publish.sync._fingerprint', return_value='fp'), \
                 patch('indeed_publish.sync._utc_now', side_effect=utc_now), \
                 patch('indeed_publish.sync._eastern_now', side_effect=eastern_now), \
                 patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh):
                result = svc.run_sync()
            return result, notify

        result, notify = run()
        assert result['self_heal_sent'] == [79605]
        assert result['self_heal_reported'] == []
        assert ui.publish_boards.call_count == 1
        assert ui.publish_boards.call_args.kwargs['operation'] == 'ADDCHANGE'
        notify.assert_not_called()

        ui.publish_boards.reset_mock()
        result, notify = run()
        assert result.get('self_heal_sent') is None
        ui.publish_boards.assert_not_called()
        notify.assert_not_called()

        clock['at'] = clock['at'] + timedelta(days=1)
        result, notify = run()
        assert result['self_heal_sent'] == [79605]
        assert result['self_heal_reported'] == []
        notify.assert_not_called()

        clock['at'] = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc) + timedelta(days=2)
        ui.publish_boards.reset_mock()
        result, notify = run()
        assert result['self_heal_sent'] == [79605]
        assert result['self_heal_reported'] == [79605]
        assert ui.publish_boards.call_count == 1
        assert ui.publish_boards.call_args.kwargs['operation'] == 'ADDCHANGE'
        notify.assert_called_once()
        assert '79605' in notify.call_args[0][1]
        assert 'Bullhorn-to-Indeed' in notify.call_args[0][1]

        ui.get_publication_status.return_value = {
            'indeed_published': True,
            'job_id': 79605,
        }
        clock['at'] = clock['at'] + timedelta(days=1)
        ui.publish_boards.reset_mock()
        result, notify = run()
        assert result.get('self_heal_sent') == []
        ui.publish_boards.assert_not_called()
        assert '79605' not in (store.get('self_heal') or {}).get('jobs', {})


def _response(status, payload):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    return response


class TestAutoRemoveHook:
    def test_skips_non_indeed_tearsheet(self):
        assert unpublish_job_after_tearsheet_remove(1, 1531) is False

    def test_skips_when_disabled(self, monkeypatch):
        monkeypatch.setenv('INDEED_TEARSHEET_PUBLISH_ENABLED', 'false')
        assert unpublish_job_after_tearsheet_remove(1, 1640) is False

    def test_failure_queues_pending_unpublish(self, monkeypatch):
        monkeypatch.setenv('INDEED_TEARSHEET_PUBLISH_ENABLED', 'true')
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
        bh = MagicMock()
        bh.authenticate.return_value = True
        ui = MagicMock()
        ui.current_user_id = '25'
        with patch('indeed_publish.sync.config_from_env', return_value=cfg), \
             patch('utils.bullhorn_helpers.get_bullhorn_service', return_value=bh), \
             patch.object(IndeedTearsheetPublishService, '_fetch_job_detail', return_value={'id': 99}), \
             patch.object(IndeedTearsheetPublishService, '_build_ui_client', return_value=ui), \
             patch.object(
                 IndeedTearsheetPublishService,
                 '_unpublish_one',
                 side_effect=RuntimeError('nope'),
             ), \
             patch('indeed_publish.sync._load_state', return_value={
                 'job_ids': [99], 'fingerprints': {}, 'pending_unpublish': [],
             }), \
             patch('indeed_publish.sync._save_state') as save_state, \
             patch('indeed_publish.sync._notify_failure'):
            assert unpublish_job_after_tearsheet_remove(99, 1640) is False
        saved = save_state.call_args[0][0]
        assert 99 in saved['pending_unpublish']
        assert 99 not in saved['job_ids']


class TestForceUnpublish:
    def test_force_unpublish_disabled(self, monkeypatch):
        monkeypatch.setenv('INDEED_TEARSHEET_PUBLISH_ENABLED', 'false')
        with patch('indeed_publish.sync.config_from_env', return_value={'enabled': False}):
            out = force_unpublish_jobs([35409, 35410])
        assert out['unpublished'] == []
        assert out['skipped']


class TestUIClientLoginRetry:
    def test_retryable_401_only(self):
        from indeed_publish.ui_client import BullhornUIClientError, _is_retryable_login_error

        assert _is_retryable_login_error(
            BullhornUIClientError('UI login rejected (401 — check credentials / password expiry)')
        )
        assert not _is_retryable_login_error(
            BullhornUIClientError('UI login requires MFA — use a service account without MFA')
        )
        assert not _is_retryable_login_error(
            BullhornUIClientError('BH_UI_USERNAME / BH_UI_PASSWORD not configured')
        )

    def test_login_with_retry_recovers_after_transient_401(self):
        from indeed_publish.ui_client import BullhornUIClient, BullhornUIClientError

        client = BullhornUIClient(
            base_url='https://cls45.bullhornstaffing.com',
            username='svc',
            password='secret',
            private_label_id='52989',
        )
        calls = {'n': 0}

        def fake_login():
            calls['n'] += 1
            if calls['n'] == 1:
                raise BullhornUIClientError(
                    'UI login rejected (401 — check credentials / password expiry)'
                )

        with patch.object(client, 'login', side_effect=fake_login), \
             patch('indeed_publish.ui_client.time.sleep'):
            client.login_with_retry(max_attempts=2, backoff_seconds=0)
        assert calls['n'] == 2

    def test_login_with_retry_raises_after_exhausted_attempts(self):
        from indeed_publish.ui_client import BullhornUIClient, BullhornUIClientError

        client = BullhornUIClient(
            base_url='https://cls45.bullhornstaffing.com',
            username='svc',
            password='secret',
            private_label_id='52989',
        )
        err = BullhornUIClientError(
            'UI login rejected (401 — check credentials / password expiry)'
        )
        with patch.object(client, 'login', side_effect=err), \
             patch('indeed_publish.ui_client.time.sleep'), \
             pytest.raises(BullhornUIClientError, match='401'):
            client.login_with_retry(max_attempts=2, backoff_seconds=0)


def _fake_resp(status, payload=None, text=None):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text if text is not None else (
        __import__('json').dumps(payload) if payload is not None else ''
    )
    resp.json.side_effect = (
        (lambda: payload) if payload is not None else ValueError('not json')
    )
    return resp


_LOGIN_OK = {
    'identity': {'userId': 2204241, 'isApiUser': True},
    'sessions': [
        {'name': 'rest', 'value': {
            'token': 'tok-1',
            'endpoint': 'https://rest45.bullhornstaffing.com/rest-services/clp2rd/',
        }},
    ],
}


class TestJobPublishClient:
    def _client(self, session):
        from indeed_publish.ui_client import BullhornUIClient

        return BullhornUIClient(username='svc', password='secret', session=session)

    def test_login_reads_rest_session_and_skips_cls45(self):
        session = MagicMock()
        session.post.side_effect = [_fake_resp(200, {}), _fake_resp(200, _LOGIN_OK)]
        client = self._client(session)
        client.login()
        assert client.rest_url == 'https://rest45.bullhornstaffing.com/rest-services/clp2rd/'
        assert client.rest_token == 'tok-1'
        assert client.current_user_id == '2204241'
        for call in session.post.call_args_list + session.get.call_args_list:
            assert 'cls45' not in str(call)

    def test_login_follows_check_for_redirect_host(self):
        default_login = 'https://universal-east.bullhornstaffing.com/universal-login'
        redirect_host = 'https://universal.bullhornstaffing.com/universal-login'
        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, {'redirect': redirect_host + '/'}),
            _fake_resp(200, _LOGIN_OK),
        ]
        from indeed_publish.ui_client import BullhornUIClient

        client = BullhornUIClient(
            username='fake-user',
            password='fake-password',
            universal_login_url=default_login,
            session=session,
        )
        client.login()
        assert session.post.call_args_list[0].args[0] == (
            f'{default_login}/session/checkForRedirect'
        )
        login_url = session.post.call_args_list[1].args[0]
        assert login_url.startswith(f'{redirect_host}/session/login')
        assert not login_url.startswith(default_login)
        assert client.rest_url == 'https://rest45.bullhornstaffing.com/rest-services/clp2rd/'
        assert client.rest_token == 'tok-1'
        assert client.current_user_id == '2204241'

    def test_login_stays_on_client_url_when_check_for_redirect_is_empty(self):
        client_login = 'https://client.example.test/universal-login'
        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, {}),
            _fake_resp(200, _LOGIN_OK),
        ]
        from indeed_publish.ui_client import BullhornUIClient

        client = BullhornUIClient(
            username='fake-user',
            password='fake-password',
            universal_login_url=client_login,
            session=session,
        )
        client.login()
        assert session.post.call_args_list[0].args[0] == (
            f'{client_login}/session/checkForRedirect'
        )
        login_url = session.post.call_args_list[1].args[0]
        assert login_url.startswith(f'{client_login}/session/login')

    def test_login_stays_on_client_url_when_check_for_redirect_is_not_json(self):
        client_login = 'https://client.example.test/universal-login'
        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, text='not-json'),
            _fake_resp(200, _LOGIN_OK),
        ]
        from indeed_publish.ui_client import BullhornUIClient

        client = BullhornUIClient(
            username='fake-user',
            password='fake-password',
            universal_login_url=client_login,
            session=session,
        )
        client.login()
        assert session.post.call_args_list[0].args[0] == (
            f'{client_login}/session/checkForRedirect'
        )
        login_url = session.post.call_args_list[1].args[0]
        assert login_url.startswith(f'{client_login}/session/login')

    def test_login_without_rest_session_fails(self):
        from indeed_publish.ui_client import BullhornUIClientError

        session = MagicMock()
        session.post.side_effect = [_fake_resp(200, {}), _fake_resp(200, {'identity': {}})]
        with pytest.raises(BullhornUIClientError, match='no REST session'):
            self._client(session).login()

    def test_publish_posts_json_addchange_to_both_boards(self):
        import json as _json

        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, {}),
            _fake_resp(200, _LOGIN_OK),
            _fake_resp(200, {'success': True, 'message': 'corporate job board'}),
            _fake_resp(200, {'success': True, 'message': 'This job will be published to Indeed.'}),
        ]
        client = self._client(session)
        out = client.publish_boards(
            job_id=79614,
            published_category_id=2000001,
            response_user_id=272,
            description_html='<p>x</p>',
            operation='REPUBLISH',
        )
        assert list(out) == ['careerPortal', 'indeed']
        board_calls = session.post.call_args_list[2:]
        urls = [c.args[0] for c in board_calls]
        assert urls == [
            'https://rest45.bullhornstaffing.com/rest-services/clp2rd/services/JobPublish/careerPortal',
            'https://rest45.bullhornstaffing.com/rest-services/clp2rd/services/JobPublish/indeed',
        ]
        body = _json.loads(board_calls[1].kwargs['data'])
        assert body == {
            'jobPostingID': 79614,
            'description': '<p>x</p>',
            'publishedCategoryID': 2000001,
            'responseUserID': 272,
            'operation': 'ADDCHANGE',
            'externalAccountID': -1,
        }
        assert board_calls[1].kwargs['headers']['BhRestToken'] == 'tok-1'
        assert 'BhRestToken' not in board_calls[1].kwargs['params']
        assert board_calls[1].kwargs['headers']['Content-Type'] == 'application/json'

    def test_unreachable_errors_do_not_echo_password_or_token(self):
        import requests as _requests
        from indeed_publish.ui_client import BullhornPublishUnavailable, BullhornUIClientError

        leak = 'https://x/session/login?password=secret&BhRestToken=tok-1'
        session = MagicMock()
        session.post.side_effect = _requests.ConnectionError(f'Max retries exceeded with url: {leak}')
        with pytest.raises(BullhornUIClientError) as login_err:
            self._client(session).login()
        assert 'secret' not in str(login_err.value)
        assert login_err.value.__cause__ is None

        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, {}),
            _fake_resp(200, _LOGIN_OK),
            _requests.ConnectionError(f'Max retries exceeded with url: {leak}'),
        ]
        with pytest.raises(BullhornPublishUnavailable) as pub_err:
            self._client(session).unpublish_boards(job_id=1)
        assert 'tok-1' not in str(pub_err.value)
        assert 'secret' not in str(pub_err.value)

    def test_get_publication_status_is_the_publish_job_boards_get(self):
        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, {}),
            _fake_resp(200, _LOGIN_OK),
        ]
        session.get.return_value = _fake_resp(200, {
            'publishedDescription': '<p>do not keep</p>',
            'publishedCategoryID': 7,
            'accounts': [
                {
                    'name': 'Indeed',
                    'isPublished': True,
                    'dateLastPublished': 1791481016783,
                    'credentials': [{'externalAccountID': -1, 'displayName': 'secret-account'}],
                    'lastPublishedValues': {'description': 'secret-body'},
                },
                {
                    'name': 'Corporate',
                    'isPublished': True,
                    'dateLastPublished': 1791481016927,
                    'credentials': [],
                },
                {
                    'name': 'SimplyHired',
                    'isPublished': False,
                    'dateLastPublished': None,
                },
            ],
        })
        client = self._client(session)
        out = client.get_publication_status(34869)

        assert session.post.call_count == 2
        get_call = session.get.call_args
        assert get_call.args[0] == (
            'https://rest45.bullhornstaffing.com/rest-services/clp2rd/'
            'services/JobPublish/boards'
        )
        assert get_call.kwargs['params'] == {'jobPostingID': 34869}
        assert get_call.kwargs['headers']['BhRestToken'] == 'tok-1'
        assert 'BhRestToken' not in get_call.kwargs['params']
        assert out == {
            'job_id': 34869,
            'indeed_published': True,
            'indeed_date_last_published': 1791481016783,
            'career_portal_published': True,
            'career_portal_date_last_published': 1791481016927,
            'accounts': [
                {'name': 'Indeed', 'isPublished': True, 'dateLastPublished': 1791481016783},
                {'name': 'Corporate', 'isPublished': True, 'dateLastPublished': 1791481016927},
                {'name': 'SimplyHired', 'isPublished': False, 'dateLastPublished': None},
            ],
        }
        rendered = str(out)
        assert 'do not keep' not in rendered
        assert 'secret-account' not in rendered
        assert 'secret-body' not in rendered

    def test_get_publication_status_without_indeed_account(self):
        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, {}),
            _fake_resp(200, _LOGIN_OK),
        ]
        session.get.return_value = _fake_resp(200, {
            'accounts': [
                {'name': 'Corporate', 'isPublished': False, 'dateLastPublished': None},
            ],
        })
        out = self._client(session).get_publication_status(23267)
        assert out['indeed_published'] is False
        assert out['indeed_date_last_published'] is None
        assert out['career_portal_published'] is False

    def test_get_publication_status_http_error(self):
        from indeed_publish.ui_client import BullhornUIClientError

        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, {}),
            _fake_resp(200, _LOGIN_OK),
        ]
        session.get.return_value = _fake_resp(404, {'errorMessage': 'Resource not found.', 'errorCode': 404})
        with pytest.raises(BullhornUIClientError, match='404'):
            self._client(session).get_publication_status(34869)

    def test_unpublish_indeed_does_not_post_career_portal(self):
        import json as _json

        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, {}),
            _fake_resp(200, _LOGIN_OK),
            _fake_resp(200, {'success': True, 'message': 'This job will be removed from Indeed.'}),
        ]
        client = self._client(session)
        client.unpublish_indeed(job_id=79425, published_category_id=3, response_user_id=258)
        board_calls = session.post.call_args_list[2:]
        assert len(board_calls) == 1
        assert board_calls[0].args[0].endswith('/services/JobPublish/indeed')
        assert _json.loads(board_calls[0].kwargs['data'])['operation'] == 'DELETE'
        assert 'careerPortal' not in board_calls[0].args[0]

    def test_unpublish_deletes_from_career_portal_and_indeed(self):
        import json as _json

        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, {}),
            _fake_resp(200, _LOGIN_OK),
            _fake_resp(200, {'success': True}),
            _fake_resp(200, {'success': True, 'message': 'This job will be removed from Indeed.'}),
        ]
        client = self._client(session)
        client.unpublish_boards(job_id=79614, published_category_id=2000001, response_user_id=272)
        board_calls = session.post.call_args_list[2:]
        assert [c.args[0].rsplit('/', 1)[-1] for c in board_calls] == ['careerPortal', 'indeed']
        assert all(_json.loads(c.kwargs['data'])['operation'] == 'DELETE' for c in board_calls)

    def test_server_error_raises_unavailable(self):
        from indeed_publish.ui_client import BullhornPublishUnavailable

        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, {}),
            _fake_resp(200, _LOGIN_OK),
            _fake_resp(503, text='<title>This page is currently unavailable</title>'),
        ]
        with pytest.raises(BullhornPublishUnavailable, match='503'):
            self._client(session).unpublish_boards(job_id=1)

    def test_success_false_raises(self):
        from indeed_publish.ui_client import BullhornPublishUnavailable, BullhornUIClientError

        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, {}),
            _fake_resp(200, _LOGIN_OK),
            _fake_resp(200, {'success': False, 'message': 'Invalid category'}),
        ]
        with pytest.raises(BullhornUIClientError, match='Invalid category') as info:
            self._client(session).publish_boards(
                job_id=1, published_category_id=2, response_user_id=3, description_html='d',
            )
        assert not isinstance(info.value, BullhornPublishUnavailable)

    def test_expired_rest_session_relogs_once(self):
        session = MagicMock()
        session.post.side_effect = [
            _fake_resp(200, {}),
            _fake_resp(200, _LOGIN_OK),
            _fake_resp(401, {'errorMessage': 'Bad BhRestToken'}),
            _fake_resp(200, {}),
            _fake_resp(200, _LOGIN_OK),
            _fake_resp(200, {'success': True}),
        ]
        out = self._client(session).publish_boards(
            job_id=1, published_category_id=2, response_user_id=3,
            description_html='d', boards=('indeed',),
        )
        assert out['indeed']['status_code'] == 200
        assert session.post.call_count == 6

    def test_legacy_board_names_still_accepted(self):
        from indeed_publish.ui_client import normalize_board, normalize_operation

        assert normalize_board('Corporate') == 'careerPortal'
        assert normalize_board('Indeed') == 'indeed'
        assert normalize_operation('UNPUBLISH') == 'DELETE'
        assert normalize_operation('PUBLISH') == 'ADDCHANGE'


class _FakeSettings:
    store = {}

    @classmethod
    def get_value(cls, key, default=None):
        return cls.store.get(key, default)

    @classmethod
    def set_value(cls, key, value, **_k):
        cls.store[key] = value


class TestPerSubjectNotifyCooldown:
    def _patch(self):
        _FakeSettings.store = {}
        return patch('models.GlobalSettings', _FakeSettings)

    def test_two_jobs_alternating_each_cool_down(self):
        from indeed_publish.sync import _notify_on_cooldown, _stamp_notify

        a = 'Indeed republish failed for job 23267'
        b = 'Indeed republish failed for job 38327'
        with self._patch():
            _stamp_notify(a)
            _stamp_notify(b)
            assert _notify_on_cooldown(a)
            assert _notify_on_cooldown(b)
            assert not _notify_on_cooldown('Indeed republish failed for job 1')

    def test_job_subjects_wait_a_day_and_others_thirty_minutes(self):
        import json as _json
        from datetime import datetime, timedelta, timezone
        from indeed_publish.config import NOTIFY_STAMP_KEY
        from indeed_publish.sync import _notify_on_cooldown

        two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        job = 'Indeed publish failed for job 79605'
        outage = 'Indeed tearsheet publish: Bullhorn publishing unavailable'
        with self._patch():
            _FakeSettings.store[NOTIFY_STAMP_KEY] = _json.dumps(
                {'sent': {job: two_hours_ago, outage: two_hours_ago}}
            )
            assert _notify_on_cooldown(job)
            assert not _notify_on_cooldown(outage)

    def test_reads_old_single_subject_stamp(self):
        import json as _json
        from indeed_publish.config import NOTIFY_STAMP_KEY
        from indeed_publish.sync import _notify_on_cooldown, _utc_now

        subject = 'Indeed tearsheet publish: Bullhorn UI login failed'
        with self._patch():
            _FakeSettings.store[NOTIFY_STAMP_KEY] = _json.dumps({'subject': subject, 'at': _utc_now()})
            assert _notify_on_cooldown(subject)

    def test_stamp_prunes_entries_older_than_a_day(self):
        import json as _json
        from datetime import datetime, timedelta, timezone
        from indeed_publish.config import NOTIFY_STAMP_KEY
        from indeed_publish.sync import _stamp_notify

        old = (datetime.now(timezone.utc) - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with self._patch():
            _FakeSettings.store[NOTIFY_STAMP_KEY] = _json.dumps({'sent': {'Indeed publish failed for job 1': old}})
            _stamp_notify('Indeed publish failed for job 2')
            sent = _json.loads(_FakeSettings.store[NOTIFY_STAMP_KEY])['sent']
        assert list(sent) == ['Indeed publish failed for job 2']


class TestNotifyCooldown:
    def test_skips_repeat_subject_within_window(self):
        from indeed_publish.sync import _notify_failure

        sent = {'n': 0}

        class FakeEmail:
            def send_notification_email(self, **_k):
                sent['n'] += 1

        with patch('indeed_publish.sync._notify_on_cooldown', return_value=True), \
             patch('utils.bullhorn_helpers.get_email_service', return_value=FakeEmail()):
            _notify_failure(
                'Indeed tearsheet publish: Bullhorn UI login failed',
                'UI login rejected (401)',
                'kroots@myticas.com',
            )
        assert sent['n'] == 0

    def test_sends_when_not_on_cooldown(self):
        from indeed_publish.sync import _notify_failure

        sent = {'n': 0}
        stamped = {'n': 0}

        class FakeEmail:
            def send_notification_email(self, **_k):
                sent['n'] += 1

        with patch('indeed_publish.sync._notify_on_cooldown', return_value=False), \
             patch('indeed_publish.sync._stamp_notify', lambda *_: stamped.__setitem__('n', 1)), \
             patch('utils.bullhorn_helpers.get_email_service', return_value=FakeEmail()):
            _notify_failure(
                'Indeed tearsheet publish: Bullhorn UI login failed',
                'UI login rejected (401)',
                'kroots@myticas.com',
            )
        assert sent['n'] == 1
        assert stamped['n'] == 1
