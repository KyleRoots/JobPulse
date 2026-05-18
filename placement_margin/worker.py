"""Placement Net Margin % worker — fetches events, computes, PATCHes back.

End-to-end flow on every scheduler tick:
    1. Ensure the Bullhorn subscription exists (idempotent).
    2. Drain queued Placement INSERTED/UPDATED events.
    3. For each unique placement id:
        a. Fetch the placement record (clientBillRate, payRate,
           customBillRate1, customBillRate2, customFloat1).
        b. Compute Net Margin % via the pure calculator.
        c. PATCH customFloat1 back if the value changed.
        d. Write an audit row to placement_margin_calc_log.

The "unique placement id" coalescing matters: a single placement save
in Bullhorn can emit multiple UPDATED events (one per field touched),
and we don't want to do four PATCHes when one suffices.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from extensions import db
from models.placement_margin import PlacementMarginCalcLog
from placement_margin.calculator import (
    MarginInputs,
    MarginStatus,
    calculate_net_margin_percent,
)
from placement_margin.subscription import ensure_subscription, fetch_events

logger = logging.getLogger(__name__)


# The four input fields + the output field we read from each placement.
PLACEMENT_FIELDS = "id,clientBillRate,payRate,customBillRate1,customBillRate2,customFloat1"
OUTPUT_FIELD = "customFloat1"

# Tolerance for "did the margin change enough to re-write?" — avoids
# unnecessary PATCHes when float-rounding produces visually-identical values.
WRITE_TOLERANCE = Decimal("0.005")


def _fetch_placement(bh, placement_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a placement's input fields from Bullhorn."""
    if not bh.base_url or not bh.rest_token:
        if not bh.authenticate():
            return None
    url = f"{bh.base_url}entity/Placement/{placement_id}"
    params = {"BhRestToken": bh.rest_token, "fields": PLACEMENT_FIELDS}
    try:
        r = bh.session.get(url, params=params, timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"fetch_placement {placement_id}: {exc}")
        return None
    if r.status_code == 401:
        bh.rest_token = None
        if not bh.authenticate():
            return None
        params["BhRestToken"] = bh.rest_token
        try:
            r = bh.session.get(url, params=params, timeout=30)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"fetch_placement retry {placement_id}: {exc}")
            return None
    if r.status_code != 200:
        logger.warning(
            f"fetch_placement {placement_id}: HTTP {r.status_code} body={r.text[:200]}"
        )
        return None
    try:
        data = r.json()
    except ValueError:
        logger.error(f"fetch_placement {placement_id}: non-JSON body")
        return None
    return data.get("data") or data


def _patch_margin(bh, placement_id: int, new_value: float) -> tuple[bool, Optional[str]]:
    """PATCH customFloat1 on the placement. Returns (success, error_body)."""
    # Reuse the canonical update_entity helper (it handles 401 re-auth).
    payload = {OUTPUT_FIELD: new_value}
    try:
        ok = bh.update_entity("Placement", placement_id, payload)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return ok, None if ok else "update_entity returned False (see Bullhorn service logs)"


def _values_differ(old: Any, new: float) -> bool:
    """True if the new value is meaningfully different from what's stored."""
    if old is None:
        return True
    try:
        old_dec = Decimal(str(old))
        new_dec = Decimal(str(new))
    except Exception:  # noqa: BLE001
        return True
    return abs(new_dec - old_dec) > WRITE_TOLERANCE


