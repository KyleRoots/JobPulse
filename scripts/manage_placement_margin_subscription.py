"""CLI to create / delete / inspect / manually fire the Placement margin job.

Usage:
    PYTHONPATH=. python scripts/manage_placement_margin_subscription.py ensure
    PYTHONPATH=. python scripts/manage_placement_margin_subscription.py delete
    PYTHONPATH=. python scripts/manage_placement_margin_subscription.py peek
    PYTHONPATH=. python scripts/manage_placement_margin_subscription.py compute <placement_id>
    PYTHONPATH=. python scripts/manage_placement_margin_subscription.py delete-id <subscription_id>
"""
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from bullhorn_service import BullhornService
from placement_margin.subscription import (
    delete_subscription,
    ensure_subscription,
    fetch_events,
    get_subscription_id,
)


def _cmd_ensure(bh):
    ok, desc = ensure_subscription(bh)
    print(f"ensure: ok={ok}")
    print(f"  subscription_id = {get_subscription_id()}")
    if desc:
        for k, v in desc.items():
            print(f"  {k}: {v}")
    return 0 if ok else 1


def _cmd_delete(bh):
    ok = delete_subscription(bh)
    print(f"delete: ok={ok}  id={get_subscription_id()}")
    return 0 if ok else 1


def _cmd_peek(bh):
    """Drain (and discard) any queued events — useful for dev cleanup."""
    events = fetch_events(bh)
    print(f"peek: drained {len(events)} events from {get_subscription_id()}")
    for ev in events[:20]:
        print(
            f"  - id={ev.get('eventId')} type={ev.get('eventType')} "
            f"entity={ev.get('entityName')}/{ev.get('entityId')} "
            f"changed={ev.get('updatedProperties')}"
        )
    if len(events) > 20:
        print(f"  ... and {len(events) - 20} more")
    return 0


def _cmd_compute(bh, placement_id):
    """Manually run the full pipeline against one placement — Flask-app required."""
    from app import app
    from placement_margin.worker import process_placement
    with app.app_context():
        row = process_placement(bh, int(placement_id), trigger="manual")
        print(f"compute: placement_id={placement_id}")
        print(f"  inputs: bill={row.client_bill_rate} pay={row.pay_rate} "
              f"burden={row.custom_bill_rate_1} other={row.custom_bill_rate_2}")
        print(f"  calc_status: {row.calc_status}")
        print(f"  computed_margin_pct: {row.computed_margin_pct}")
        print(f"  write_success: {row.write_success}")
        print(f"  write_skipped_reason: {row.write_skipped_reason}")
        print(f"  bullhorn_error: {row.bullhorn_error}")
        print(f"  audit_log_id: {row.id}")
    return 0 if row.write_success or row.write_skipped_reason in ("no_change",) else 1


def _cmd_delete_id(bh, sub_id):
    """Delete an arbitrary subscription by id (cleanup tool)."""
    url = f"{bh.base_url}event/subscription/{sub_id}"
    r = bh.session.delete(url, params={"BhRestToken": bh.rest_token}, timeout=30)
    print(f"delete-id: ok={r.status_code == 200} id={sub_id} status={r.status_code}")
    return 0 if r.status_code == 200 else 1


def main(argv):
    valid = {"ensure", "delete", "peek", "compute", "delete-id"}
    if len(argv) < 2 or argv[1] not in valid:
        print(__doc__)
        return 2
    bh = BullhornService()
    if not bh.authenticate():
        print("ERROR: Bullhorn authentication failed")
        return 3
    cmd = argv[1]
    if cmd == "compute":
        if len(argv) < 3:
            print("compute requires <placement_id>")
            return 2
        return _cmd_compute(bh, argv[2])
    if cmd == "delete-id":
        if len(argv) < 3:
            print("delete-id requires <subscription_id>")
            return 2
        return _cmd_delete_id(bh, argv[2])
    return {"ensure": _cmd_ensure, "delete": _cmd_delete, "peek": _cmd_peek}[cmd](bh)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
