"""Find a recent Bullhorn placement to use for the end-to-end demo.

Lists the 10 most recent placements with the four input fields, so we
can pick one to run the manual `compute` command against.
"""
import sys, logging
logging.basicConfig(level=logging.WARNING)

from bullhorn_service import BullhornService

bh = BullhornService()
if not bh.authenticate():
    print("AUTH FAILED")
    sys.exit(1)

url = f"{bh.base_url}query/Placement"
params = {
    "BhRestToken": bh.rest_token,
    "where": "id IS NOT NULL",
    "fields": "id,status,dateBegin,clientBillRate,payRate,customBillRate1,customBillRate2,customFloat1",
    "count": 10,
    "orderBy": "-id",
}
r = bh.session.get(url, params=params, timeout=30)
print(f"HTTP {r.status_code}")
import json
try:
    data = r.json()
    for p in data.get("data", []):
        print(
            f"  id={p.get('id')} status={p.get('status'):20s} "
            f"bill={p.get('clientBillRate')} pay={p.get('payRate')} "
            f"burden={p.get('customBillRate1')} other={p.get('customBillRate2')} "
            f"currentMargin={p.get('customFloat1')}"
        )
except Exception as e:
    print(f"parse err: {e}")
    print(r.text[:500])
