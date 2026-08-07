"""
Candidate Name Extraction Helpers

Deterministic helpers used by the inbound email parser as fallbacks when
the primary subject/body regex and the AI resume parser both fail to
recover a candidate name.

The goal of this module is "no silent drops": when standard board emails
arrive in unusual formats (3+ token names, hyphenated names, name
particles, mixed casing, generic resume filenames) we still want a
deterministic chance to extract the candidate's name before falling
through to the last-resort AI call or admin notification.

All public helpers are pure functions (no I/O, no DB, no network).
"""
from __future__ import annotations

import os
import re
from typing import Dict, Optional, Tuple

NAME_PARTICLES = {
    "van", "von", "der", "den", "de", "del", "della", "di", "da", "du",
    "la", "le", "el", "al", "bin", "ben", "ibn", "abu", "mac", "mc",
    "san", "santa", "st", "st.", "ter", "ten", "los", "las", "do",
    "dos", "das",
}

GENERIC_FILENAME_TOKENS = {
    "resume", "cv", "curriculum", "vitae", "profile", "candidate",
    "applicant", "application", "updated", "new", "final", "latest",
    "current", "doc", "document", "file", "copy", "untitled",
    "myresume", "my", "personal",
}

INVALID_NAME_TOKENS = GENERIC_FILENAME_TOKENS | {
    "none", "null", "n/a", "na", "unknown", "anonymous", "test",
    "candidate", "applicant",
}

# Tokens that, when present in a (first, last) pair, indicate a
# work-authorization or citizenship phrase rather than a real candidate
# name. Production failure: "Canadian Citizen" was extracted from a
# resume header line below the actual name and shipped to Bullhorn as
# firstName="Canadian" / lastName="Citizen". Any name containing one of
# these tokens (case-insensitive, exact whitespace/hyphen-split match)
# is rejected by ``is_valid_name``.
#
# Tokens here MUST be unambiguously work-authorization vocabulary —
# words that are essentially zero-probability as a person's legal name
# token. Ambiguous words like "Green" (a real surname — Eva Green, John
# Green) and "Permanent" are intentionally NOT in this set; they are
# only rejected when they appear in a known work-auth phrase, see
# :data:`WORK_AUTH_PHRASES` below.
WORK_AUTH_TOKENS = {
    "citizen", "citizens", "citizenship",
    "resident", "residency", "residents",
    "naturalized",
    "visa", "visas",
    "h1b", "h-1b", "h1-b",
    "ead", "opt", "cpt",
    "asylee", "asylum", "refugee",
}

# Multi-word work-authorization phrases. Matched as a substring inside
# the lowercased, whitespace-normalized "first last" string so that
# ambiguous single tokens like "green" and "permanent" still trigger
# rejection in their work-auth form ("green card", "permanent resident")
# without rejecting legitimate surnames like "Green" or "Permanent".
WORK_AUTH_PHRASES = {
    "green card",
    "green card holder",
    "permanent resident",
    "permanent residency",
    "permanent residents",
    "work permit",
    "work authorization",
    "work authorized",
    "work eligible",
    "authorized to work",
    "eligible to work",
    "right to work",
    "lawful permanent",
}

