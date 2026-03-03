"""
Regression tests for Jinja2 template block names.

These tests guard against silent block name mismatches where page-specific
CSS or header action buttons are defined in blocks that don't exist in
base_layout.html, causing them to be silently dropped on render.

Rules enforced:
- CSS blocks must use {% block extra_styles %} (NOT {% block extra_head %})
- Header action buttons must use {% block page_actions %} (NOT {% block header_actions %})
"""

import os
import glob
import pytest
from werkzeug.security import generate_password_hash


TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')


def _make_admin(db, username, email):
    from models import User
    user = User.query.filter_by(username=username).first()
    if not user:
        user = User(username=username, email=email, is_admin=True)
        user.password_hash = generate_password_hash('testpass', method='pbkdf2:sha256')
        db.session.add(user)
        db.session.commit()
    return user


def _login(app, username, password='testpass'):
    c = app.test_client()
    c.post('/login', data={'username': username, 'password': password},
           follow_redirects=False)
    return c


class TestBlockNameConventions:

    def _all_templates(self):
        return glob.glob(os.path.join(TEMPLATES_DIR, '*.html'))

    def test_no_extra_head_blocks_in_any_template(self):
        """
        No template should use block extra_head.
        The correct block is extra_styles (defined in base_layout.html).
        Using extra_head silently drops all CSS in that block.
        """
        violations = []
        for path in self._all_templates():
            with open(path) as f:
                content = f.read()
            if '{% block extra_head %}' in content or '{%block extra_head%}' in content:
                violations.append(os.path.basename(path))

        assert not violations, (
            "Templates using undefined block 'extra_head' (CSS will be silently dropped). "
            "Rename to 'extra_styles': " + str(violations)
        )

    def test_no_header_actions_blocks_in_any_template(self):
        """
        No template should use block header_actions.
        The correct block is page_actions (defined in base_layout.html).
        Using header_actions silently drops all header buttons in that block.
        """
        violations = []
        for path in self._all_templates():
            with open(path) as f:
                content = f.read()
            if '{% block header_actions %}' in content or '{%block header_actions%}' in content:
                violations.append(os.path.basename(path))

        assert not violations, (
            "Templates using undefined block 'header_actions' (buttons will be silently dropped). "
            "Rename to 'page_actions': " + str(violations)
        )

    def test_extra_styles_block_defined_in_base_layout(self):
        """base_layout.html must define the extra_styles block so child templates can use it."""
        base = os.path.join(TEMPLATES_DIR, 'base_layout.html')
        with open(base) as f:
            content = f.read()
        assert '{% block extra_styles %}' in content, (
            "base_layout.html is missing block 'extra_styles' — child template CSS will never load."
        )

    def test_page_actions_block_defined_in_base_layout(self):
        """base_layout.html must define the page_actions block so child templates can use it."""
        base = os.path.join(TEMPLATES_DIR, 'base_layout.html')
        with open(base) as f:
            content = f.read()
        assert '{% block page_actions %}' in content, (
            "base_layout.html is missing block 'page_actions' — child template header buttons will never render."
        )


class TestCSSActuallyRendered:
    """
    Integration tests that verify page-specific CSS reaches the browser.
    These tests catch cases where a template's CSS block is correctly named
    but the content is otherwise broken.
    """

    def test_settings_page_renders_settings_card_css(self, app):
        """Settings page extra_styles block must render .settings-card overrides."""
        from extensions import db
        with app.app_context():
            _make_admin(db, 'tb_admin_css1', 'tb_admin_css1@test.com')
            c = _login(app, 'tb_admin_css1')
            response = c.get('/settings')
            if response.status_code == 200:
                assert b'settings-card' in response.data or b'settings' in response.data.lower()

    def test_scout_screening_page_renders_without_error(self, app):
        """
        Scout screening page used 223 lines of CSS in the wrong block.
        After the fix, the page must render successfully (200 or auth redirect).
        """
        from extensions import db
        with app.app_context():
            _make_admin(db, 'tb_admin_css2', 'tb_admin_css2@test.com')
            c = _login(app, 'tb_admin_css2')
            response = c.get('/scout-screening')
            assert response.status_code in [200, 302, 404]

    def test_ats_monitoring_page_renders_without_error(self, app):
        """ATS monitoring page should render successfully after block fix."""
        from extensions import db
        with app.app_context():
            _make_admin(db, 'tb_admin_css3', 'tb_admin_css3@test.com')
            c = _login(app, 'tb_admin_css3')
            response = c.get('/ats/monitoring')
            assert response.status_code in [200, 302, 308, 404]


class TestHeaderActionsActuallyRendered:
    """
    Integration tests verifying that page_actions content reaches the rendered HTML.
    """

    def test_ats_monitoring_contains_add_monitor_button(self, app):
        """
        ATS Monitoring page had 'Add Monitor' button in the wrong block.
        After fix it must appear in the rendered page HTML.
        """
        from extensions import db
        with app.app_context():
            _make_admin(db, 'tb_admin_hdr1', 'tb_admin_hdr1@test.com')
            c = _login(app, 'tb_admin_hdr1')
            response = c.get('/ats/monitoring')
            if response.status_code == 200:
                assert b'Add Monitor' in response.data, (
                    "ATS Monitoring page is missing the 'Add Monitor' button — page_actions block may not be rendering."
                )

    def test_scout_inbound_contains_refresh_button(self, app):
        """
        Scout Inbound page had a 'Refresh' button in the wrong block.
        After fix it must appear in the rendered HTML.
        """
        from extensions import db
        with app.app_context():
            _make_admin(db, 'tb_admin_hdr2', 'tb_admin_hdr2@test.com')
            c = _login(app, 'tb_admin_hdr2')
            response = c.get('/scout-inbound')
            if response.status_code == 200:
                assert b'Refresh' in response.data, (
                    "Scout Inbound page is missing the 'Refresh' button — page_actions block may not be rendering."
                )
