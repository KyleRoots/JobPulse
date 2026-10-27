"""Match an unknown Zip reference to one open job by title.

Used only after the feed reference and retired aliases miss. A title is
used when exactly one open job has that title. Zero matches and two
matches both stay unmatched.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

_TITLE_KEYS = ("job_title", "jobTitle", "position_title", "title")
_NESTED_JOB_KEYS = ("job", "listing", "posting")
_TRAILING_ID = re.compile(r"\s*\(\d{1,6}\)\s*$")
_CACHE_SECONDS = 300
_open_job_cache: Dict[str, Any] = {"at": 0.0, "jobs": None}


def zip_job_title(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    """Title of the job the person applied to, not a past job on the profile."""
    if not isinstance(payload, dict):
        return None
    found = _title_in(payload)
    if found:
        return found
    for key in _NESTED_JOB_KEYS:
        nested = payload.get(key)
        found = _title_in(nested)
        if found:
            return found
    return None


def _title_in(record: Any) -> Optional[str]:
    if not isinstance(record, dict):
        return None
    for key in _TITLE_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    return None


def normalize_job_title(title: Optional[str]) -> str:
    """Compare titles ignoring case, spacing, and a trailing Bullhorn id."""
    text = re.sub(r"\s+", " ", str(title or "")).strip().casefold()
    return _TRAILING_ID.sub("", text).strip()


def match_open_job_by_title(title: Optional[str], jobs: Iterable[Dict[str, Any]]) -> Optional[str]:
    """Return one Bullhorn job id, or None unless exactly one open job matches."""
    wanted = normalize_job_title(title)
    if not wanted:
        return None
    matches = []
    for job in jobs or []:
        if not isinstance(job, dict) or job.get("isOpen") is not True:
            continue
        if normalize_job_title(job.get("title")) != wanted:
            continue
        job_id = str(job.get("id") or "").strip()
        if job_id.isdigit() and job_id not in matches:
            matches.append(job_id)
    if len(matches) == 1:
        return matches[0]
    return None


def load_open_qualified_jobs() -> list:
    """Open jobs on this service's tearsheets. Empty when Bullhorn cannot be read."""
    now = time.time()
    cached = _open_job_cache.get("jobs")
    if cached is not None and now - float(_open_job_cache.get("at") or 0) < _CACHE_SECONDS:
        return cached
    try:
        from candidate_vetting_service import CandidateVettingService

        jobs = CandidateVettingService().get_active_jobs_from_tearsheets() or []
    except Exception:
        logger.warning("Could not load open jobs for a Zip title match", exc_info=True)
        return []
    open_jobs = [job for job in jobs if isinstance(job, dict) and job.get("isOpen") is True]
    _open_job_cache["jobs"] = open_jobs
    _open_job_cache["at"] = now
    return open_jobs


def clear_open_job_cache() -> None:
    _open_job_cache["jobs"] = None
    _open_job_cache["at"] = 0.0
