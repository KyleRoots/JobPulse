"""Pure eligibility + sales-rep resolution for Myticas client OB notify.

No Flask / Bullhorn I/O here — unit-tested in isolation.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Company Status (Bullhorn field `status`). Positive pipeline only.
POSITIVE_STATUSES = frozenset({
    "Qualified",
    "Proposal",
    "Negotiation",
    "Active Account",
})

# Company Type (Bullhorn field `customText1`, label "Type").
EXCLUDED_TYPES = frozenset({
    "Vendor",
    "MSP",
    "Former Client",
})


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        return _norm(value[0])
    return str(value).strip()


def company_type(company: Dict[str, Any]) -> str:
    return _norm(company.get("customText1"))


def company_status(company: Dict[str, Any]) -> str:
    return _norm(company.get("status"))


def sales_rep_display_name(company: Dict[str, Any]) -> str:
    return _norm(company.get("customText6"))


def sales_rep_picker_user_id(company: Dict[str, Any]) -> Optional[int]:
    """CorporateUser id from the Sales Rep picker (`customText3`)."""
    raw = _norm(company.get("customText3"))
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    match = re.search(r"\d+", raw)
    if match:
        return int(match.group(0))
    return None


def is_company_eligible(company: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (eligible, skip_reason). Blank Type is allowed."""
    status = company_status(company)
    if status not in POSITIVE_STATUSES:
        return False, f"status_not_positive:{status or 'blank'}"

    ctype = company_type(company)
    if ctype and ctype.casefold() in {t.casefold() for t in EXCLUDED_TYPES}:
        return False, f"type_excluded:{ctype}"

    return True, ""


def parse_bh_datetime(value: Any) -> Optional[datetime]:
    """Bullhorn dateAdded is usually epoch milliseconds; ISO strings also appear."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        elif ts > 1e10:
            ts /= 1000.0
        try:
            return datetime.utcfromtimestamp(ts)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_bh_datetime(int(text))
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def is_new_company_record(
    company: Dict[str, Any], go_live_at: datetime
) -> Tuple[bool, str]:
    """Only companies created in Bullhorn at or after go-live are in scope."""
    added = parse_bh_datetime(company.get("dateAdded"))
    if added is None:
        return False, "existing_or_unknown_date_added"
    if added < go_live_at:
        return False, "existing_company"
    return True, ""


def is_interview_appointment(appointment: Dict[str, Any]) -> bool:
    appt_type = _norm(appointment.get("type"))
    return "interview" in appt_type.casefold()


def nested_entity_id(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        raw = value.get("id")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def split_display_name(name: str) -> Tuple[str, str]:
    parts = [p for p in _norm(name).split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def resolve_sales_rep(
    company: Dict[str, Any],
    *,
    fetch_user_by_id,
    search_users_by_name,
) -> Dict[str, Any]:
    """Resolve Sales Rep email.

    Prefer picker `customText3` (CorporateUser id). Fall back to matching
    display `customText6` against CorporateUser first/last name.

    Returns dict with display_name, user_id, email, source, note.
    """
    display = sales_rep_display_name(company)
    picker_id = sales_rep_picker_user_id(company)
    result = {
        "display_name": display,
        "user_id": picker_id,
        "email": None,
        "source": None,
        "note": None,
    }

    if picker_id:
        user = fetch_user_by_id(picker_id) or {}
        email = _norm(user.get("email")).lower()
        if email:
            result["email"] = email
            result["source"] = "customText3"
            result["display_name"] = display or _full_name(user) or display
            return result
        result["note"] = (
            f"Sales Rep picker user #{picker_id} has no email; "
            "Accounting is still notified."
        )

    if display:
        first, last = split_display_name(display)
        users: List[Dict[str, Any]] = search_users_by_name(first, last) or []
        enabled = [u for u in users if u.get("enabled") is not False]
        pool = enabled or users
        with_email = [u for u in pool if _norm(u.get("email"))]
        if len(with_email) == 1:
            user = with_email[0]
            result["email"] = _norm(user.get("email")).lower()
            result["user_id"] = nested_entity_id(user) or result["user_id"]
            result["source"] = "customText6"
            return result
        if not with_email:
            result["note"] = (
                f"Sales Rep display name {display!r} did not match an enabled "
                "CorporateUser with email; Accounting is still notified."
            )
        else:
            result["note"] = (
                f"Sales Rep display name {display!r} matched multiple users; "
                "Accounting is still notified (no CC)."
            )
        return result

    result["note"] = "Sales Rep not set on the company; Accounting is still notified."
    return result


def _full_name(user: Dict[str, Any]) -> str:
    first = _norm(user.get("firstName"))
    last = _norm(user.get("lastName"))
    return " ".join(p for p in (first, last) if p)
