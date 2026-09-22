"""Mirror Job Internal Department onto Candidate (Qualified inbound).

On Qualified Staffing Bullhorn:
  JobOrder.correlatedCustomText1  label = "Internal Department" (e.g. Dalton)
  Candidate.customText3           label = "Internal Department" (SELECT)

LinkedIn / Zip / apply-form traffic arrives via apply@ mailbox pull. When we
know the Bullhorn job id, copy the job's department string onto the candidate
so branch reporting matches the role they applied to.

Myticas/STSI are untouched (different field semantics). Fail-soft always.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Qualified Candidate field that the UI labels "Internal Department"
CANDIDATE_INTERNAL_DEPARTMENT_FIELD = 'customText3'
# Qualified JobOrder field that the UI labels "Internal Department"
JOB_INTERNAL_DEPARTMENT_FIELD = 'correlatedCustomText1'


def should_mirror_job_internal_department() -> bool:
    try:
        from feeds.feed_config import is_qualified_tenant
        return bool(is_qualified_tenant())
    except Exception:
        return False


def fetch_job_internal_department(bullhorn: Any, job_id: Optional[int]) -> Optional[str]:
    """Return the job's Internal Department string, or None."""
    if not job_id or not bullhorn:
        return None
    try:
        job_id_int = int(job_id)
    except (TypeError, ValueError):
        return None

    try:
        if not getattr(bullhorn, 'base_url', None) or not getattr(bullhorn, 'rest_token', None):
            if hasattr(bullhorn, 'authenticate') and not bullhorn.authenticate():
                return None

        url = f"{bullhorn.base_url}entity/JobOrder/{job_id_int}"
        params = {
            'fields': f'id,{JOB_INTERNAL_DEPARTMENT_FIELD}',
            'BhRestToken': bullhorn.rest_token,
        }
        response = bullhorn.session.get(url, params=params, timeout=20)
        if response.status_code == 401 and hasattr(bullhorn, 'authenticate'):
            bullhorn.rest_token = None
            if bullhorn.authenticate():
                params['BhRestToken'] = bullhorn.rest_token
                response = bullhorn.session.get(url, params=params, timeout=20)
        if response.status_code != 200:
            logger.warning(
                "Job %s Internal Department fetch failed: HTTP %s",
                job_id_int,
                response.status_code,
            )
            return None
        data = response.json() if hasattr(response, 'json') else {}
        if hasattr(bullhorn, '_safe_json_parse'):
            data = bullhorn._safe_json_parse(response)
        job = data.get('data') or {}
        raw = job.get(JOB_INTERNAL_DEPARTMENT_FIELD)
        if raw is None:
            return None
        value = str(raw).strip()
        return value or None
    except Exception as exc:
        logger.warning(
            "Job %s Internal Department lookup failed (non-fatal): %s",
            job_id,
            exc,
        )
        return None


def apply_job_internal_department_to_candidate_payload(
    candidate_payload: dict,
    department: Optional[str],
) -> bool:
    """Set Candidate.customText3 from department. Returns True if applied."""
    if not department or not isinstance(candidate_payload, dict):
        return False
    value = str(department).strip()
    if not value:
        return False
    candidate_payload[CANDIDATE_INTERNAL_DEPARTMENT_FIELD] = value
    return True