# Two-word call-to-action phrases scraped from job-board email bodies
# (LinkedIn, Indeed, Dice, ZipRecruiter, etc.) that have leaked into
# candidate name fields in production. Production failure: LinkedIn
# application emails contain an "Invite a Friend" CTA in the email
# footer; a fallback name extractor captured "Invite Friend" and shipped
# it to Bullhorn as firstName="Invite" / lastName="Friend" for two
# different real candidates (#3822915 Sujatha Devineni, #3817209 Sai
# Charan Mittapalli) across multiple re-applications.
#
# Matched as a full-pair check inside ``is_valid_name`` AND as a
# substring on the lowercased, whitespace-normalised "first last"
# string so variants like "Invite a Friend" and "Apply Now Here" are
# caught even when intermediate tokens are stripped.
#
# Single tokens like "apply", "click", "view" are too ambiguous to
# blanket-reject on their own (e.g. surname "Click" exists) — they only
# trigger rejection when they appear AS PART OF a known CTA phrase.
CTA_PHRASES = {
    "invite friend",
    "invite a friend",
    "apply now",
    "apply here",
    "apply today",
    "easy apply",
    "apply on",
    "apply via",
    "quick apply",
    "view profile",
    "view candidate",
    "view application",
    "view resume",
    "view job",
    "view details",
    "view all",
    "see profile",
    "see candidate",
    "see resume",
    "see more",
    "see full",
    "full application",
    "click here",
    "click below",
    "click apply",
    "learn more",
    "get started",
    "sign up",
    "sign in",
    "log in",
    "save job",
    "save this",
    "share job",
    "share this",
    "follow company",
    "follow us",
    "connect now",
    "message candidate",
    "message recruiter",
    "download resume",
    "download app",
    "open app",
    "unsubscribe here",
    "manage preferences",
    "full profile",
    "read more",
    "find jobs",
    "find candidates",
    "post job",
    "no reply",
    "noreply",
    "do not",
    "team scout",
    "scout genius",
}

# Job-title vocabulary. Production failure (2026-08-06): LinkedIn apply-form
# submitted firstName="Senior" / lastName="Business Analyst" (the candidate's
# occupation) while the résumé AI correctly extracted "Uday Vasireddy". Email
# subject preference then shipped the title to Bullhorn as the candidate name
# (#4673968). These tokens/phrases reject title-shaped (first, last) pairs
# without blocking rare real surnames like "Senior" alone ("John Senior").
JOB_TITLE_SENIORITY_TOKENS = {
    "senior", "junior", "principal", "staff", "lead", "sr", "jr",
    "associate", "entry", "mid", "midlevel", "mid-level",
}

JOB_TITLE_ROLE_TOKENS = {
    "analyst", "engineer", "developer", "manager", "architect",
    "consultant", "specialist", "director", "coordinator",
    "administrator", "admin", "officer", "designer", "scientist",
    "programmer", "technician", "recruiter", "accountant",
    "executive", "president", "founder", "intern", "trainee",
    "assistant", "supervisor", "strategist", "owner", "lead",
    "sme", "ba", "pm", "po", "qa", "sdet", "devops",
}

JOB_TITLE_DOMAIN_TOKENS = {
    "business", "software", "data", "product", "project", "systems",
    "system", "network", "cloud", "full", "stack", "fullstack",
    "frontend", "backend", "front", "back", "end", "ux", "ui",
    "technical", "tech", "sales", "marketing", "finance",
    "financial", "operations", "ops", "security", "cyber",
    "machine", "learning", "web", "mobile", "platform",
    "infrastructure", "solutions", "solution", "enterprise",
    "digital", "information", "quality", "assurance", "scrum",
    "agile", "delivery", "program", "portfolio", "release",
    "site", "reliability", "sre", "database", "warehouse",
}

# Exact / near-exact multi-word titles commonly pasted into name fields.
JOB_TITLE_PHRASES = {
    "business analyst",
    "senior business analyst",
    "junior business analyst",
    "software engineer",
    "senior software engineer",
    "software developer",
    "senior software developer",
    "product manager",
    "senior product manager",
    "project manager",
    "senior project manager",
    "product owner",
    "technical product owner",
    "scrum master",
    "data scientist",
    "data engineer",
    "data analyst",
    "devops engineer",
    "site reliability engineer",
    "solutions architect",
    "solution architect",
    "technical architect",
    "qa engineer",
    "quality assurance",
    "full stack developer",
    "fullstack developer",
    "front end developer",
    "frontend developer",
    "back end developer",
    "backend developer",
    "program manager",
    "engineering manager",
    "account manager",
    "sales manager",
    "hr manager",
    "recruiter",
    "talent acquisition",
}


def is_cta_phrase(text: Optional[str]) -> bool:
    """Return True if ``text`` is a job-board call-to-action phrase.

    Substring match (case-insensitive, whitespace-normalised) against
    :data:`CTA_PHRASES`. Used by :func:`is_valid_name` as a final
    sanity gate so a CTA fragment like "Invite Friend" or "Apply Now"
    can never be committed as a candidate's first + last name.

    Defensive against typical leak vectors: LinkedIn email footer
    buttons, Dice/Indeed action links, ZipRecruiter unsubscribe lines,
    and platform navigation breadcrumbs that AI extractors sometimes
    capture when the resume itself lacks a clear name header.
    """
    if not text:
        return False
    normalised = " ".join(text.strip().lower().split())
    if not normalised:
        return False
    for phrase in CTA_PHRASES:
        if phrase in normalised:
            return True
    return False


