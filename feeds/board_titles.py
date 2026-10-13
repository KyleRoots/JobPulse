"""Board-specific display titles for XML job feeds.

ZipRecruiter and the Indeed XML fallback keep Bullhorn punctuation and still
append the Bullhorn job id. Mailbox matching reads that suffix. LinkedIn and
every other channel keep the historical formatter in
``xml_integration_service.mapping`` (slashes become spaces, parentheticals are
removed, then the id is appended).

The Zip Apply webhook does not read the title. It resolves ``referencenumber``.
Native Indeed publish does not use this module. It sends ``jobPostingID`` and
leaves the Bullhorn title as stored.
"""
from __future__ import annotations

import html
import re

_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufeff\ufffe\uffff]")


def _sanitize_title_text(title: object) -> str:
    """Plain text safe for an XML CDATA title. Punctuation is kept."""
    if title is None:
        return ""
    text = html.unescape(str(title))
    text = _ILLEGAL_XML.sub("", text)
    # A raw "]]>" would end a CDATA section early.
    text = text.replace("]]>", "]] >")
    return text.strip()


def display_title_keeping_punctuation(title: object, job_id: object) -> str:
    """Zip and Indeed XML title: Bullhorn text plus the Bullhorn id suffix.

    Slashes, dashes, quotes, ampersands, accents, and parentheses from
    Bullhorn are kept. Only an id suffix this function itself adds is
    treated as generated. A title that already ends with that suffix is
    not given a second copy.
    """
    text = _sanitize_title_text(title) or "Untitled Position"
    job_text = str(job_id or "").strip()
    if not job_text or job_text == "None":
        return text
    suffix = f"({job_text})"
    if text.endswith(suffix):
        return text
    return f"{text} {suffix}"


zip_display_title = display_title_keeping_punctuation
