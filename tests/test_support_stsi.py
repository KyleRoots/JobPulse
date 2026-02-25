"""
Regression tests for the STSI Internal Support Request form.
Covers: routing, contact search, email routing logic, template rendering.
"""
import json
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(scope='module')
def app():
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


class TestSTSIFormRendering:
    def test_stsi_form_loads(self, client, app):
        with app.app_context():
            resp = client.get('/support/stsi')
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'STSI Group' in html
            assert 'stsi_logo.png' in html

    def test_stsi_form_has_brand_hidden_field(self, client, app):
        with app.app_context():
            resp = client.get('/support/stsi')
            html = resp.data.decode()
            assert 'name="brand" value="STSI"' in html

    def test_stsi_form_teal_branding(self, client, app):
        with app.app_context():
            resp = client.get('/support/stsi')
            html = resp.data.decode()
            assert '#00B5B5' in html

    def test_myticas_form_still_works(self, client, app):
        with app.app_context():
            resp = client.get('/support')
            assert resp.status_code == 200
            html = resp.data.decode()
            assert 'Myticas Consulting' in html

    def test_stsi_form_autocomplete_uses_stsi_brand(self, client, app):
        with app.app_context():
            resp = client.get('/support/stsi')
            html = resp.data.decode()
            assert "brand=STSI" in html


class TestSTSIContactSearch:
    def test_search_stsi_contacts(self, client, app):
        with app.app_context():
            resp = client.get('/support/contacts/search?q=Jo&brand=STSI')
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert len(data) >= 1
            names = [c['full_name'] for c in data]
            assert 'Josh Bocek' in names

    def test_search_stsi_contacts_emma(self, client, app):
        with app.app_context():
            resp = client.get('/support/contacts/search?q=Em&brand=STSI')
            assert resp.status_code == 200
            data = json.loads(resp.data)
            names = [c['full_name'] for c in data]
            assert 'Emma Valentine' in names

    def test_stsi_search_excludes_myticas(self, client, app):
        with app.app_context():
            resp = client.get('/support/contacts/search?q=Jo&brand=STSI')
            data = json.loads(resp.data)
            for c in data:
                assert '@myticas.com' not in c['email']

    def test_myticas_search_excludes_stsi(self, client, app):
        with app.app_context():
            resp = client.get('/support/contacts/search?q=Jo&brand=Myticas')
            data = json.loads(resp.data)
            for c in data:
                assert '@stsigroup.com' not in c['email']

    def test_search_returns_department(self, client, app):
        with app.app_context():
            resp = client.get('/support/contacts/search?q=Josh&brand=STSI')
            data = json.loads(resp.data)
            assert len(data) >= 1
            assert data[0]['department'] == 'STS-STSI'