def process_placement(
    bh,
    placement_id: int,
    trigger: str = "webhook",
    event_id: Optional[str] = None,
) -> PlacementMarginCalcLog:
    """Process a single placement end-to-end. Always writes an audit row.

    Returns the persisted PlacementMarginCalcLog row.
    """
    log_row = PlacementMarginCalcLog(
        bullhorn_placement_id=placement_id,
        trigger=trigger,
        bullhorn_event_id=event_id,
        calc_status=MarginStatus.INVALID_INPUT.value,  # overwritten below
    )

    placement = _fetch_placement(bh, placement_id)
    if placement is None:
        log_row.calc_status = "fetch_failed"
        log_row.write_skipped_reason = "fetch_failed"
        db.session.add(log_row)
        db.session.commit()
        return log_row

    # Snapshot inputs into the audit row (decimals preserved).
    bill = placement.get("clientBillRate")
    pay = placement.get("payRate")
    burden = placement.get("customBillRate1")
    other = placement.get("customBillRate2")
    current_output = placement.get(OUTPUT_FIELD)

    # Safe coercion: Bullhorn occasionally returns blank strings or unexpected
    # types for numeric fields. We must NEVER lose the audit row over a bad
    # input — log what we got, let the calculator return INVALID_INPUT, and
    # persist the row so Finance can trace what happened.
    def _safe_dec(v):
        if v is None or v == "":
            return None
        try:
            return Decimal(str(v))
        except Exception:  # noqa: BLE001 — any coercion failure → None + flag
            return None

    log_row.client_bill_rate = _safe_dec(bill)
    log_row.pay_rate = _safe_dec(pay)
    log_row.custom_bill_rate_1 = _safe_dec(burden)
    log_row.custom_bill_rate_2 = _safe_dec(other)

    # Feed the already-coerced Decimals to the calculator so a malformed
    # raw Bullhorn value (e.g. "N/A") deterministically becomes INVALID_INPUT
    # rather than risking an exception that loses the audit row.
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=log_row.client_bill_rate,
            pay_rate=log_row.pay_rate,
            custom_bill_rate_1=log_row.custom_bill_rate_1,
            custom_bill_rate_2=log_row.custom_bill_rate_2,
        )
    )
    log_row.calc_status = result.status.value
    log_row.computed_margin_pct = (
        Decimal(str(result.value)) if result.value is not None else None
    )

    if result.status != MarginStatus.OK:
        # Don't PATCH null/error states — preserves existing customFloat1
        # value (might be a known-good prior calc) and avoids overwriting
        # with NULL when an UPDATED event happens to land while inputs
        # are temporarily incomplete (mid-edit).
        log_row.write_skipped_reason = f"calc_status:{result.status.value}"
        db.session.add(log_row)
        db.session.commit()
        return log_row

    if not _values_differ(current_output, result.value):
        log_row.write_skipped_reason = "no_change"
        db.session.add(log_row)
        db.session.commit()
        return log_row

    ok, err = _patch_margin(bh, placement_id, result.value)
    log_row.write_success = ok
    log_row.bullhorn_error = err

    db.session.add(log_row)
    db.session.commit()
    return log_row


def poll_and_process(bh) -> Dict[str, int]:
    """One scheduler tick: ensure subscription, drain events, process each.

    Returns a small summary dict for logging / health tile use.
    """
    summary = {
        "events_drained": 0,
        "placements_processed": 0,
        "ok": 0,
        "skipped": 0,
        "failed": 0,
    }

    ok, _desc = ensure_subscription(bh)
    if not ok:
        logger.error("poll_and_process: subscription not ensured; skipping tick")
        return summary

    events = fetch_events(bh)
    summary["events_drained"] = len(events)
    if not events:
        return summary

    # Coalesce by placement id — one save often emits multiple per-field events.
    # Keep the most recent event id per placement for the audit trail.
    by_placement: Dict[int, str] = {}
    for ev in events:
        if ev.get("entityName") != "Placement":
            continue
        pid = ev.get("entityId")
        if pid is None:
            continue
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        by_placement[pid_int] = str(ev.get("eventId", ""))

    summary["placements_processed"] = len(by_placement)

    for pid, eid in by_placement.items():
        try:
            row = process_placement(bh, pid, trigger="webhook", event_id=eid)
            if row.write_success is True:
                summary["ok"] += 1
            elif row.write_success is False:
                summary["failed"] += 1
            else:
                summary["skipped"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"poll_and_process: placement {pid} raised: {exc}")
            summary["failed"] += 1

    return summary
