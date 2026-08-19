"""Bullhorn event subscriptions for Sendout + Appointment INSERTED.

Two subscription IDs (Bullhorn PUT `names` is a single entity). Namespaced
by APP_ENV like placement-margin so prod/dev queues do not collide.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SENDOUT_SUB_TEMPLATE = "client-ob-sendout-{env}"
APPOINTMENT_SUB_TEMPLATE = "client-ob-appointment-{env}"
MAX_EVENTS_PER_POLL = 100


def _env_slug() -> str:
    return (os.environ.get("APP_ENV") or "dev").lower().strip() or "dev"


def sendout_subscription_id() -> str:
    explicit = (os.environ.get("CLIENT_OB_SENDOUT_SUBSCRIPTION_ID") or "").strip()
    if explicit:
        return explicit
    return SENDOUT_SUB_TEMPLATE.format(env=_env_slug())


def appointment_subscription_id() -> str:
    explicit = (os.environ.get("CLIENT_OB_APPOINTMENT_SUBSCRIPTION_ID") or "").strip()
    if explicit:
        return explicit
    return APPOINTMENT_SUB_TEMPLATE.format(env=_env_slug())


def _ensure_authenticated(bh) -> bool:
    if bh.base_url and bh.rest_token:
        return True
    return bh.authenticate()


def ensure_subscription(bh, sub_id: str, entity_name: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    if not _ensure_authenticated(bh):
        logger.error("client_ob ensure_subscription: Bullhorn authentication failed")
        return False, None

    url = f"{bh.base_url}event/subscription/{sub_id}"
    params = {
        "BhRestToken": bh.rest_token,
        "type": "entity",
        "names": entity_name,
        "eventTypes": "INSERTED",
    }
    try:
        r = bh.session.put(url, params=params, timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"client_ob ensure_subscription PUT failed id={sub_id}: {exc}")
        return False, None

    if r.status_code in (200, 201):
        try:
            data = r.json()
        except ValueError:
            data = None
        logger.info(f"client_ob ensure_subscription OK id={sub_id} entity={entity_name}")
        return True, data

    body = r.text[:300]
    if r.status_code == 400 and "already exists" in body.lower():
        return True, {"already_exists": True, "subscription_id": sub_id}

    logger.error(
        f"client_ob ensure_subscription PUT {r.status_code} id={sub_id} body={body}"
    )
    return False, None


def ensure_both_subscriptions(bh) -> bool:
    ok_sendout, _ = ensure_subscription(bh, sendout_subscription_id(), "Sendout")
    ok_appt, _ = ensure_subscription(bh, appointment_subscription_id(), "Appointment")
    return ok_sendout and ok_appt


def fetch_events(bh, sub_id: str, max_events: int = MAX_EVENTS_PER_POLL) -> List[Dict[str, Any]]:
    if not _ensure_authenticated(bh):
        return []

    url = f"{bh.base_url}event/subscription/{sub_id}"
    params = {"BhRestToken": bh.rest_token, "maxEvents": max_events}

    try:
        r = bh.session.get(url, params=params, timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"client_ob fetch_events GET failed id={sub_id}: {exc}")
        return []

    if r.status_code == 401:
        bh.rest_token = None
        if not bh.authenticate():
            return []
        params["BhRestToken"] = bh.rest_token
        try:
            r = bh.session.get(url, params=params, timeout=30)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"client_ob fetch_events retry GET failed id={sub_id}: {exc}")
            return []

    if r.status_code == 204:
        return []
    if r.status_code == 200 and not r.text.strip():
        return []

    if r.status_code != 200:
        body = r.text[:300]
        if "EntityNotFoundException" in body or "could not be found" in body.lower():
            logger.warning(f"client_ob fetch_events: subscription {sub_id} not found")
        else:
            logger.error(
                f"client_ob fetch_events: {r.status_code} id={sub_id} body={body or '<empty>'}"
            )
        return []

    try:
        data = r.json()
    except ValueError:
        logger.error(f"client_ob fetch_events non-JSON id={sub_id}: {r.text[:300]}")
        return []

    events = data.get("events", []) or []
    if events:
        logger.info(f"client_ob fetch_events: drained {len(events)} from {sub_id}")
    return events
