"""Optional NeverBounce + Twilio Lookup contact validation (fail-soft).

Gated by VettingConfig ``fraud_contact_validation_enabled`` and env secrets.
Missing keys or API errors return None — never raise into screening.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from urllib.parse import quote

import requests

logger = logging.getLogger("fraud_detection.contact_validation")

CACHE_TTL_DAYS = 30
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _hash_contact(kind: str, value: str) -> str:
    raw = f"{kind}:{value.strip().lower()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cache_get(kind: str, value: str) -> Optional[Dict[str, Any]]:
    try:
        from app import db
        from models import ContactValidationCache
        from sqlalchemy.orm import Session

        h = _hash_contact(kind, value)
        with Session(db.engine, expire_on_commit=False) as session:
            row = (
                session.query(ContactValidationCache)
                .filter_by(contact_type=kind, contact_hash=h)
                .first()
            )
            if not row or not row.result_json:
                return None
            if row.expires_at and row.expires_at < datetime.utcnow():
                return None
            return json.loads(row.result_json)
    except Exception as exc:
        logger.debug("contact cache get failed: %s", exc)
        return None


def _cache_put(kind: str, value: str, payload: Dict[str, Any]) -> None:
    try:
        from app import db
        from models import ContactValidationCache
        from sqlalchemy.orm import Session

        h = _hash_contact(kind, value)
        expires = datetime.utcnow() + timedelta(days=CACHE_TTL_DAYS)
        blob = json.dumps(payload)
        with Session(db.engine, expire_on_commit=False) as session:
            row = (
                session.query(ContactValidationCache)
                .filter_by(contact_type=kind, contact_hash=h)
                .first()
            )
            if row is None:
                row = ContactValidationCache(
                    contact_type=kind,
                    contact_hash=h,
                    result_json=blob,
                    expires_at=expires,
                )
                session.add(row)
            else:
                row.result_json = blob
                row.expires_at = expires
                row.updated_at = datetime.utcnow()
            session.commit()
    except Exception as exc:
        logger.debug("contact cache put failed: %s", exc)


def validate_email(email: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return ``{"result": "valid"|"invalid"|...}`` or None if skipped."""
    if not email or not _EMAIL_RE.match(email.strip()):
        return None
    email = email.strip()
    cached = _cache_get("email", email)
    if cached is not None:
        return cached

    api_key = (os.environ.get("NEVERBOUNCE_API_KEY") or "").strip()
    if not api_key:
        return None

    try:
        resp = requests.get(
            "https://api.neverbounce.com/v4/single/check",
            params={"key": api_key, "email": email},
            timeout=8,
        )
        if resp.status_code != 200:
            logger.warning("NeverBounce HTTP %s", resp.status_code)
            return None
        data = resp.json() or {}
        # NeverBounce: 0=valid, 1=invalid, 2=disposable, 3=catchall, 4=unknown
        code = data.get("result")
        mapping = {
            0: "valid",
            "valid": "valid",
            1: "invalid",
            "invalid": "invalid",
            2: "disposable",
            "disposable": "disposable",
            3: "catchall",
            "catchall": "catchall",
            4: "unknown",
            "unknown": "unknown",
        }
        result = mapping.get(code, str(code or "unknown").lower())
        payload = {"result": result, "provider": "neverbounce"}
        _cache_put("email", email, payload)
        return payload
    except Exception as exc:
        logger.warning("NeverBounce check failed: %s", exc)
        return None


def validate_phone(phone: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return ``{"valid": bool, "line_type": str}`` or None if skipped."""
    digits = re.sub(r"[^0-9]", "", str(phone or ""))
    if len(digits) < 10:
        return None
    cached = _cache_get("phone", digits)
    if cached is not None:
        return cached

    sid = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
    if not sid or not token:
        return None

    # E.164-ish: assume NA if 10 digits
    e164 = digits if digits.startswith("1") and len(digits) == 11 else (
        f"1{digits}" if len(digits) == 10 else digits
    )
    e164 = f"+{e164}" if not e164.startswith("+") else e164

    try:
        url = f"https://lookups.twilio.com/v2/PhoneNumbers/{quote(e164)}"
        resp = requests.get(
            url,
            params={"Fields": "line_type_intelligence"},
            auth=(sid, token),
            timeout=8,
        )
        if resp.status_code == 404:
            payload = {"valid": False, "line_type": "", "provider": "twilio"}
            _cache_put("phone", digits, payload)
            return payload
        if resp.status_code != 200:
            logger.warning("Twilio Lookup HTTP %s", resp.status_code)
            return None
        data = resp.json() or {}
        lti = data.get("line_type_intelligence") or {}
        line_type = (
            lti.get("type")
            or data.get("line_type")
            or ""
        )
        payload = {
            "valid": True,
            "line_type": str(line_type or "").lower(),
            "provider": "twilio",
        }
        _cache_put("phone", digits, payload)
        return payload
    except Exception as exc:
        logger.warning("Twilio Lookup failed: %s", exc)
        return None


def run_contact_validation(
    email: Optional[str],
    phone: Optional[str],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Run email + phone checks. Returns (email_payload, phone_payload)."""
    return validate_email(email), validate_phone(phone)