class TestSTSIRouting:
    def test_default_routing(self):
        from routes.support_request import get_routing_info
        to, cc = get_routing_info('ats_issue', brand='STSI')
        assert to == 'jbocek@stsigroup.com'
        assert 'kroots@myticas.com' in cc
        assert 'jbocek@stsigroup.com' in cc

    def test_email_notifications_routing(self):
        from routes.support_request import get_routing_info
        to, cc = get_routing_info('email_notifications', brand='STSI')
        assert to == 'doneil@q-staffing.com'
        assert 'kroots@myticas.com' in cc
        assert 'jbocek@stsigroup.com' in cc

    def test_backoffice_onboarding_routing(self):
        from routes.support_request import get_routing_info
        to, cc = get_routing_info('backoffice_onboarding', brand='STSI')
        assert to == 'evalentine@stsigroup.com'
        assert 'kroots@myticas.com' in cc
        assert 'jbocek@stsigroup.com' in cc

    def test_backoffice_finance_routing(self):
        from routes.support_request import get_routing_info
        to, cc = get_routing_info('backoffice_finance', brand='STSI')
        assert to == 'evalentine@stsigroup.com'
        assert 'kroots@myticas.com' in cc
        assert 'jbocek@stsigroup.com' in cc

    def test_all_other_categories_go_to_default(self):
        from routes.support_request import get_routing_info
        for cat in ('candidate_parsing', 'job_posting', 'account_access',
                     'data_correction', 'feature_request', 'other'):
            to, cc = get_routing_info(cat, brand='STSI')
            assert to == 'jbocek@stsigroup.com', f'{cat} should route to default'
            assert 'kroots@myticas.com' in cc

    def test_cc_always_includes_both(self):
        from routes.support_request import get_routing_info
        for cat in ('ats_issue', 'email_notifications', 'backoffice_onboarding',
                     'backoffice_finance', 'other'):
            _, cc = get_routing_info(cat, brand='STSI')
            assert 'kroots@myticas.com' in cc
            assert 'jbocek@stsigroup.com' in cc

    def test_myticas_routing_unchanged(self):
        from routes.support_request import get_routing_info
        to, cc = get_routing_info('ats_issue', brand='Myticas')
        assert to == 'dsifer@myticas.com'
        assert 'kroots@myticas.com' in cc

    def test_myticas_routing_default_brand(self):
        from routes.support_request import get_routing_info
        to, cc = get_routing_info('ats_issue')
        assert to == 'dsifer@myticas.com'


class TestSTSIEmailBranding:
    def test_stsi_email_html_branding(self):
        from routes.support_request import build_support_email_html
        html = build_support_email_html(
            requester_name='Test User',
            requester_email='test@stsigroup.com',
            internal_department='STS-STSI',
            category_label='ATS / Bullhorn Issue',
            category_icon='🔧',
            priority_label='Medium',
            priority_color='#ffc107',
            subject='Test Issue',
            description='Test description',
            attachments=[],
            brand='STSI',
        )
        assert 'STSI' in html
        assert '#003d4d' in html
        assert '#00B5B5' in html
        assert 'STSI Group' in html

    def test_myticas_email_html_branding(self):
        from routes.support_request import build_support_email_html
        html = build_support_email_html(
            requester_name='Test User',
            requester_email='test@myticas.com',
            internal_department='MYT-Ottawa',
            category_label='ATS / Bullhorn Issue',
            category_icon='🔧',
            priority_label='Medium',
            priority_color='#ffc107',
            subject='Test Issue',
            description='Test description',
            attachments=[],
            brand='Myticas',
        )
        assert 'MYTICAS' in html
        assert 'Myticas Consulting' in html
        assert '#1e3c72' in html

    def test_default_brand_is_myticas(self):
        from routes.support_request import build_support_email_html
        html = build_support_email_html(
            requester_name='Test', requester_email='t@t.com',
            internal_department='', category_label='Other', category_icon='📌',
            priority_label='Low', priority_color='#28a745',
            subject='Test', description='Test', attachments=[],
        )
        assert 'MYTICAS' in html


