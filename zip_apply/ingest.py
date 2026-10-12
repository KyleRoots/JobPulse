"""Turn a Zip Apply JSON body into the mailbox Bullhorn path.

Qualified only. The JSON is adapted into the same SendGrid-shaped payload
``EmailInboundService.process_email`` already accepts, so résumé parsing,
the candidate Resume field, file upload, field mapping, dedupe, and
job submission stay on one path. Resume bytes are not written to
``zip_apply_delivery``.
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any, Dict, Optional, Tuple

from zip_apply.digest import summarize_payload

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_APPLY_TO = "apply@q-staffing.com"
_ZIP_FROM = "ZipRecruiter <noreply@ziprecruiter.com>"


def webhook_creates_candidates() -> bool:
    """Only the Qualified service should create people from this webhook."""
    return os.environ.get("SCOUT_TENANT", "").strip().lower() == "qualified_staffing"


def message_id_for(payload: Dict[str, Any]) -> str:
    summary = summarize_payload(payload)
    token = summary.get("response_id") or summary.get("job_id") or "delivery"
    token = _SAFE_NAME.sub("-", str(token))[:80] or "delivery"
    return f"<zip-apply-{token}@ziprecruiter.com>"


def resolve_bullhorn_job_id(reference: Optional[str]) -> Optional[str]:
    """Map Zip's alphanumeric job id to the numeric Bullhorn job id."""
    if not reference or not str(reference).strip():
        return None
    from models.scheduling import JobReferenceNumber

    row = JobReferenceNumber.query.filter(
        JobReferenceNumber.reference_number == str(reference).strip()
    ).first()
    if not row or not row.bullhorn_job_id:
        return None
    job_id = str(row.bullhorn_job_id).strip()
    if not job_id.isdigit():
        return None
    return job_id


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _person(payload: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    first = _text(payload.get("first_name") or payload.get("firstName"))
    last = _text(payload.get("last_name") or payload.get("lastName"))
    name = _text(payload.get("name") or payload.get("full_name") or payload.get("fullName"))
    if not name:
        name = f"{first} {last}".strip()
    if name and not first:
        bits = name.split()
        first = bits[0]
        last = " ".join(bits[1:])
    email = _text(payload.get("email") or payload.get("email_address") or payload.get("emailAddress"))
    phone = _text(payload.get("phone") or payload.get("phone_number") or payload.get("phoneNumber"))
    return first, last, (name or "Applicant"), email, phone


def _profile_text(payload: Dict[str, Any]) -> str:
    profile = payload.get("profile")
    if isinstance(profile, str):
        return profile.strip()[:20000]
    if not isinstance(profile, dict):
        return ""
    parts = []
    for key in ("text", "text_resume", "textResume", "executive_summary", "summary"):
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n\n".join(parts)[:20000]


def _resume_bytes(payload: Dict[str, Any]) -> Tuple[Optional[bytes], Optional[str]]:
    raw = payload.get("resume")
    if raw in (None, "", {}, []):
        raw = payload.get("resume_data") or payload.get("encoded_resume") or payload.get("file")
    filename = _text(payload.get("resume_filename") or payload.get("filename"))
    if isinstance(raw, dict):
        filename = filename or _text(raw.get("filename") or raw.get("file_name") or raw.get("fileName"))
        raw = raw.get("content") or raw.get("data") or raw.get("file") or raw.get("encoded_resume")
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    encoded = raw.strip()
    if "," in encoded and encoded.lower().startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        data = base64.b64decode(encoded, validate=False)
    except Exception:
        return None, None
    if len(data) < 32:
        return None, None
    return data, _resume_filename(data, filename, payload)


def _resume_filename(data: bytes, filename: str, payload: Dict[str, Any]) -> str:
    ext = ".pdf"
    content_hint = data[:8]
    if content_hint.startswith(b"%PDF"):
        ext = ".pdf"
    elif content_hint.startswith(b"PK"):
        ext = ".docx"
    elif content_hint.startswith(b"\xd0\xcf\x11\xe0"):
        ext = ".doc"
    if filename:
        safe = _SAFE_NAME.sub("_", filename)[:160]
        lower = safe.lower()
        if not any(lower.endswith(suffix) for suffix in (".pdf", ".doc", ".docx", ".rtf", ".txt")):
            safe = f"{safe}{ext}"
        if "resume" not in lower and "cv" not in lower:
            stem, dot, suffix = safe.rpartition(".")
            safe = f"{stem or safe}_resume.{suffix or ext.lstrip('.')}"
        return safe
    _first, last, _name, _email, _phone = _person(payload)
    stem = _SAFE_NAME.sub("_", last or "applicant")[:40] or "applicant"
    return f"{stem}_resume{ext}"


def build_mailbox_payload(payload: Dict[str, Any], bullhorn_job_id: Optional[str]) -> Dict[str, str]:
    """Shape Zip JSON as the mailbox parser already understands."""
    _first, _last, name, email, phone = _person(payload)
    job_bit = f" ({bullhorn_job_id})" if bullhorn_job_id else ""
    subject = f"ZipRecruiter Apply{job_bit} - {name}"[:240]
    lines = [f"Name: {name}"]
    if email:
        lines.append(f"Email: {email}")
    if phone:
        lines.append(f"Phone: {phone}")
    lines.append("")
    lines.append("Submitted via ZipRecruiter Apply.")
    profile = _profile_text(payload)
    if profile:
        lines.extend(["", profile])
    body = "\n".join(lines)
    outbound = {
        "from": _ZIP_FROM,
        "to": _APPLY_TO,
        "subject": subject,
        "text": body,
        "html": "",
        "headers": f"Message-ID: {message_id_for(payload)}\n",
    }
    content, filename = _resume_bytes(payload)
    if content and filename:
        content_type = "application/pdf"
        if filename.lower().endswith(".docx"):
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif filename.lower().endswith(".doc"):
            content_type = "application/msword"
        elif filename.lower().endswith(".txt"):
            content_type = "text/plain"
        outbound["attachments"] = json.dumps([{
            "filename": filename,
            "type": content_type,
            "content": base64.b64encode(content).decode("ascii"),
        }])
    return outbound


def _safe_message(text: str) -> str:
    cleaned = _EMAIL_RE.sub("[redacted]", text or "")
    return cleaned[:500]


def _ids_from_prior_attempt(message_id: str) -> Optional[Dict[str, Any]]:
    from extensions import db
    from models.ats import ParsedEmail

    row = ParsedEmail.query.filter_by(message_id=message_id).first()
    if row is None:
        row = ParsedEmail.query.filter_by(recovery_message_id=message_id).order_by(
            ParsedEmail.id.desc()
        ).first()
    if row is None:
        return None
    if row.status == "completed" and row.bullhorn_candidate_id:
        return {
            "success": True,
            "candidate_id": row.bullhorn_candidate_id,
            "submission_id": row.bullhorn_submission_id,
            "duplicate": True,
        }
    if row.status == "failed":
        row.recovery_message_id = message_id
        row.message_id = None
        db.session.commit()
        return None
    if row.status in ("processing", "received"):
        return {"success": False, "message": "still processing", "retry": True}
    return {"success": True, "duplicate": True, "candidate_id": row.bullhorn_candidate_id}


def ingest_zip_apply(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update the Bullhorn candidate the way a mailbox apply would."""
    from email_inbound_service import EmailInboundService

    summary = summarize_payload(payload)
    bullhorn_job_id = resolve_bullhorn_job_id(summary.get("job_id"))
    mailbox = build_mailbox_payload(payload, bullhorn_job_id)
    message_id = message_id_for(payload)
    result = EmailInboundService().process_email(mailbox)
    if result.get("duplicate") and not result.get("candidate_id"):
        prior = _ids_from_prior_attempt(message_id)
        if prior and prior.get("retry"):
            return {
                "success": False,
                "message": "still processing",
                "bullhorn_job_id": bullhorn_job_id,
                "unmatched_job": bullhorn_job_id is None,
            }
        if prior is None:
            result = EmailInboundService().process_email(mailbox)
        else:
            result = prior
    success = bool(result.get("success"))
    return {
        "success": success,
        "candidate_id": result.get("candidate_id"),
        "submission_id": result.get("submission_id"),
        "bullhorn_job_id": bullhorn_job_id,
        "unmatched_job": bullhorn_job_id is None,
        "message": "" if success else _safe_message(str(result.get("message") or "ingest failed")),
    }