def is_job_title_phrase(text: Optional[str]) -> bool:
    """Return True if ``text`` looks like a job title rather than a person name.

    Production failure: apply-form / LinkedIn subject used the candidate's
    occupation ("Senior Business Analyst") where first/last name belong.
    ``is_valid_name`` previously accepted those tokens because they are
    alphabetic and title-cased.

    Match layers (any hit → True):

    1. Exact / substring hit against :data:`JOB_TITLE_PHRASES`.
    2. First token is seniority (``Senior``, ``Lead``, …) AND any later
       token is a role (``Analyst``, ``Engineer``, …).
    3. Every token (2+) is in the union of seniority / role / domain
       title vocabulary (e.g. ``Business Analyst``, ``Senior Business``
       after a role word was stripped). Bare ``John Senior`` still
       passes because ``John`` is not title vocabulary.
    """
    if not text:
        return False
    normalised = " ".join(text.strip().lower().split())
    if not normalised:
        return False
    for phrase in JOB_TITLE_PHRASES:
        if phrase == normalised or f" {phrase} " in f" {normalised} ":
            return True
        if normalised.startswith(phrase + " ") or normalised.endswith(" " + phrase):
            return True

    tokens = [t.strip(".,;:") for t in re.split(r"[\s\-]+", normalised) if t.strip(".,;:")]
    if len(tokens) < 2:
        return False

    title_vocab = (
        JOB_TITLE_SENIORITY_TOKENS
        | JOB_TITLE_ROLE_TOKENS
        | JOB_TITLE_DOMAIN_TOKENS
    )
    if tokens[0] in JOB_TITLE_SENIORITY_TOKENS and any(
        t in JOB_TITLE_ROLE_TOKENS for t in tokens[1:]
    ):
        return True
    # All-vocab pairs catch incomplete titles ("Senior Business") and
    # domain+role titles ("Business Analyst") without requiring a role
    # token — real surnames like "John Senior" stay allowed.
    if all(t in title_vocab for t in tokens):
        return True
    return False


NAME_TOKEN_RE = r"[A-Za-z][A-Za-z'\-]*"
# Non-greedy multi-token name capture so trailing suffix anchors match
# correctly. Allows 1-5 additional tokens after the first.
MULTI_TOKEN_NAME_PATTERN = rf"({NAME_TOKEN_RE}(?:\s+{NAME_TOKEN_RE}){{1,5}}?)"


def is_valid_name_token(token: str) -> bool:
    """Return True if a single token plausibly belongs to a person's name."""
    if not token:
        return False
    cleaned = token.strip().lower().rstrip(".")
    if not cleaned:
        return False
    if cleaned in INVALID_NAME_TOKENS:
        return False
    if not re.match(r"^[a-z][a-z'\-]*$", cleaned):
        return False
    if len(cleaned) > 40:
        return False
    return True


# Domains / local-parts that appear in job-board notification HTML but are
# never the candidate's real contact address (Easy Apply / digest emails).
JOB_BOARD_RELAY_DOMAINS = frozenset({
    'ziprecruiter.com',
    'indeed.com',
    'indeedemail.com',
    'linkedin.com',
    'lnkd.in',
    'dice.com',
    'glassdoor.com',
    'monster.com',
    'careerbuilder.com',
})

JOB_BOARD_RELAY_LOCALPARTS = frozenset({
    'noreply', 'no-reply', 'donotreply', 'do-not-reply',
    'mailer-daemon', 'notifications', 'notify', 'bounce',
})

# Our own intake / ops mailboxes. Zip/Indeed Easy Apply bodies greet
# ``Hi apply@myticas.com`` — a generic body scrape would treat that as the
# candidate email and collapse every applicant onto the Bullhorn record that
# already owns apply@ (prod incident: Candidate 4380273 "ISMS SME").
OWNED_INTAKE_DOMAINS = frozenset({
    'myticas.com',
    'stsigroup.com',
    'scoutgenius.ai',
})

