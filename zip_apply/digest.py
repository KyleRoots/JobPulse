"""Summarize a Zip Apply webhook body without keeping resume bytes."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional, Tuple

MAX_BODY_BYTES = 15 * 1024 * 1024

_JOB_ID_KEYS = ("job_id", "jobId", "referencenumber", "reference_number", "referenceNumber")
_RESPONSE_ID_KEYS = ("response_id", "responseId", "application_id", "applicationId")
_EMAIL_KEYS = ("email", "email_address", "emailAddress")
_PHONE_KEYS = ("phone", "phone_number", "phoneNumber", "mobile")
_NAME_KEYS = ("name", "full_name", "fullName", "first_name", "firstName")
_RESUME_KEYS = ("resume", "resume_data", "resumeData", "file", "file_content", "encoded_resume")
_PROFILE_KEYS = ("profile", "job_records", "text_resume", "textResume")

_SIGNATURE_HEADERS = (
    "x-ziprecruiter-signature",
    "x-zr-signature",
    "zip-signature",
    "x-signature",
)


def body_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw or b"").hexdigest()


def _find(obj: Any, keys: Tuple[str, ...], depth: int = 0) -> Any:
    if depth > 4 or not isinstance(obj, dict):
        return None
    for key in keys:
        value = obj.get(key)
        if value not in (None, "", [], {}):
            return value
    for value in obj.values():
        if isinstance(value, dict):
            found = _find(value, keys, depth + 1)
            if found not in (None, "", [], {}):
                return found
    return None


def _has_key(obj: Any, keys: Tuple[str, ...], depth: int = 0) -> bool:
    if depth > 4 or not isinstance(obj, dict):
        return False
    for key in obj:
        if key in keys:
            return True
        if "resume" in key.lower() and key.lower() not in ("text_resume", "textresume"):
            return True
    for value in obj.values():
        if isinstance(value, dict) and _has_key(value, keys, depth + 1):
            return True
    return False


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text[:120] or None


def summarize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Presence flags and ids only. Never returns contact values or resume bytes."""
    top_keys = sorted(str(k) for k in payload.keys())[:40]
    return {
        "job_id": _as_text(_find(payload, _JOB_ID_KEYS)),
        "response_id": _as_text(_find(payload, _RESPONSE_ID_KEYS)),
        "has_name": _find(payload, _NAME_KEYS) is not None,
        "has_email": _find(payload, _EMAIL_KEYS) is not None,
        "has_phone": _find(payload, _PHONE_KEYS) is not None,
        "has_resume": _has_key(payload, _RESUME_KEYS),
        "has_profile": _find(payload, _PROFILE_KEYS) is not None or _has_key(payload, ("profile",)),
        "field_names": ", ".join(top_keys),
    }


def parse_json_body(raw: bytes) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not raw:
        return None, "empty body"
    if len(raw) > MAX_BODY_BYTES:
        return None, "body too large"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "body must be JSON"
    if not isinstance(payload, dict):
        return None, "JSON body must be an object"
    return payload, None


def signature_header_present(headers) -> bool:
    if headers is None:
        return False
    for name in _SIGNATURE_HEADERS:
        if headers.get(name):
            return True
    return False
