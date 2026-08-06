"""Conservative URL-only LinkedIn soft cross-check (public data, no login).

Fetches the résumé-provided ``linkedin.com/in/...`` URL and returns a status
plus optional public profile name. Never guesses profiles by name search.
Fail-soft: network/parse errors → status ``error`` / ``blocked``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("fraud_detection.linkedin_crosscheck")

_TITLE_RE = re.compile(
    r"<title[^>]*>(.*?)</title>",
    re.IGNORECASE | re.DOTALL,
)
_OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_TITLE_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
    re.IGNORECASE,
)


def _normalize_profile_url(linkedin_url: str) -> Optional[str]:
    raw = (linkedin_url or "").strip()
    if not raw:
        return None
    if not raw.startswith("http"):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    host = (parsed.netloc or "").lower()
    if "linkedin.com" not in host:
        return None
    path = parsed.path or ""
    if "/in/" not in path.lower():
        return None
    return f"https://www.linkedin.com{path.rstrip('/')}"


def _extract_name_from_html(html: str) -> Optional[str]:
    for rx in (_OG_TITLE_RE, _OG_TITLE_RE_ALT):
        m = rx.search(html or "")
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            # og:title often "Name - Title | LinkedIn"
            name = title.split("|")[0].split(" - ")[0].strip()
            if name and len(name) >= 2:
                return name[:120]
    m = _TITLE_RE.search(html or "")
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        name = title.split("|")[0].split(" - ")[0].strip()
        if name and "linkedin" not in name.lower() and len(name) >= 2:
            return name[:120]
    return None


def check_linkedin_profile(linkedin_url: Optional[str]) -> Dict[str, Any]:
    """Return ``{status, profile_name, url}``.

    status: ok | dead | private | blocked | error | skipped
    """
    url = _normalize_profile_url(linkedin_url or "")
    if not url:
        return {"status": "skipped", "profile_name": None, "url": None}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; ScoutGeniusIntegrity/1.0; "
            "+https://app.scoutgenius.ai)"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        final = (resp.url or url).lower()
        if resp.status_code == 404 or "/404" in final:
            return {"status": "dead", "profile_name": None, "url": url}
        if resp.status_code in (401, 403):
            return {"status": "blocked", "profile_name": None, "url": url}
        if resp.status_code >= 400:
            return {"status": "error", "profile_name": None, "url": url}

        html = resp.text or ""
        # Auth walls / empty shells often lack a real profile name.
        if "authwall" in final or "signup" in final or "login" in html[:2000].lower():
            return {"status": "private", "profile_name": None, "url": url}

        name = _extract_name_from_html(html)
        if not name:
            return {"status": "private", "profile_name": None, "url": url}
        return {"status": "ok", "profile_name": name, "url": url}
    except requests.Timeout:
        logger.debug("LinkedIn check timeout for %s", url)
        return {"status": "error", "profile_name": None, "url": url}
    except Exception as exc:
        logger.debug("LinkedIn check failed: %s", exc)
        return {"status": "error", "profile_name": None, "url": url}