OWNED_INTAKE_LOCALPARTS = frozenset({
    'apply', 'info', 'jobs', 'careers', 'noreply', 'no-reply',
    'donotreply', 'do-not-reply', 'parser', 'notifications',
})

# Exact addresses always treated as non-candidate (even if domain list drifts).
# apply@stsigroup.com is not a live EXO mailbox (bounces) but remains denylisted
# so legacy/body scrapes never treat it as the applicant.
# stsioffice@ is the STSI apply-form privacy mailto (not in OWNED_INTAKE_LOCALPARTS).
OWNED_INTAKE_ADDRESSES = frozenset({
    'apply@myticas.com',
    'apply@stsigroup.com',
    'stsioffice@stsigroup.com',
    'info@myticas.com',
    'info@stsigroup.com',
})


def is_owned_intake_mailbox(email: Optional[str]) -> bool:
    """True for our apply/info mailboxes that must never be a candidate email."""
    if not email or not isinstance(email, str):
        return False
    value = email.strip().lower()
    if '@' not in value:
        return False
    if value in OWNED_INTAKE_ADDRESSES:
        return True
    # GRAPH_MAILBOX_UPN (and similar) — whatever mailbox we poll is never the applicant.
    for env_key in ('GRAPH_MAILBOX_UPN', 'APPLY_EMAIL'):
        configured = (os.environ.get(env_key) or '').strip().lower()
        if configured and value == configured:
            return True
    local, _, domain = value.partition('@')
    if not local or not domain:
        return False
    if local in OWNED_INTAKE_LOCALPARTS and domain in OWNED_INTAKE_DOMAINS:
        return True
    return False


def is_job_board_relay_email(email: Optional[str]) -> bool:
    """True for board/ops addresses that must not become Bullhorn candidate email.

    Easy Apply notifications often embed ``noreply@ziprecruiter.com`` (etc.) in
    the HTML. Generic body scrapers would otherwise prefer that over the real
    address recovered from the résumé.

    Also rejects our own intake mailboxes (``apply@myticas.com`` etc.) which
    appear in ZipRecruiter greeting lines and must never win contact coalesce.
    """
    if not email or not isinstance(email, str):
        return False
    value = email.strip().lower()
    if '@' not in value:
        return False
    if is_owned_intake_mailbox(value):
        return True
    local, _, domain = value.partition('@')
    if not local or not domain:
        return False
    if local in JOB_BOARD_RELAY_LOCALPARTS:
        return True
    # Strip one subdomain level for match (e.g. mail.ziprecruiter.com)
    parts = domain.split('.')
    for i in range(len(parts) - 1):
        candidate = '.'.join(parts[i:])
        if candidate in JOB_BOARD_RELAY_DOMAINS:
            return True
    return False


def coalesce_candidate_email(*candidates: Optional[str]) -> Optional[str]:
    """Return the first usable candidate email, skipping board/relay/intake addresses."""
    for raw in candidates:
        if not raw or not str(raw).strip():
            continue
        value = str(raw).strip().lower()
        if is_job_board_relay_email(value):
            continue
        return value
    return None


def resolve_linkedin_profile_url(*sources: Optional[str]) -> Optional[str]:
    """Return a clickable LinkedIn ``/in/`` profile URL from résumé/body text.

    Used as a third contact channel (alongside email/phone) so name + LinkedIn
    alone can still create a Bullhorn candidate. Maps to Bullhorn ``customText9``.
    Returns ``None`` when no personal ``/in/`` profile is found (company pages
    and empty sources are ignored).
    """
    try:
        from fraud_detection.signals import extract_linkedin_url
    except ImportError:  # pragma: no cover — package always present in app
        return None
    canonical = extract_linkedin_url(*sources)
    if not canonical:
        return None
    # extract_linkedin_url returns ``linkedin.com/in/<slug>``; store absolute URL
    # so recruiters can open it from the Bullhorn LinkedIn field in one click.
    if canonical.startswith("http://") or canonical.startswith("https://"):
        return canonical
    return f"https://www.{canonical}"


