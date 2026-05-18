"""One-shot probe: does our Bullhorn tenant expose the Subscription API?"""
import os, sys, logging
logging.basicConfig(level=logging.WARNING)

from bullhorn_service import BullhornService

svc = BullhornService()
if not svc.authenticate():
    print("AUTH_FAILED")
    sys.exit(1)

print(f"Authenticated. base_url={svc.base_url}")

# Test 1: Try to GET an unlikely subscription id. 
# - 404 / "subscription not found" => endpoint EXISTS, feature available
# - 401/403 => feature not licensed or no permission
# - other => investigate
import uuid
probe_id = f"scoutgenius-probe-{uuid.uuid4().hex[:8]}"
url = f"{svc.base_url}event/subscription/{probe_id}"
params = {'BhRestToken': svc.rest_token, 'maxEvents': 1}

print(f"\n--- Test 1: GET {url}")
r = svc.session.get(url, params=params, timeout=20)
print(f"Status: {r.status_code}")
print(f"Body (first 500): {r.text[:500]}")

# Test 2: Try to CREATE a subscription for Placement events (read-only intent — we'll DELETE it right after)
print(f"\n--- Test 2: PUT (create) subscription for Placement entity")
create_url = f"{svc.base_url}event/subscription/{probe_id}"
create_params = {
    'BhRestToken': svc.rest_token,
    'type': 'entity',
    'names': 'Placement',
    'eventTypes': 'INSERTED,UPDATED,DELETED',
}
r2 = svc.session.put(create_url, params=create_params, timeout=20)
print(f"Status: {r2.status_code}")
print(f"Body (first 500): {r2.text[:500]}")

# Test 3: Clean up if we created one
if r2.status_code in (200, 201):
    print(f"\n--- Test 3: DELETE probe subscription (cleanup)")
    r3 = svc.session.delete(create_url, params={'BhRestToken': svc.rest_token}, timeout=20)
    print(f"Status: {r3.status_code}")
    print(f"Body: {r3.text[:300]}")

print("\n=== VERDICT ===")
if r2.status_code in (200, 201):
    print("SUBSCRIPTION_API_ENABLED — ideal path is GO.")
elif r2.status_code in (401, 403):
    print("SUBSCRIPTION_API_BLOCKED — likely not licensed/permissioned. Fallback needed + support ticket.")
elif r2.status_code == 404:
    print("SUBSCRIPTION_API_ENDPOINT_NOT_FOUND — feature not enabled on tenant. Fallback needed + support ticket.")
else:
    print(f"UNCLEAR (status={r2.status_code}) — need manual review.")
