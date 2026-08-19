"""Drain Sendout + Interview events and email Finance once per company.

Observe-only: never PATCHes Bullhorn. Historical records are not scanned —
Bullhorn only queues events after the subscription is registered.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from client_onboarding_notify.config import accounting_email, is_enabled, is_live
from client_onboarding_notify.eligibility import (
    company_status,
    company_type,
    is_company_eligible,
    is_interview_appointment,
    nested_entity_id,
    resolve_sales_rep,
)
from client_onboarding_notify.email import build_recipients, send_notify_email
from client_onboarding_notify.subscription import (
    appointment_subscription_id,
    ensure_both_subscriptions,
    fetch_events,
    sendout_subscription_id,
)
from extensions import db
from models.client_onboarding_notify import ClientOnboardingNotifyLog

logger = logging.getLogger(__name__)

COMPANY_FIELDS = "id,name,status,customText1,customText3,customText6"
SENDOUT_FIELDS = (
    "id,clientCorporation,jobOrder(id,title,clientCorporation),user,"
    "candidate(id,firstName,lastName)"
)
APPOINTMENT_FIELDS = (
    "id,type,subject,jobOrder(id,title,clientCorporation),"
    "clientContactReference(id,clientCorporation),"
    "candidateReference(id,firstName,lastName)"
)
USER_FIELDS = "id,firstName,lastName,email,enabled"
BH_COMPANY_URL = (
    "https://cls45.bullhornstaffing.com/BullhornStaffing/"
    "OpenWindow.cfm?Entity=ClientCorporation&id={id}"
)


def _bh_get(bh, path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not bh.base_url or not bh.rest_token:
        if not bh.authenticate():
            return None
    url = f"{bh.base_url}{path.lstrip('/')}"
    params = {**params, "BhRestToken": bh.rest_token}
    try:
        r = bh.session.get(url, params=params, timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"client_ob GET {path} failed: {exc}")
        return None
    if r.status_code == 401:
        bh.rest_token = None
        if not bh.authenticate():
            return None
        params["BhRestToken"] = bh.rest_token
        try:
            r = bh.session.get(url, params=params, timeout=30)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"client_ob GET retry {path} failed: {exc}")
            return None
    if r.status_code != 200:
        logger.warning(f"client_ob GET {path}: HTTP {r.status_code} {r.text[:200]}")
        return None
    try:
        data = r.json()
    except ValueError:
        logger.error(f"client_ob GET {path}: non-JSON")
        return None
    return data.get("data") if isinstance(data, dict) and "data" in data else data


def fetch_company(bh, company_id: int) -> Optional[Dict[str, Any]]:
    return _bh_get(bh, f"entity/ClientCorporation/{company_id}", {"fields": COMPANY_FIELDS})


def fetch_sendout(bh, sendout_id: int) -> Optional[Dict[str, Any]]:
    return _bh_get(bh, f"entity/Sendout/{sendout_id}", {"fields": SENDOUT_FIELDS})


def fetch_appointment(bh, appointment_id: int) -> Optional[Dict[str, Any]]:
    return _bh_get(bh, f"entity/Appointment/{appointment_id}", {"fields": APPOINTMENT_FIELDS})


def fetch_user_by_id(bh, user_id: int) -> Optional[Dict[str, Any]]:
    return _bh_get(bh, f"entity/CorporateUser/{user_id}", {"fields": USER_FIELDS})


def search_users_by_name(bh, first: str, last: str) -> List[Dict[str, Any]]:
    if not first or not last:
        return []
    # Escape Lucene reserved chars in names.
    safe_first = first.replace(":", " ").replace('"', "")
    safe_last = last.replace(":", " ").replace('"', "")
    query = f'firstName:"{safe_first}" AND lastName:"{safe_last}"'
    data = _bh_get(
        bh,
        "search/CorporateUser",
        {"query": query, "fields": USER_FIELDS, "count": 5},
    )
    if not data:
        return []
    if isinstance(data, list):
        return data
    return []


def _person_name(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    first = str(obj.get("firstName") or "").strip()
    last = str(obj.get("lastName") or "").strip()
    return " ".join(p for p in (first, last) if p)


def company_id_from_sendout(sendout: Dict[str, Any]) -> Optional[int]:
    cid = nested_entity_id(sendout.get("clientCorporation"))
    if cid:
        return cid
    job = sendout.get("jobOrder") if isinstance(sendout.get("jobOrder"), dict) else {}
    return nested_entity_id((job or {}).get("clientCorporation"))


def company_id_from_appointment(appt: Dict[str, Any]) -> Optional[int]:
    job = appt.get("jobOrder") if isinstance(appt.get("jobOrder"), dict) else {}
    cid = nested_entity_id((job or {}).get("clientCorporation"))
    if cid:
        return cid
    contact = (
        appt.get("clientContactReference")
        if isinstance(appt.get("clientContactReference"), dict)
        else {}
    )
    return nested_entity_id((contact or {}).get("clientCorporation"))


def _already_notified(company_id: int) -> bool:
    return (
        ClientOnboardingNotifyLog.query.filter_by(bullhorn_company_id=company_id).first()
        is not None
    )


def _record_outcome(
    *,
    company_id: int,
    company: Optional[Dict[str, Any]],
    trigger_type: str,
    trigger_entity_id: Optional[int],
    sales: Optional[Dict[str, Any]],
    skip_reason: Optional[str],
    email_sent: bool,
    notes: Optional[str],
) -> Optional[ClientOnboardingNotifyLog]:
    recipients = build_recipients((sales or {}).get("email"))
    row = ClientOnboardingNotifyLog(
        bullhorn_company_id=company_id,
        company_name=(company or {}).get("name") if company else None,
        company_status=company_status(company or {}) if company else None,
        company_type=company_type(company or {}) if company else None,
        trigger_type=trigger_type,
        trigger_entity_id=trigger_entity_id,
        sales_rep_name=(sales or {}).get("display_name"),
        sales_rep_email=(sales or {}).get("email"),
        sales_rep_source=(sales or {}).get("source"),
        intended_to=recipients["intended_to"],
        intended_cc=recipients["intended_cc"],
        actual_to=recipients["to"] if email_sent else None,
        live_mode=is_live(),
        email_sent=email_sent,
        skip_reason=skip_reason,
        notes=notes or (sales or {}).get("note"),
    )
    db.session.add(row)
    try:
        db.session.commit()
        return row
    except IntegrityError:
        db.session.rollback()
        logger.info(f"client_ob already ledgered company={company_id}")
        return None


def process_company(
    bh,
    company_id: int,
    *,
    trigger_type: str,
    trigger_entity_id: Optional[int],
    job_title: str = "",
    candidate_name: str = "",
    trigger_label: str = "",
) -> str:
    """Evaluate one company. Returns outcome token."""
    if _already_notified(company_id):
        return "already_notified"

    company = fetch_company(bh, company_id)
    if not company:
        logger.warning(f"client_ob company {company_id} not fetched; will retry next event")
        return "company_fetch_failed"

    eligible, reason = is_company_eligible(company)
    if not eligible:
        # Do not ledger skips — a Vendor that later becomes a Client
        # should still get the Finance email on the next trigger.
        logger.info(f"client_ob skip company={company_id} reason={reason}")
        return f"skipped:{reason}"

    sales = resolve_sales_rep(
        company,
        fetch_user_by_id=lambda uid: fetch_user_by_id(bh, uid),
        search_users_by_name=lambda first, last: search_users_by_name(bh, first, last),
    )
    context = {
        "company_id": company_id,
        "company_name": (company.get("name") or "").strip(),
        "company_status": company_status(company),
        "company_type": company_type(company),
        "company_url": BH_COMPANY_URL.format(id=company_id),
        "trigger_type": trigger_type,
        "trigger_label": trigger_label or trigger_type,
        "job_title": job_title,
        "candidate_name": candidate_name,
        "sales_rep_name": sales.get("display_name"),
        "sales_rep_email": sales.get("email"),
        "sales_rep_note": sales.get("note"),
        "intended_to": accounting_email(),
    }
    sent = send_notify_email(context)
    if not sent:
        # Leave un-ledgered so the next Sendout/Interview retries.
        return "email_failed"
    _record_outcome(
        company_id=company_id,
        company=company,
        trigger_type=trigger_type,
        trigger_entity_id=trigger_entity_id,
        sales=sales,
        skip_reason=None,
        email_sent=True,
        notes=sales.get("note"),
    )
    return "sent"


def _job_title(entity: Dict[str, Any]) -> str:
    job = entity.get("jobOrder") if isinstance(entity.get("jobOrder"), dict) else {}
    return str((job or {}).get("title") or "").strip()


def handle_sendout_event(bh, entity_id: int) -> str:
    sendout = fetch_sendout(bh, entity_id)
    if not sendout:
        return "sendout_fetch_failed"
    company_id = company_id_from_sendout(sendout)
    if not company_id:
        logger.info(f"client_ob sendout {entity_id} has no company; skip")
        return "no_company"
    return process_company(
        bh,
        company_id,
        trigger_type="sendout",
        trigger_entity_id=entity_id,
        job_title=_job_title(sendout),
        candidate_name=_person_name(sendout.get("candidate")),
        trigger_label="Client Submission (Sendout)",
    )


def handle_appointment_event(bh, entity_id: int) -> str:
    appt = fetch_appointment(bh, entity_id)
    if not appt:
        return "appointment_fetch_failed"
    if not is_interview_appointment(appt):
        return "not_interview"
    company_id = company_id_from_appointment(appt)
    if not company_id:
        logger.info(f"client_ob appointment {entity_id} has no company; skip")
        return "no_company"
    return process_company(
        bh,
        company_id,
        trigger_type="interview",
        trigger_entity_id=entity_id,
        job_title=_job_title(appt),
        candidate_name=_person_name(appt.get("candidateReference")),
        trigger_label="Interview appointment",
    )


def poll_and_process(bh) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "enabled": is_enabled(),
        "live": is_live(),
        "events_drained": 0,
        "outcomes": {},
    }
    if not is_enabled():
        return summary

    if not ensure_both_subscriptions(bh):
        summary["subscription_ok"] = False
        return summary
    summary["subscription_ok"] = True

    sendout_events = fetch_events(bh, sendout_subscription_id())
    appt_events = fetch_events(bh, appointment_subscription_id())
    summary["events_drained"] = len(sendout_events) + len(appt_events)

    counts: Dict[str, int] = {}

    def _count(token: str) -> None:
        counts[token] = counts.get(token, 0) + 1

    for ev in sendout_events:
        entity_id = nested_entity_id(ev.get("entityId"))
        if not entity_id:
            continue
        _count(handle_sendout_event(bh, entity_id))

    for ev in appt_events:
        entity_id = nested_entity_id(ev.get("entityId"))
        if not entity_id:
            continue
        _count(handle_appointment_event(bh, entity_id))

    summary["outcomes"] = counts
    return summary
