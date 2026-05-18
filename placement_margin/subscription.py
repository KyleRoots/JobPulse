"""Bullhorn Subscription API manager for the Placement Net Margin worker.

Bullhorn's Subscription API is pull-based: we PUT a subscription once
to register interest in Placement INSERTED/UPDATED/DELETED events, then
periodically GET the subscription endpoint to drain queued events.
Events stay queued until we successfully fetch them.

Subscription IDs are environment-namespaced and brand-neutral by design
(no "scoutgenius" prefix) — see replit.md and the design conversation
for the rationale. Bullhorn admins inspecting their subscription console
should see vendor-agnostic IDs.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Brand-neutral subscription ID. The {env} segment lets dev/prod coexist
# against the same Bullhorn tenant without colliding event queues.
SUBSCRIPTION_ID_TEMPLATE = "placement-margin-calc-{env}"

# Bullhorn subscription parameters.
SUBSCRIPTION_ENTITY_NAME = "Placement"
SUBSCRIPTION_EVENT_TYPES = "INSERTED,UPDATED"
SUBSCRIPTION_TYPE = "entity"

# Maximum events to drain per poll. Bullhorn caps higher values silently;
# 100 is the documented upper bound and matches typical SaaS rate limits.
MAX_EVENTS_PER_POLL = 100


def get_subscription_id() -> str:
    """Resolve the subscription id.

    Resolution order (first non-empty wins):
        1. PLACEMENT_MARGIN_SUBSCRIPTION_ID  — explicit override
        2. placement-margin-calc-{APP_ENV}   — env-namespaced default
        3. placement-margin-calc-dev         — safe fallback

    The explicit override exists because APP_ENV is often "production"
    in both the Replit workspace AND the deployed app, which would
    collide both environments on the same Bullhorn event queue. Setting
    PLACEMENT_MARGIN_SUBSCRIPTION_ID per-environment avoids the race.
    """
    explicit = (os.environ.get("PLACEMENT_MARGIN_SUBSCRIPTION_ID") or "").strip()
    if explicit:
        return explicit
    env = (os.environ.get("APP_ENV") or "dev").lower().strip() or "dev"
    return SUBSCRIPTION_ID_TEMPLATE.format(env=env)


def _ensure_authenticated(bh) -> bool:
    """Make sure the Bullhorn client has a live REST token."""
    if bh.base_url and bh.rest_token:
        return True
    return bh.authenticate()


def ensure_subscription(bh) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """Create the Placement subscription if it doesn't already exist.

    Bullhorn's PUT endpoint is idempotent on the subscription id — if the
    id already exists with matching params it returns the existing
    descriptor; if it exists with different params it overwrites them.

    Returns:
        (success, descriptor_dict_or_None)
    """
    if not _ensure_authenticated(bh):
        logger.error("ensure_subscription: Bullhorn authentication failed")
        return False, None

    sub_id = get_subscription_id()
    url = f"{bh.base_url}event/subscription/{sub_id}"
    params = {
        "BhRestToken": bh.rest_token,
        "type": SUBSCRIPTION_TYPE,
        "names": SUBSCRIPTION_ENTITY_NAME,
        "eventTypes": SUBSCRIPTION_EVENT_TYPES,
    }

    try:
        r = bh.session.put(url, params=params, timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"ensure_subscription: PUT failed: {exc}")
        return False, None

    if r.status_code in (200, 201):
        try:
            data = r.json()
        except ValueError:
            data = None
        logger.info(f"ensure_subscription: OK id={sub_id} body={data}")
        return True, data

    # Bullhorn returns 400 with body 'Subscription "X" already exists!' on
    # PUT when the id is already registered. That's the steady-state
    # response on every poll after the first create — must be treated as
    # success, not failure, or the poller never drains events.
    body = r.text[:300]
    if r.status_code == 400 and "already exists" in body.lower():
        logger.debug(
            f"ensure_subscription: id={sub_id} already registered (idempotent)"
        )
        return True, {"already_exists": True, "subscription_id": sub_id}

    logger.error(
        f"ensure_subscription: PUT returned {r.status_code} body={body}"
    )
    return False, None


def delete_subscription(bh) -> bool:
    """Delete the placement-margin subscription (used by management CLI)."""
    if not _ensure_authenticated(bh):
        return False

    sub_id = get_subscription_id()
    url = f"{bh.base_url}event/subscription/{sub_id}"
    params = {"BhRestToken": bh.rest_token}

    try:
        r = bh.session.delete(url, params=params, timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"delete_subscription: DELETE failed: {exc}")
        return False

    if r.status_code == 200:
        logger.info(f"delete_subscription: OK id={sub_id}")
        return True
    logger.error(
        f"delete_subscription: returned {r.status_code} body={r.text[:300]}"
    )
    return False


def fetch_events(bh, max_events: int = MAX_EVENTS_PER_POLL) -> List[Dict[str, Any]]:
    """Drain queued events from the subscription.

    Bullhorn returns events in the order they occurred. Successful
    retrieval removes them from the queue (Bullhorn's at-least-once
    delivery contract). If the GET fails mid-flight, Bullhorn re-serves
    the same events on the next call.

    Returns:
        List of event dicts. Each event has keys like:
          eventId, eventType ('INSERTED'|'UPDATED'|'DELETED'),
          eventTimestamp, entityName ('Placement'),
          entityId (the placement id), entityEventType,
          updatedProperties (list of field names that changed, on UPDATED)
    """
    if not _ensure_authenticated(bh):
        return []

    sub_id = get_subscription_id()
    url = f"{bh.base_url}event/subscription/{sub_id}"
    params = {"BhRestToken": bh.rest_token, "maxEvents": max_events}

    try:
        r = bh.session.get(url, params=params, timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"fetch_events: GET failed: {exc}")
        return []

    if r.status_code == 401:
        # Token expired — re-auth once and retry.
        bh.rest_token = None
        if not bh.authenticate():
            return []
        params["BhRestToken"] = bh.rest_token
        try:
            r = bh.session.get(url, params=params, timeout=30)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"fetch_events: retry GET failed: {exc}")
            return []

    if r.status_code == 204 or not r.text.strip():
        # No events queued — normal idle state. Bullhorn returns either
        # HTTP 204 or HTTP 200 with an empty body when the queue is drained.
        return []

    if r.status_code != 200:
        # 400 with EntityNotFoundException-style body means the
        # subscription doesn't exist yet; caller should ensure_subscription().
        body = r.text[:300]
        if "EntityNotFoundException" in body or "could not be found" in body.lower():
            logger.warning(
                f"fetch_events: subscription {sub_id} not found; needs registration"
            )
        else:
            logger.error(f"fetch_events: returned {r.status_code} body={body}")
        return []

    try:
        data = r.json()
    except ValueError:
        logger.error(f"fetch_events: non-JSON body: {r.text[:300]}")
        return []

    events = data.get("events", []) or []
    if events:
        logger.info(f"fetch_events: drained {len(events)} events from {sub_id}")
    return events