def has_candidate_contact(
    email: Optional[str] = None,
    phone: Optional[str] = None,
    linkedin_url: Optional[str] = None,
) -> bool:
    """True when at least one recruiter-reachable contact channel is present."""
    if email and str(email).strip() and not is_job_board_relay_email(email):
        return True
    if phone and str(phone).strip():
        return True
    if linkedin_url and str(linkedin_url).strip():
        return True
    return False



def is_work_auth_phrase(text: Optional[str]) -> bool:
    """Return True if ``text`` is a work-authorization / citizenship phrase.

    Two-layer match:

    1. **Single-token rule** — tokenises on whitespace and hyphens
       (case-insensitive) and rejects if any token appears in
       :data:`WORK_AUTH_TOKENS`. These tokens are unambiguously
       work-auth vocabulary (``"citizen"``, ``"visa"``, ``"h1b"`` …)
       so an exact match is safe.

    2. **Phrase rule** — also checks if any phrase in
       :data:`WORK_AUTH_PHRASES` appears as a substring in the
       lowercased, whitespace-normalised text. This catches phrases
       built from ambiguous tokens (``"green card"``,
       ``"permanent resident"``) without rejecting the bare surname
       form (``"Eva Green"``, ``"John Permanent"``).

    Used as a blocklist by :func:`is_valid_name` and by upstream
    extractors so a line like ``"Canadian Citizen"`` or
    ``"Permanent Resident"`` can never be committed as a candidate's
    first/last name.
    """
    if not text:
        return False
    cleaned = text.strip().lower()
    if not cleaned:
        return False
    # Single-token rule: split on whitespace AND hyphens so "h-1b" and
    # "green-card" both decompose correctly.
    tokens = re.split(r"[\s\-]+", cleaned)
    if any(tok.strip(".,") in WORK_AUTH_TOKENS for tok in tokens):
        return True
    # Phrase rule: substring match after collapsing whitespace.
    normalised = " ".join(cleaned.split())
    for phrase in WORK_AUTH_PHRASES:
        if phrase in normalised:
            return True
    return False


def is_valid_name(first_name: Optional[str], last_name: Optional[str]) -> bool:
    """Return True if the (first, last) pair looks like a real person name.

    Rejects: empty/None, generic placeholders ("None None", "Resume Doc"),
    work-authorization / citizenship phrases ("Canadian Citizen",
    "Permanent Resident", "H1B Visa"), or anything containing
    digits/symbols other than hyphens and apostrophes. Multi-word last
    names are supported by validating each whitespace-separated token
    individually (e.g. "El Fared", "van der Berg") — name particles
    count as valid tokens.
    """
    if not first_name or not last_name:
        return False
    first = first_name.strip()
    last = last_name.strip()
    if not first or not last:
        return False

    # Reject work-authorization / citizenship phrases anywhere in the
    # combined name. This blocks the production failure mode where a
    # parser picked up a "Canadian Citizen" header line below the actual
    # name and shipped it to Bullhorn as the candidate's name.
    combined = f"{first} {last}"
    if is_work_auth_phrase(combined):
        return False

    # Reject job-board call-to-action phrases ("Invite Friend",
    # "Apply Now", "View Profile", etc.). Production failure: LinkedIn
    # email-footer "Invite a Friend" CTA was captured by a fallback name
    # extractor and committed to Bullhorn as firstName="Invite" /
    # lastName="Friend" for two real candidates.
    if is_cta_phrase(combined):
        return False

    # Reject job-title-shaped pairs ("Senior Business Analyst",
    # "Software Engineer"). Production failure: apply-form submitted
    # occupation as first/last and inbound preferred the email subject
    # over the AI résumé name (#4673968 Uday Vasireddy).
    if is_job_title_phrase(combined):
        return False

    # Validate first name: must be a single valid token
    if not is_valid_name_token(first):
        return False

    # Validate last name: split on whitespace, every token must be either
    # a known particle or a valid name token. At least one non-particle
    # token must exist (otherwise it's just particles like "van der").
    last_tokens = last.split()
    if not last_tokens:
        return False
    has_real_surname = False
    for tok in last_tokens:
        if tok.lower().rstrip(".") in NAME_PARTICLES:
            continue
        if not is_valid_name_token(tok):
            return False
        has_real_surname = True
    return has_real_surname


