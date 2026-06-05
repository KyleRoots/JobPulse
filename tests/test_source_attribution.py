"""Tests for dynamic apply-page source attribution (source_attribution.py).

These are pure-function tests — no app context or DB needed. They lock in the
resolver precedence that makes attribution independent of the vendors: the
browser referrer (browser truth) must win over the hardcoded ?source=LinkedIn
param that every published apply URL carries.
"""
from source_attribution import (
    normalize_source_value,
    referrer_host,
    source_from_referrer,
    resolve_source,
)


def test_normalize_known_variants():
    assert normalize_source_value('indeed') == 'Indeed Job Board'
    assert normalize_source_value('Indeed') == 'Indeed Job Board'
    assert normalize_source_value('indeed.com') == 'Indeed Job Board'
    assert normalize_source_value('Indeed Job Board') == 'Indeed Job Board'
    assert normalize_source_value('careerbuilder') == 'CareerBuilder'
    assert normalize_source_value('ZipRecruiter') == 'ZipRecruiter Job Board'


def test_normalize_unknown_and_generic_returns_empty():
    assert normalize_source_value('') == ''
    assert normalize_source_value(None) == ''
    assert normalize_source_value('Website') == ''
    assert normalize_source_value('Direct') == ''
    assert normalize_source_value('some-random-site') == ''


def test_referrer_host_strips_www():
    assert referrer_host('https://www.indeed.com/viewjob?jk=1') == 'indeed.com'
    assert referrer_host('https://linkedin.com/jobs') == 'linkedin.com'
    assert referrer_host('') == ''


def test_referrer_internal_and_pando_are_not_sources():
    # Our own domains must never be treated as a channel.
    assert source_from_referrer('https://apply.myticas.com/123/x/') == ''
    assert source_from_referrer('https://apply.stsigroup.com/123/x/') == ''
    assert source_from_referrer('https://app.scoutgenius.ai/') == ''
    # PandoLogic is a redirect middle-man that masks the true origin.
    assert source_from_referrer('https://click.pandologic.com/redir?x') == ''


def test_referrer_beats_hardcoded_linkedin_param():
    # The core win: every apply URL hardcodes ?source=LinkedIn, but an Indeed
    # referrer must override it to the real channel.
    assert resolve_source('LinkedIn', 'https://www.indeed.com/viewjob?jk=1', '') == 'Indeed Job Board'
    assert resolve_source('LinkedIn', 'https://www.dice.com/jobs', '') == 'Dice'


def test_falls_back_to_param_when_referrer_unusable():
    # Referrer stripped (privacy) -> use the param default.
    assert resolve_source('LinkedIn', '', '') == 'LinkedIn Job Board'
    # Internal referrer -> param.
    assert resolve_source('LinkedIn', 'https://apply.myticas.com/1/x/', '') == 'LinkedIn Job Board'
    # PandoLogic-masked referrer -> param.
    assert resolve_source('LinkedIn', 'https://click.pandologic.com/r', '') == 'LinkedIn Job Board'


def test_utm_used_between_referrer_and_param():
    # Unknown referrer, but a real utm_source present -> utm wins over the param.
    assert resolve_source('LinkedIn', 'https://mail.google.com/', 'indeed') == 'Indeed Job Board'


def test_unknown_everything_returns_empty():
    # Nothing recognizable -> '' so the caller keeps its own legacy fallback.
    assert resolve_source('Website', '', '') == ''
    assert resolve_source('', '', '') == ''
