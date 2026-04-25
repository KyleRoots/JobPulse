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
    """STSI ticket routing — current behavior as of April 2026.

    HISTORICAL CONTEXT (do not "restore" without explicit owner approval):
    Originally (March 22, 2026, when the STSI form launched), STSI tickets
    routed To: jbocek@stsigroup.com with jbocek in the CC list.  On
    March 24, 2026 (commit e1bf5c11) the routing was changed to send STSI
    tickets through Scout Support AI for triage and to escalate to Kyle
    Roots (kroots@myticas.com) only when the AI cannot resolve the ticket
    or when the category specifically targets doneil@q-staffing.com or
    evalentine@stsigroup.com.  The STSI CC list was emptied at the same
    time so that incoming requests no longer notify Josh by default —
    Scout Support owns the intake conversation now.

    The Myticas CC list (`_CC_ALWAYS`) is also empty in current production;
    Kyle is reached via the BCC channel (`_BCC_ALWAYS`) instead so he
    receives a copy of every support request without appearing in the CC
    visible to the requester.

    These tests assert the CURRENT routing behavior so that future agents
    do not silently regress it back to the pre-March-24 layout.
    """

    def test_default_routing(self):
        from routes.support_request import get_routing_info
        to, cc = get_routing_info('ats_issue', brand='STSI')
        # Default STSI category goes to Kyle, who triages via Scout Support.
        assert to == 'kroots@myticas.com'
        # CC list is intentionally empty — Scout Support owns intake.
        assert cc == []

    def test_email_notifications_routing(self):
        from routes.support_request import get_routing_info
        to, cc = get_routing_info('email_notifications', brand='STSI')
        # email_notifications has a dedicated owner outside of Scout Support.
        assert to == 'doneil@q-staffing.com'
        assert cc == []

    def test_backoffice_onboarding_routing(self):
        from routes.support_request import get_routing_info
        to, cc = get_routing_info('backoffice_onboarding', brand='STSI')
        # Back-office onboarding routes directly to Emma at STSI.
        assert to == 'evalentine@stsigroup.com'
        assert cc == []

    def test_backoffice_finance_routing(self):
        from routes.support_request import get_routing_info
        to, cc = get_routing_info('backoffice_finance', brand='STSI')
        # Back-office finance also routes directly to Emma at STSI.
        assert to == 'evalentine@stsigroup.com'
        assert cc == []

    def test_all_other_categories_go_to_default(self):
        from routes.support_request import get_routing_info
        for cat in ('candidate_parsing', 'job_posting', 'account_access',
                     'data_correction', 'feature_request', 'other'):
            to, cc = get_routing_info(cat, brand='STSI')
            assert to == 'kroots@myticas.com', (
                f"{cat} should route to the STSI default (kroots@myticas.com) "
                f"so Scout Support can triage it."
            )
            assert cc == []

    def test_cc_always_empty_for_stsi(self):
        """STSI CC list is intentionally empty across every category."""
        from routes.support_request import get_routing_info
        for cat in ('ats_issue', 'email_notifications', 'backoffice_onboarding',
                     'backoffice_finance', 'other'):
            _, cc = get_routing_info(cat, brand='STSI')
            assert cc == [], f"STSI '{cat}' must have empty CC list"

    def test_myticas_routing_unchanged(self):
        from routes.support_request import get_routing_info
        to, cc = get_routing_info('ats_issue', brand='Myticas')
        # Myticas default still goes to Dan; CC list is empty (Kyle is BCC'd
        # via _BCC_ALWAYS, not CC'd).
        assert to == 'dsifer@myticas.com'
        assert cc == []

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


class _InlineThread:
    """Test helper: replaces threading.Thread so .start() runs target inline.

    The /support/submit endpoint launches a daemon thread to call
    ScoutSupportService.create_ticket().  In tests, that thread may not have
    finished — or even started — by the time the request returns and the
    assertion runs, which would silently mask real regressions.  Substituting
    this class for threading.Thread makes execution deterministic: .start()
    invokes target() synchronously on the test thread, so any subsequent
    mock assertion sees the actual call.
    """
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