def split_full_name(full_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Split a full-name string into (first_name, last_name).

    Handles:
      - 2 tokens:                "John Smith"          -> (John, Smith)
      - 3+ tokens:               "Abderrahmane El Fared" -> (Abderrahmane, El Fared)
      - particles in last name:  "John van der Berg"   -> (John, van der Berg)
      - hyphenated:              "Mary-Jane O'Brien"   -> (Mary-Jane, O'Brien)
      - "Last, First" comma:     "Smith, John"         -> (John, Smith)
      - mononym (single token):  "Madonna"             -> (Madonna, None)

    Returns (None, None) if the input is empty or non-alphabetic noise.
    """
    if not full_name:
        return None, None

    cleaned = re.sub(r"\s+", " ", full_name.strip())
    if not cleaned:
        return None, None

    # "Last, First [Middle]" convention
    if "," in cleaned:
        last_part, _, first_part = cleaned.partition(",")
        last_part = last_part.strip()
        first_part = first_part.strip()
        if last_part and first_part:
            first_tokens = first_part.split()
            return _titlecase(first_tokens[0]), _titlecase(last_part)

    tokens = cleaned.split()
    tokens = [t for t in tokens if re.match(r"^[A-Za-z][A-Za-z'\-]*$", t)]
    if not tokens:
        return None, None

    if len(tokens) == 1:
        return _titlecase(tokens[0]), None

    first = tokens[0]
    last_tokens = tokens[1:]

    # Collapse trailing particles into the last name (e.g. "van der Berg")
    last = " ".join(_format_last_name_token(t) for t in last_tokens)
    return _titlecase(first), last.strip() or None


def _titlecase(token: str) -> str:
    """Normalize casing for a single name token preserving hyphens/apostrophes.

    Casing policy (shared by the résumé and inbound-email paths):

      * **Preserve deliberate internal capitalization.** A segment that
        already has an uppercase letter somewhere *other than* the first
        position AND at least one lowercase letter — "McDonald",
        "MacLeod", "DeVito", "DiCaprio" — is kept exactly as written. The
        source clearly intends that casing, so we must not flatten it to
        "Mcdonald".
      * **Otherwise title-case.** ALL-CAPS ("SMITH" -> "Smith") and
        lowercase ("smith" -> "Smith") input — which carries no casing
        signal — is title-cased.
      * **Re-capitalize the "Mc" prefix** on title-cased output
        ("Mcdonald" -> "McDonald"). "Mc" + a capital next letter is the
        near-universal Scottish/Irish form, so this is safe even when the
        source gave no signal (e.g. "MCDONALD").
      * **"Mac" is intentionally NOT auto-capitalized.** It collides with
        ordinary surnames/words (Mace, Mack, Macey, Machado, Macon), so
        guessing would produce false positives like "MacEy". A
        signal-less "MACLEOD" therefore stays "Macleod"; a source-cased
        "MacLeod" is preserved by the rule above.
    """
    if not token:
        return token

    def cap(part: str) -> str:
        if not part:
            return part
        # Preserve deliberate mixed-case (McDonald, DeVito, DiCaprio).
        if any(c.isupper() for c in part[1:]) and any(c.islower() for c in part):
            return part
        titled = part[:1].upper() + part[1:].lower()
        # Re-capitalize the unambiguous "Mc" prefix.
        if len(titled) > 2 and titled[:2] == "Mc" and titled[2].islower():
            titled = "Mc" + titled[2].upper() + titled[3:]
        return titled

    parts = re.split(r"([\-'])", token)
    return "".join(cap(p) if p not in ("-", "'") else p for p in parts)


def _format_last_name_token(token: str) -> str:
    """Lowercase known particles, title-case real surnames."""
    if token.lower().rstrip(".") in NAME_PARTICLES:
        return token.lower()
    return _titlecase(token)


def extract_name_from_pattern(
    text: str,
    prefix_pattern: str,
    suffix_pattern: str = r"(?=\s+has\s+applied|\s+applied\s+|\s*$|\s*[\n\r])",
) -> Tuple[Optional[str], Optional[str]]:
    """Search ``text`` for ``prefix_pattern`` followed by a multi-token name.

    ``prefix_pattern`` is a raw regex fragment that should leave the cursor
    immediately before the name to capture (e.g. r"-\\s*" or r"Name[:\\s]+").

    ``suffix_pattern`` defaults to a lookahead that stops at common job-board
    boundaries ("has applied", "applied", end-of-line). Pass an empty string
    to disable suffix anchoring.

    The non-greedy multi-token quantifier guarantees that the first valid
    sentence boundary wins — preventing capture from absorbing trailing
    "has applied on LinkedIn" text into the surname.

    Returns (first_name, last_name) or (None, None) on no match.
    """
    if not text:
        return None, None
    # Truncate adversarially long input before regex search to bound any
    # backtracking work. 20 KB is well above the largest realistic email
    # subject + label-fragment we extract from.
    if len(text) > 20_000:
        text = text[:20_000]
    pattern_str = prefix_pattern + MULTI_TOKEN_NAME_PATTERN + suffix_pattern
    try:
        pattern = re.compile(pattern_str, re.IGNORECASE)
    except re.error:
        return None, None
    match = pattern.search(text)
    if not match:
        return None, None
    captured = match.group(1)
    return split_full_name(captured)


def parse_name_from_filename(filename: str) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort extraction of a candidate name from a resume filename.

    Handles common conventions:
      - "First Last Resume.docx"
      - "First_Last_CV.pdf"
      - "Last, First - Resume 2024.pdf"
      - "Resume - First Last.pdf"
      - "First-Last_resume_v2.docx"

    Strips file extensions, suffix tokens (Resume, CV, year tags, version
    numbers), and replaces underscores/hyphens with spaces before
    delegating to ``split_full_name``.
    """
    if not filename:
        return None, None

    base = os.path.basename(filename)
    base = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", base)  # strip extension

    # Normalise separators: underscores and dots become spaces; preserve
    # internal hyphens and apostrophes for names like "Mary-Jane".
    normalised = re.sub(r"[_\.]+", " ", base)
    normalised = re.sub(r"\s+-\s+", " ", normalised)

    # Drop generic resume/CV/version/year suffixes anywhere in the name.
    tokens = normalised.split()
    filtered = []
    for tok in tokens:
        bare = tok.lower().strip("()[]{}")
        if bare in GENERIC_FILENAME_TOKENS:
            continue
        if re.fullmatch(r"v\d+(\.\d+)?", bare):  # version tags v1, v2.0
            continue
        if re.fullmatch(r"\d{4}", bare):  # year tags 2024
            continue
        if re.fullmatch(r"\d+", bare):  # plain numbers
            continue
        filtered.append(tok)

    if not filtered:
        return None, None

    # Strip trailing job-title tokens so filenames like
    # "Uday_Vasireddy_Senior_Business_Analyst.docx" yield the person
    # name rather than absorbing the occupation suffix into last_name.
    # Continue while len > 1 so a title-only filename
    # ("Senior_Business_Analyst.docx") collapses to a mononym / empty
    # rather than leaving the remnant pair "Senior Business".
    title_vocab = (
        JOB_TITLE_SENIORITY_TOKENS
        | JOB_TITLE_ROLE_TOKENS
        | JOB_TITLE_DOMAIN_TOKENS
    )
    while len(filtered) > 1 and filtered[-1].lower().strip("()[]{}") in title_vocab:
        filtered.pop()

    if not filtered:
        return None, None

    candidate = " ".join(filtered)
    first, last = split_full_name(candidate)

    # If the first or last token is a generic filename word that slipped
    # through (e.g. "Resume Smith"), reject the result.
    if first and first.lower() in INVALID_NAME_TOKENS:
        return None, None
    if last and last.split()[0].lower() in INVALID_NAME_TOKENS:
        return None, None

    # Reject when the remaining (first, last) is still a job title
    # (e.g. filename was only "Senior_Business_Analyst.docx").
    if first and last and is_job_title_phrase(f"{first} {last}"):
        return None, None
    # Title-only filenames that collapsed to a single seniority/role
    # token (mononym) are not usable person names.
    if first and not last and first.lower() in title_vocab:
        return None, None

    return first, last


def parse_name_from_email_address(email: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract a (first, last) guess from local-part of an email address.

    Conventions handled:
      - first.last@domain    -> (First, Last)
      - first_last@domain    -> (First, Last)
      - firstlast@domain     -> (None, None)  -- ambiguous, do not guess
      - flast@domain         -> (None, None)
      - first-last@domain    -> (First, Last)

    Numbers and trailing digits are stripped (e.g. ``john.smith24@``).
    """
    if not email or "@" not in email:
        return None, None
    local = email.split("@", 1)[0]
    local = re.sub(r"\d+$", "", local)
    parts = re.split(r"[._\-]+", local)
    parts = [p for p in parts if p and p.isalpha() and len(p) >= 2]
    if len(parts) < 2:
        return None, None
    return _titlecase(parts[0]), _titlecase(parts[-1])


def merge_name_candidates(*candidates: Tuple[Optional[str], Optional[str]]) -> Tuple[Optional[str], Optional[str]]:
    """Pick the first (first, last) tuple where both halves look valid.

    Used to combine results from email-subject extraction, AI resume
    parsing, filename parsing, and the last-resort AI call into a single
    decision.
    """
    for first, last in candidates:
        if is_valid_name(first, last):
            return first, last
    # Accept partial result (first only) only if no full pair was found.
    for first, last in candidates:
        if first and is_valid_name_token(first):
            return first, last
    return None, None


def strip_html_to_text(html: str) -> str:
    """Convert HTML email body to a plain-text approximation for regex.

    Preserves label structure ("Email:", "Phone:") that the existing
    regex relies on, and inserts whitespace around block-level tags so
    adjacent fields don't collide.
    """
    if not html:
        return ""
    if "<" not in html:
        return html
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Replace <br> with newlines so phone/email labels stay on their own line
        for br in soup.find_all("br"):
            br.replace_with("\n")
        # Add newlines around block elements (also include td and h5/h6)
        for tag in soup.find_all(["p", "div", "tr", "td", "li",
                                   "h1", "h2", "h3", "h4", "h5", "h6"]):
            tag.insert_before("\n")
            tag.insert_after("\n")
        # Add a single-space separator around inline tags so adjacent
        # spans like "<span>Name</span><span>John Doe</span>" don't
        # collide into "NameJohn Doe".
        for tag in soup.find_all(["span", "strong", "b", "em", "i", "label"]):
            tag.insert_before(" ")
            tag.insert_after(" ")
        text = soup.get_text(separator=" ", strip=False)
    except Exception:
        # Defensive fallback if BeautifulSoup misbehaves on malformed HTML
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        text = re.sub(r"</(p|div|tr|td|li|h\d)>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</?(span|strong|b|em|i|label)>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\u00a0", " ", text)  # non-breaking spaces
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_extraction_summary(resume_data: Dict, email_candidate: Dict, filename: Optional[str]) -> Dict:
    """Compose a structured snapshot of everything we *did* manage to extract.

    Used by the admin notification path so a human can pick up where the
    automation gave up without re-doing the parsing work.
    """
    return {
        "filename": filename,
        "email_extracted": {
            "first_name": email_candidate.get("first_name"),
            "last_name": email_candidate.get("last_name"),
            "email": email_candidate.get("email"),
            "phone": email_candidate.get("phone"),
        },
        "resume_extracted": {
            "first_name": resume_data.get("first_name"),
            "last_name": resume_data.get("last_name"),
            "email": resume_data.get("email"),
            "phone": resume_data.get("phone"),
            "current_title": resume_data.get("current_title"),
            "current_company": resume_data.get("current_company"),
            "years_experience": resume_data.get("years_experience"),
            "skills_count": len(resume_data.get("skills") or []),
            "skills_preview": (resume_data.get("skills") or [])[:10],
            "city": resume_data.get("city"),
            "state": resume_data.get("state"),
        },
    }
