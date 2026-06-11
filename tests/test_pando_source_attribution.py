"""Tests for PandoLogic-distributed apply-form attribution.

~27% of applicants reach our own apply form via PandoLogic distribution
(e.g. TheJobNetwork). They carry the apply URL's hardcoded ?source=LinkedIn
param, so without intervention they were mislabeled "LinkedIn" in Bullhorn.

The fix keys off the reliable `feed=pando` discriminator (passed through the
apply form, extracted on inbound) rather than the unreliable source string:
when feed indicates PandoLogic the Bullhorn source is set to "Corporate
Website" (they applied on our form) and the candidate owner is set to the
PandoLogic API user (captures the distribution channel). Genuine recognized
referrers (LinkedIn/Indeed/Facebook) do not carry feed=pando, so they keep
their true source and are not given the PandoLogic owner.
"""
import pytest

import logging

import models
from email_inbound_service._core import _InboundCore
from email_inbound_service.ai_mixin import AIMixin
from email_inbound_service.extraction_mixin import ExtractionMixin
from email_inbound_service.resume_mixin import ResumeMixin


class _Harness(ResumeMixin, AIMixin, ExtractionMixin, _InboundCore):
    """Minimal combination exposing map_to_bullhorn_fields /
    _build_enrichment_update / detect_feed without the OpenAI client init
    that _InboundCore.__init__ performs."""

    def __init__(self):  # noqa: D401 - intentionally skips OpenAI setup
        self.logger = logging.getLogger('test_pando')


@pytest.fixture
def mapper():
    return _Harness()


@pytest.fixture
def basic_email():
    return {'first_name': 'Ada', 'last_name': 'Lovelace', 'email': 'ada@example.com'}


@pytest.fixture
def patch_pando_owner(monkeypatch):
    def _set(value):
        monkeypatch.setattr(
            models.VettingConfig,
            'get_value',
            classmethod(lambda cls, key, default=None: value),
        )
    return _set


# ── _is_pando_feed -----------------------------------------------------------

@pytest.mark.parametrize('value', ['pando', 'pandologic', 'Pando', ' PANDOLOGIC ', 'pando_xyz'])
def test_is_pando_feed_true(value):
    assert ResumeMixin._is_pando_feed(value) is True


@pytest.mark.parametrize('value', ['', None, 'linkedin', 'indeed', '-', 'corporate'])
def test_is_pando_feed_false(value):
    assert ResumeMixin._is_pando_feed(value) is False


# ── _pando_owner_id ----------------------------------------------------------

def test_pando_owner_id_numeric(patch_pando_owner):
    patch_pando_owner('4582033')
    assert ResumeMixin._pando_owner_id() == 4582033


def test_pando_owner_id_unset_returns_none(patch_pando_owner):
    patch_pando_owner(None)
    assert ResumeMixin._pando_owner_id() is None


def test_pando_owner_id_non_numeric_returns_none(patch_pando_owner):
    patch_pando_owner('not-a-number')
    assert ResumeMixin._pando_owner_id() is None


# ── map_to_bullhorn_fields ---------------------------------------------------

def test_corporate_website_is_a_known_source(mapper):
    assert mapper.SOURCE_TO_BULLHORN.get('Corporate Website') == 'Corporate Website'


def test_pando_feed_overrides_source_and_sets_owner(mapper, basic_email, patch_pando_owner):
    patch_pando_owner('4582033')
    # Incoming source is the hardcoded-param mislabel; feed reveals PandoLogic.
    candidate = mapper.map_to_bullhorn_fields(
        basic_email, {}, 'LinkedIn Job Board', feed='pandologic'
    )
    assert candidate['source'] == 'Corporate Website'
    assert candidate['owner'] == {'id': 4582033}


def test_pando_feed_short_token(mapper, basic_email, patch_pando_owner):
    patch_pando_owner('4582033')
    candidate = mapper.map_to_bullhorn_fields(
        basic_email, {}, 'LinkedIn Job Board', feed='pando'
    )
    assert candidate['source'] == 'Corporate Website'
    assert candidate['owner'] == {'id': 4582033}


def test_pando_feed_missing_owner_still_sets_source(mapper, basic_email, patch_pando_owner):
    patch_pando_owner(None)
    candidate = mapper.map_to_bullhorn_fields(
        basic_email, {}, 'LinkedIn Job Board', feed='pando'
    )
    assert candidate['source'] == 'Corporate Website'
    assert 'owner' not in candidate