class TestSTSISubmission:
    @patch('routes.support_request.send_support_email', return_value=True)
    def test_submit_stsi_request(self, mock_send, client, app):
        with app.app_context():
            resp = client.post('/support/submit', data={
                'requesterName': 'Josh Bocek',
                'requesterEmail': 'jbocek@stsigroup.com',
                'issueCategory': 'ats_issue',
                'priority': 'medium',
                'subject': 'Test STSI Issue',
                'description': 'Testing the STSI support form',
                'internalDepartment': 'STS-STSI',
                'brand': 'STSI',
            })
            assert resp.status_code == 200
            data = json.loads(resp.data)
            assert data['success'] is True
            mock_send.assert_called_once()

    @patch('routes.support_request.send_support_email', return_value=True)
    def test_submit_stsi_email_subject_prefix(self, mock_send, client, app):
        with app.app_context():
            resp = client.post('/support/submit', data={
                'requesterName': 'Josh Bocek',
                'requesterEmail': 'jbocek@stsigroup.com',
                'issueCategory': 'email_notifications',
                'priority': 'high',
                'subject': 'Notification Problem',
                'description': 'Emails not arriving',
                'internalDepartment': 'STS-STSI',
                'brand': 'STSI',
            })
            assert resp.status_code == 200
            call_kwargs = mock_send.call_args
            subject_arg = call_kwargs.kwargs.get('subject', '')
            assert '[STSI Support]' in subject_arg

    @patch('routes.support_request.send_support_email', return_value=True)
    def test_submit_myticas_keeps_prefix(self, mock_send, client, app):
        with app.app_context():
            resp = client.post('/support/submit', data={
                'requesterName': 'Kyle Roots',
                'requesterEmail': 'kroots@myticas.com',
                'issueCategory': 'ats_issue',
                'priority': 'low',
                'subject': 'Test Myticas Issue',
                'description': 'Testing Myticas form still works',
                'internalDepartment': 'MYT-Ottawa',
                'brand': 'Myticas',
            })
            assert resp.status_code == 200
            call_kwargs = mock_send.call_args
            subject_arg = call_kwargs.kwargs.get('subject', '')
            assert '[Myticas Support]' in subject_arg

    @patch('routes.support_request.send_support_email', return_value=True)
    def test_submit_default_brand_is_myticas(self, mock_send, client, app):
        with app.app_context():
            resp = client.post('/support/submit', data={
                'requesterName': 'Test User',
                'requesterEmail': 'test@test.com',
                'issueCategory': 'other',
                'priority': 'low',
                'subject': 'Test',
                'description': 'Test',
                'internalDepartment': '',
            })
            assert resp.status_code == 200
            call_kwargs = mock_send.call_args
            subject_arg = call_kwargs.kwargs.get('subject', '')
            assert '[Myticas Support]' in subject_arg


class TestSTSIDomainRedirect:
    def test_stsi_support_domain_root(self, app):
        with app.app_context():
            with app.test_client() as c:
                resp = c.get('/', headers={'Host': 'support.stsigroup.com'})
                assert resp.status_code == 200
                html = resp.data.decode()
                assert 'STSI Group' in html

    def test_stsi_support_domain_404(self, app):
        with app.app_context():
            with app.test_client() as c:
                resp = c.get('/random', headers={'Host': 'support.stsigroup.com'})
                assert resp.status_code == 404

    def test_myticas_support_domain_still_works(self, app):
        with app.app_context():
            with app.test_client() as c:
                resp = c.get('/', headers={'Host': 'support.myticas.com'})
                assert resp.status_code == 200
                html = resp.data.decode()
                assert 'Myticas Consulting' in html


class TestSTSISeedData:
    def test_seed_data_count(self):
        from seed_database import SUPPORT_CONTACTS_STSI
        assert len(SUPPORT_CONTACTS_STSI) == 15

    def test_seed_data_all_have_department(self):
        from seed_database import SUPPORT_CONTACTS_STSI
        for c in SUPPORT_CONTACTS_STSI:
            assert c['department'] == 'STS-STSI'

    def test_seed_data_no_stsi_house(self):
        from seed_database import SUPPORT_CONTACTS_STSI
        names = [f"{c['first_name']} {c['last_name']}" for c in SUPPORT_CONTACTS_STSI]
        assert 'STSI House' not in names

    def test_seed_data_excludes_disabled_users(self):
        from seed_database import SUPPORT_CONTACTS_STSI
        emails = [c['email'] for c in SUPPORT_CONTACTS_STSI]
        assert 'spopulorum@stsigroup.com' not in emails
        assert 'kthomas@stsigroup.com' not in emails

    def test_seed_data_includes_key_contacts(self):
        from seed_database import SUPPORT_CONTACTS_STSI
        emails = [c['email'] for c in SUPPORT_CONTACTS_STSI]
        assert 'jbocek@stsigroup.com' in emails
        assert 'evalentine@stsigroup.com' in emails
        assert 'kmiller@stsigroup.com' in emails
