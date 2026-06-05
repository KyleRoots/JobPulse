"""Dynamic source attribution for the public apply page.

Resolves the true application channel on OUR side instead of trusting a vendor
to append a per-channel ``?source=`` param. Every published apply URL hardcodes
``?source=LinkedIn`` (see ``xml_integration_service.mapping``), so that param is
NOT a reliable per-vendor signal. The browser *referrer* captured when the
candidate first lands on the apply page is the real origin signal.

Priority (best-signal-wins):
    1. referrer host  -> mapped to a canonical Bullhorn source (browser truth)
    2. utm_source     -> normalized (future-proofing if a vendor adds one)
    3. explicit ?source= param -> normalized (only the hardcoded LinkedIn default today)
    4. ''             -> undetermined; caller keeps its own fallback

All non-empty outputs are canonical Bullhorn source picklist values that the
inbound parser already recognizes (see
``email_inbound_service._core.SOURCE_TO_BULLHORN``).
"""
from urllib.parse import urlparse

# Canonical Bullhorn source values, keyed by a normalized token.
# Keep values in sync with SOURCE_TO_BULLHORN in email_inbound_service/_core.py.
_CANONICAL = {
    'indeed': 'Indeed Job Board',
    'linkedin': 'LinkedIn Job Board',
    'ziprecruiter': 'ZipRecruiter Job Board',
    'dice': 'Dice',
    'glassdoor': 'Glassdoor',
    'monster': 'Monster',
    'careerbuilder': 'CareerBuilder',
    'facebook': 'Facebook',
    'twitter': 'Twitter',
}

# Substrings that may appear in a referrer hostname or a raw source string,
# mapped to the normalized token above.
_TOKEN_ALIASES = {
    'indeed': 'indeed',
    'linkedin': 'linkedin',
    'lnkd.in': 'linkedin',
    'ziprecruiter': 'ziprecruiter',
    'ziprecruit': 'ziprecruiter',
    'dice': 'dice',
    'glassdoor': 'glassdoor',
    'monster': 'monster',
    'careerbuilder': 'careerbuilder',
    'career builder': 'careerbuilder',
    'facebook': 'facebook',
    'fb.com': 'facebook',
    'twitter': 'twitter',
    'x.com': 'twitter',
    't.co': 'twitter',
}

# Hostnames that are OUR OWN / non-source middle-men: never treat as a channel.
# PandoLogic is a redirect aggregator that masks the true origin (documented
# wrinkle) — we deliberately do NOT map it to a source, so a pandologic referrer
# falls through to the next-best signal rather than mis-attributing.
_NON_SOURCE_HOST_SUBSTRINGS = (
    'myticas.com',
    'stsigroup.com',
    'stsi.com',
    'scoutgenius.ai',
    'pandologic',
    'pandolytics',
)


def _norm_token(raw):
    """Reduce a raw string to a known canonical token, or '' if unrecognized."""
    if not raw:
        return ''
    s = str(raw).strip().lower()
    if not s:
        return ''
    if s in _CANONICAL:
        return s
    for alias, token in _TOKEN_ALIASES.items():
        if alias in s:
            return token
    return ''


def normalize_source_value(raw):
    """Map a messy raw source string to a canonical Bullhorn source value.

    'indeed', 'Indeed', 'indeed.com', 'Indeed Job Board' -> 'Indeed Job Board'.
    Returns '' when nothing recognizable is present.
    """
    return _CANONICAL.get(_norm_token(raw), '')


def referrer_host(referrer):
    """Parse the lowercase hostname (sans leading 'www.') from a referrer URL."""
    if not referrer:
        return ''
    try:
        netloc = urlparse(str(referrer).strip()).netloc.lower()
    except Exception:
        return ''
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    return netloc


def source_from_referrer(referrer):
    """Canonical Bullhorn source inferred from the browser referrer host.

    Returns '' for blank, internal, or non-source (PandoLogic) referrers, or any
    host we don't recognize as a known job board.
    """
    host = referrer_host(referrer)
    if not host:
        return ''
    for bad in _NON_SOURCE_HOST_SUBSTRINGS:
        if bad in host:
            return ''
    return normalize_source_value(host)


def resolve_source(explicit_source='', referrer='', utm_source=''):
    """Resolve the best-available canonical Bullhorn source.

    Priority: referrer host (browser truth) > utm_source > explicit ?source=
    param. The published apply URLs hardcode ?source=LinkedIn for every channel,
    so the explicit param is the WEAKEST signal and is only used when the
    referrer and utm give us nothing. Returns '' when undetermined (caller keeps
    its own fallback).
    """
    ref = source_from_referrer(referrer)
    if ref:
        return ref
    utm = normalize_source_value(utm_source)
    if utm:
        return utm
    return normalize_source_value(explicit_source)