class TestSTSISubmission:
    """Submission flow tests for the /support/submit endpoint.

    UPDATED 2026-04-25 to reflect the post-March-24 architecture: support
    requests are no longer emailed synchronously through send_support_email.
    Instead, /support/submit hands the payload to ScoutSupportService in a
    daemon thread, which creates a Scout Support ticket and runs its own
    AI intake/notification logic.  The endpoint returns success as soon as
    the thread is started, so synchronous email assertions no longer apply.

    These tests verify that the endpoint accepts the payload, returns a
    successful JSON response, and routes the brand parameter into the
    Scout Support service correctly.  The downstream notification behavior
    is covered by ScoutSupportService's own tests, not here.

    SILENT-PASS GUARD: Each submission test patches threading.Thread with
    _InlineThread so the daemon target runs synchronously on the test
    thread.  This eliminates the race window between request return and
    background execution, allowing strict assert_called_once_with assertions
    that catch real regressions (e.g., a future refactor that drops the
    brand kwarg or stops calling create_ticket entirely).
    """

    def test_submit_stsi_request(self, client, app):
        """Endpoint accepts a valid STSI submission and reports success."""
        with app.app_context():
            with patch('threading.Thread', _InlineThread), \
                 patch('scout_support_service.ScoutSupportService') as MockSvc:
                instance = MagicMock()
                MockSvc.return_value = instance
                instance.create_ticket.return_value = MagicMock(id=1, ticket_number='TKT-001')

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
                assert 'submitted successfully' in data.get('message', '').lower()
                instance.create_ticket.assert_called_once()
                instance.process_new_ticket.assert_called_once_with(1)

    def test_submit_stsi_routes_brand_through_scout_support(self, client, app):
        """Verify the STSI brand flag reaches ScoutSupportService.create_ticket."""
        with app.app_context():
            with patch('threading.Thread', _InlineThread), \
                 patch('scout_support_service.ScoutSupportService') as MockSvc:
                instance = MagicMock()
                MockSvc.return_value = instance
                instance.create_ticket.return_value = MagicMock(id=2, ticket_number='TKT-002')

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

                instance.create_ticket.assert_called_once()
                call = instance.create_ticket.call_args
                assert call.kwargs.get('brand') == 'STSI', (
                    f"Expected brand='STSI' to reach ScoutSupportService; got "
                    f"kwargs={call.kwargs}"
                )

    def test_submit_myticas_routes_brand_correctly(self, client, app):
        """Myticas submissions pass brand='Myticas' through to ScoutSupportService."""
        with app.app_context():
            with patch('threading.Thread', _InlineThread), \
                 patch('scout_support_service.ScoutSupportService') as MockSvc:
                instance = MagicMock()
                MockSvc.return_value = instance
                instance.create_ticket.return_value = MagicMock(id=3, ticket_number='TKT-003')

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

                instance.create_ticket.assert_called_once()
                call = instance.create_ticket.call_args
                assert call.kwargs.get('brand') == 'Myticas', (
                    f"Expected brand='Myticas' to reach ScoutSupportService; got "
                    f"kwargs={call.kwargs}"
                )

    def test_submit_default_brand_is_myticas(self, client, app):
        """When no brand is provided, the endpoint defaults to Myticas."""
        with app.app_context():
            with patch('threading.Thread', _InlineThread), \
                 patch('scout_support_service.ScoutSupportService') as MockSvc:
                instance = MagicMock()
                MockSvc.return_value = instance
                instance.create_ticket.return_value = MagicMock(id=4, ticket_number='TKT-004')

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

                instance.create_ticket.assert_called_once()
                call = instance.create_ticket.call_args
                assert call.kwargs.get('brand') == 'Myticas', (
                    f"Default brand should be 'Myticas' when none provided; got "
                    f"kwargs={call.kwargs}"
                )


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

    def test_myticas_support_domain_redirects_to_microsoft_sso(self, app):
        """support.myticas.com is gated by Microsoft Entra ID SSO.

        UPDATED 2026-04-25: support.myticas.com no longer serves the support
        form directly to anonymous visitors.  Per replit.md, the Myticas
        support subdomain is protected by Microsoft Entra ID (Office 365)
        single sign-on.  Unauthenticated requests are redirected to
        /support/auth/login, which initiates the OAuth 2.0 flow.  STSI's
        support subdomain (support.stsigroup.com) remains open to the public
        because it has no Entra tenant attached.
        """
        with app.app_context():
            with app.test_client() as c:
                resp = c.get('/', headers={'Host': 'support.myticas.com'})
                assert resp.status_code == 302, (
                    "support.myticas.com should redirect anonymous visitors to "
                    "the Microsoft SSO login flow."
                )
                assert '/support/auth/login' in resp.headers.get('Location', ''), (
                    "Expected redirect to /support/auth/login (Microsoft Entra ID); "
                    f"got: {resp.headers.get('Location', '(no Location header)')}"
                )


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