def test_recognized_referrer_without_pando_keeps_true_source(mapper, basic_email):
    candidate = mapper.map_to_bullhorn_fields(
        basic_email, {}, 'Indeed Job Board', feed=''
    )
    assert candidate['source'] == 'Indeed Job Board'
    assert 'owner' not in candidate


def test_no_feed_keeps_true_source(mapper, basic_email):
    candidate = mapper.map_to_bullhorn_fields(
        basic_email, {}, 'LinkedIn Job Board'
    )
    assert candidate['source'] == 'LinkedIn Job Board'
    assert 'owner' not in candidate


# ── detect_feed: must survive the HTML apply email -----------------------------

# The apply-form email is HTML; inbound processes the HTML body where the
# "Feed:" label and its value sit in SEPARATE <td> cells. A naive
# `Feed:\s*value` regex only matches the plain-text body, which is why the
# owner override never fired in production until detect_feed learned to strip
# tags.
_HTML_FEED_BODY = (
    '<table><tr>'
    '<td style="font-weight:bold;">Feed:</td>'
    '<td style="color:#333;">pando</td>'
    '</tr></table>'
)
_HTML_FEED_SENTINEL = (
    '<tr><td>Feed:</td><td>-</td></tr>'
)


def test_detect_feed_html_cell_split(mapper):
    """The real production failure: HTML body with Feed label/value split."""
    assert mapper.detect_feed(_HTML_FEED_BODY) == 'pando'


def test_detect_feed_plain_text(mapper):
    assert mapper.detect_feed('Source: Corporate Website\nFeed: pando\n') == 'pando'


def test_detect_feed_html_sentinel_is_empty(mapper):
    assert mapper.detect_feed(_HTML_FEED_SENTINEL) == ''


def test_detect_feed_absent_is_empty(mapper):
    assert mapper.detect_feed('<p>Some unrelated job-board forward</p>') == ''


def test_detect_feed_blank_is_empty(mapper):
    assert mapper.detect_feed('') == ''


# ── enrichment: correct pando attribution on RETURNING candidates -------------
# The duplicate/recovery paths run map output through enrichment. For a
# PandoLogic application (new_data carries an owner), the attribution source is
# always corrected, and ownership is reassigned ONLY when the existing owner is
# an automated API user — never a human recruiter.

def test_enrichment_human_owner_keeps_owner_but_corrects_source(mapper):
    existing = {
        'source': 'LinkedIn',
        'owner': {'id': 999999},  # a human recruiter
        'phone': '555-1111',
    }
    new_data = {
        'source': 'Corporate Website',
        'owner': {'id': 4582033},
        'occupation': 'Engineer',
    }
    enriched = mapper._build_enrichment_update(existing, new_data)
    # source corrected, but the human owner is left untouched
    assert enriched.get('source') == 'Corporate Website'
    assert 'owner' not in enriched
    # sanity: genuinely-missing fields still enrich
    assert enriched.get('occupation') == 'Engineer'


def test_enrichment_api_user_owner_gets_reassigned(mapper):
    existing = {
        'source': 'LinkedIn',
        'owner': {'id': 1147490},  # Myticas API user
    }
    new_data = {
        'source': 'Corporate Website',
        'owner': {'id': 4582033},
    }
    enriched = mapper._build_enrichment_update(existing, new_data)
    assert enriched.get('source') == 'Corporate Website'
    assert enriched.get('owner') == {'id': 4582033}


def test_enrichment_unowned_candidate_gets_pando_owner(mapper):
    existing = {'source': 'LinkedIn'}  # no owner
    new_data = {'source': 'Corporate Website', 'owner': {'id': 4582033}}
    enriched = mapper._build_enrichment_update(existing, new_data)
    assert enriched.get('owner') == {'id': 4582033}


def test_enrichment_non_pando_never_touches_source_or_owner(mapper):
    """A non-PandoLogic application (no owner in new_data) must never alter an
    existing candidate's source or owner."""
    existing = {'source': 'LinkedIn', 'owner': {'id': 1147490}}
    new_data = {'source': 'Indeed Job Board', 'occupation': 'Engineer'}
    enriched = mapper._build_enrichment_update(existing, new_data)
    assert 'source' not in enriched
    assert 'owner' not in enriched
    assert enriched.get('occupation') == 'Engineer'


