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
from email_inbound_service.resume_mixin import ResumeMixin


class _Harness(ResumeMixin, AIMixin, _InboundCore):
    """Minimal combination exposing map_to_bullhorn_fields /
    _build_enrichment_update without the OpenAI client init that
    _InboundCore.__init__ performs."""

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


# ── enrichment must never stomp an existing candidate's owner/source ----------

def test_enrichment_never_overwrites_existing_owner_or_source(mapper):
    """The duplicate/recovery paths run map output through enrichment, which is
    the load-bearing guard: a Pando override on a NEW candidate must not later
    overwrite an EXISTING candidate's owner/source. Lock that 'source' and
    'owner' are not enrichable, even when the mapped payload carries them."""
    existing = {
        'source': 'LinkedIn',
        'owner': {'id': 999999},
        'phone': '555-1111',
    }
    new_data = {
        'source': 'Corporate Website',
        'owner': {'id': 4582033},
        'occupation': 'Engineer',
    }
    enriched = mapper._build_enrichment_update(existing, new_data)
    assert 'source' not in enriched
    assert 'owner' not in enriched
    # sanity: genuinely-missing fields still enrich
    assert enriched.get('occupation') == 'Engineer'
