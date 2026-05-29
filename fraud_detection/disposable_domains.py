"""Curated static set of known disposable / temporary email domains.

Used by `fraud_detection.signals.evaluate_disposable_email` as a cheap,
deterministic fraud signal. Intentionally a hand-maintained frozenset (no
network dependency, no API cost). Add domains as they're observed in the wild.

Keep entries lowercase and bare (no leading '@'). Matching is exact on the
registered domain portion of the email address.
"""

DISPOSABLE_EMAIL_DOMAINS = frozenset({
    "mailinator.com",
    "guerrillamail.com",
    "guerrillamail.net",
    "guerrillamail.org",
    "sharklasers.com",
    "grr.la",
    "10minutemail.com",
    "10minutemail.net",
    "20minutemail.com",
    "tempmail.com",
    "temp-mail.org",
    "temp-mail.io",
    "tempmailo.com",
    "tempmail.net",
    "throwawaymail.com",
    "throwaway.email",
    "getnada.com",
    "nada.email",
    "dispostable.com",
    "yopmail.com",
    "yopmail.net",
    "yopmail.fr",
    "trashmail.com",
    "trashmail.net",
    "mailnesia.com",
    "maildrop.cc",
    "mailcatch.com",
    "fakeinbox.com",
    "fakemailgenerator.com",
    "spam4.me",
    "mintemail.com",
    "mohmal.com",
    "emailondeck.com",
    "tempinbox.com",
    "burnermail.io",
    "33mail.com",
    "anonbox.net",
    "mailtemp.net",
    "tmpmail.org",
    "tmpmail.net",
    "tmail.ws",
    "moakt.com",
    "tempr.email",
    "discard.email",
    "discardmail.com",
    "wegwerfmail.de",
    "einrot.com",
    "fleckens.hu",
    "spambog.com",
    "spambog.de",
    "spambog.ru",
    "mytemp.email",
    "luxusmail.org",
    "inboxbear.com",
    "instant-mail.de",
    "tempemail.co",
    "tempemail.net",
    "emltmp.com",
    "vomoto.com",
    "1secmail.com",
    "1secmail.org",
    "1secmail.net",
    "esiix.com",
    "wwjmp.com",
    "xojxe.com",
    "rteet.com",
    "dropmail.me",
    "fakemail.net",
    "cs.email",
    "harakirimail.com",
    "guerrillamailblock.com",
    "pokemail.net",
    "spamgourmet.com",
    "mailexpire.com",
    "jetable.org",
    "minuteinbox.com",
    "tempail.com",
    "emailfake.com",
    "email-fake.com",
    "fakermail.com",
    "mailpoof.com",
    "smailpro.com",
    "tempmailaddress.com",
    "tempemails.io",
    "throwawaymails.com",
    "mvrht.net",
    "byom.de",
})


def extract_domain(email: str) -> str:
    """Return the lowercased domain portion of an email address, or ''.

    Tolerant of surrounding whitespace and missing '@'. Does not validate
    full RFC syntax — that's handled separately by the contact-anomaly check.
    """
    if not email or "@" not in email:
        return ""
    return email.strip().rsplit("@", 1)[-1].strip().lower()


def is_disposable_domain(email: str) -> bool:
    """True when the email's domain is in the curated disposable set."""
    return extract_domain(email) in DISPOSABLE_EMAIL_DOMAINS