def test_enrichment_pando_source_already_correct_skips_source(mapper):
    """If the existing source is already Corporate Website, don't re-write it,
    but still reassign an API-user owner."""
    existing = {'source': 'Corporate Website', 'owner': {'id': 1147490}}
    new_data = {'source': 'Corporate Website', 'owner': {'id': 4582033}}
    enriched = mapper._build_enrichment_update(existing, new_data)
    assert 'source' not in enriched
    assert enriched.get('owner') == {'id': 4582033}


# ── submit_application glue: pando referrer -> feed + Corporate Website --------

class _FakeSendGridResponse:
    status_code = 202
    headers = {}
    body = b''


def test_submit_application_pando_referrer_tags_feed_and_source(monkeypatch):
    """End-to-end glue on the apply form: a PandoLogic referrer (TheJobNetwork)
    with the hardcoded ?source=LinkedIn param and NO explicit feed must make the
    outbound email carry feed='pando' and source 'Corporate Website', so the
    existing inbound pipeline routes Bullhorn ownership to the Pando API user."""
    from job_application_service import JobApplicationService

    svc = JobApplicationService()

    captured = {}
    monkeypatch.setattr(
        svc, '_build_application_email_html',
        lambda data, is_stsi=False: captured.update(html=dict(data)) or '')
    monkeypatch.setattr(
        svc, '_build_application_email_text',
        lambda data, is_stsi=False: captured.update(text=dict(data)) or '')
    monkeypatch.setattr(svc, '_create_logo_attachment', lambda is_stsi: None)
    monkeypatch.setattr(svc, '_create_attachment', lambda f, kind: None)
    monkeypatch.setattr(svc, '_close_apply_visit', lambda **kw: None)
    monkeypatch.setattr(svc, '_check_and_clear_suppression', lambda *a, **k: None)

    class _FakeSG:
        def send(self, message):
            return _FakeSendGridResponse()

    svc.sg = _FakeSG()
    svc.sendgrid_api_key = 'test-key'

    application_data = {
        'firstName': 'Ada', 'lastName': 'Lovelace',
        'email': 'ada@example.com', 'phone': '555-1234',
        'jobId': '34613', 'jobTitle': 'Engineer',
        'source': 'LinkedIn',   # hardcoded apply-URL default
        'feed': '',             # PandoLogic does not preserve ?feed=pando
        'referrer': 'https://myticasconsulting.thejobnetwork.com/job/1',
        'utm_source': '',
        'visit_token': '',      # empty -> skip the ApplyPageVisit DB lookup
    }

    result = svc.submit_application(application_data, resume_file=object())

    assert result.get('success') is True
    assert captured['html']['feed'] == 'pando'
    assert captured['html']['source'] == 'Corporate Website'
    assert captured['text']['feed'] == 'pando'


def test_submit_application_real_linkedin_referrer_unchanged(monkeypatch):
    """Guard: a genuine LinkedIn referrer must NOT be tagged pando — feed stays
    empty and source resolves to the real channel."""
    from job_application_service import JobApplicationService

    svc = JobApplicationService()

    captured = {}
    monkeypatch.setattr(
        svc, '_build_application_email_html',
        lambda data, is_stsi=False: captured.update(html=dict(data)) or '')
    monkeypatch.setattr(svc, '_build_application_email_text',
                        lambda data, is_stsi=False: '')
    monkeypatch.setattr(svc, '_create_logo_attachment', lambda is_stsi: None)
    monkeypatch.setattr(svc, '_create_attachment', lambda f, kind: None)
    monkeypatch.setattr(svc, '_close_apply_visit', lambda **kw: None)
    monkeypatch.setattr(svc, '_check_and_clear_suppression', lambda *a, **k: None)

    class _FakeSG:
        def send(self, message):
            return _FakeSendGridResponse()

    svc.sg = _FakeSG()
    svc.sendgrid_api_key = 'test-key'

    application_data = {
        'firstName': 'Ada', 'lastName': 'Lovelace',
        'email': 'ada@example.com', 'phone': '555-1234',
        'jobId': '34613', 'jobTitle': 'Engineer',
        'source': 'LinkedIn',
        'feed': '',
        'referrer': 'https://www.linkedin.com/jobs/view/1',
        'utm_source': '',
        'visit_token': '',
    }

    result = svc.submit_application(application_data, resume_file=object())

    assert result.get('success') is True
    assert captured['html']['feed'] == ''
    assert captured['html']['source'] == 'LinkedIn Job Board'
