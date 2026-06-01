"""Curated static set of free / personal email providers.

Used by `fraud_detection.signals` to distinguish a *personal* email address
(free consumer webmail — the norm for a real candidate applying on their own)
from a *non-personal* one (a corporate/agency/custom domain). On its own this
is NOT a fraud signal — plenty of legitimate candidates use a work address.
It only matters as one half of the "third-party submission" composite, where a
truncated name (first + single initial / first-only) paired with a non-personal
domain is the classic agency-submitted-shell-profile pattern.

Intentionally a hand-maintained frozenset (no network dependency, no API cost).
Keep entries lowercase and bare (no leading '@'). Matching is exact on the
registered domain portion of the email address.
"""

from fraud_detection.disposable_domains import extract_domain

# Free consumer webmail / ISP-mailbox domains. A candidate using one of these is
# the expected, benign case. Anything NOT in this set (and not disposable) is
# treated as "non-personal" (corporate / agency / custom domain).
FREE_EMAIL_PROVIDERS = frozenset({
    # Google
    "gmail.com", "googlemail.com",
    # Microsoft
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "hotmail.co.uk", "outlook.co.uk", "live.co.uk", "hotmail.fr",
    "outlook.fr", "live.fr", "hotmail.it", "hotmail.de", "live.de",
    "hotmail.es", "outlook.es",
    # Yahoo / AOL / Verizon family
    "yahoo.com", "yahoo.co.uk", "yahoo.ca", "yahoo.co.in", "yahoo.in",
    "yahoo.fr", "yahoo.de", "yahoo.es", "yahoo.com.au", "ymail.com",
    "rocketmail.com", "aol.com", "aim.com",
    # Apple
    "icloud.com", "me.com", "mac.com",
    # Privacy-focused
    "proton.me", "protonmail.com", "pm.me", "tutanota.com", "tuta.io",
    # Other global free webmail
    "gmx.com", "gmx.net", "gmx.de", "gmx.us", "mail.com", "email.com",
    "yandex.com", "yandex.ru", "zoho.com", "zohomail.com",
    "fastmail.com", "hushmail.com", "hey.com",
    # Common ISP mailboxes (US/CA/UK)
    "comcast.net", "verizon.net", "att.net", "sbcglobal.net", "bellsouth.net",
    "cox.net", "charter.net", "earthlink.net", "rogers.com", "shaw.ca",
    "sympatico.ca", "bell.net", "telus.net", "btinternet.com", "sky.com",
    "ntlworld.com", "virginmedia.com",
})


def is_free_provider(email: str) -> bool:
    """True when the email's domain is a known free / personal webmail provider."""
    return extract_domain(email) in FREE_EMAIL_PROVIDERS


def is_personal_email(email: str) -> bool:
    """True when the address looks like a personal mailbox.

    Personal == a known free webmail provider. A non-personal address is any
    other deliverable domain (corporate / agency / custom). Disposable domains
    are intentionally NOT treated as personal — they are handled by their own
    dedicated disposable-email signal and should not soften the third-party
    composite. An empty / malformed address returns False (cannot confirm
    personal) so the composite stays conservative.
    """
    return is_free_provider(email)
