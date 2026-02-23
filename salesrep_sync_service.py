import re
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

SOURCE_FIELD = "customText3"
DISPLAY_FIELD = "customText6"

_user_cache = {}


def _safe_str(val):
    if val is None:
        return ""
    if isinstance(val, list):
        return val[0].strip() if val else ""
    return str(val).strip()


def _is_numeric_id(value):
    if not value:
        return False
    return bool(re.match(r'^\d+$', value.strip()))


def _resolve_user(rest_url, headers, user_id):
    global _user_cache
    if user_id in _user_cache:
        return _user_cache[user_id]

    try:
        url = f"{rest_url}entity/CorporateUser/{user_id}"
        params = {"fields": "id,firstName,lastName,name"}
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        user = resp.json().get("data", {})

        first = (user.get("firstName") or "").strip()
        last = (user.get("lastName") or "").strip()
        if first and last:
            full_name = f"{first} {last}"
        elif user.get("name"):
            full_name = user["name"].strip()
        else:
            full_name = None

        _user_cache[user_id] = full_name
        return full_name
    except Exception as e:
        logger.warning(f"Failed to resolve CorporateUser {user_id}: {e}")
        _user_cache[user_id] = None
        return None


def run_salesrep_sync(bullhorn_service):
    global _user_cache
    _user_cache = {}

    start_time = datetime.utcnow()
    logger.info("🔄 Sales Rep Sync: Starting sync cycle...")

    try:
        rest_url = bullhorn_service.rest_url
        headers = bullhorn_service._get_headers()
    except Exception as e:
        logger.error(f"Sales Rep Sync: Failed to get Bullhorn connection: {e}")
        return {"success": False, "error": str(e)}

    fields = f"id,name,{SOURCE_FIELD},{DISPLAY_FIELD}"
    mismatches = []
    updated = []
    errors = []
    total_scanned = 0
    start_idx = 0
    batch_size = 200

    try:
        while True:
            url = f"{rest_url}query/ClientCorporation"
            params = {
                "where": f"{SOURCE_FIELD} IS NOT NULL AND {SOURCE_FIELD} <> ''",
                "fields": fields,
                "count": batch_size,
                "start": start_idx,
                "orderBy": "id"
            }

            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            companies = data.get("data", [])

            if not companies:
                break

            for company in companies:
                total_scanned += 1
                company_id = company.get("id")
                company_name = (company.get("name") or "").strip()
                source_val = (company.get(SOURCE_FIELD) or "").strip()
                current_display = _safe_str(company.get(DISPLAY_FIELD))

                if not _is_numeric_id(source_val):
                    continue

                resolved_name = _resolve_user(rest_url, headers, source_val)
                if not resolved_name:
                    continue

                if current_display == resolved_name:
                    continue

                mismatches.append({
                    "company_id": company_id,
                    "company_name": company_name,
                    "source_id": source_val,
                    "old_display": current_display,
                    "new_display": resolved_name
                })

                try:
                    update_url = f"{rest_url}entity/ClientCorporation/{company_id}"
                    update_resp = requests.post(
                        update_url,
                        headers={**headers, "Content-Type": "application/json"},
                        json={DISPLAY_FIELD: resolved_name},
                        timeout=15
                    )
                    update_resp.raise_for_status()
                    updated.append({
                        "company_id": company_id,
                        "company_name": company_name,
                        "old": current_display or "(empty)",
                        "new": resolved_name
                    })
                    logger.info(f"  ✅ Updated {company_name} (ID:{company_id}): '{current_display}' → '{resolved_name}'")
                except Exception as e:
                    errors.append({
                        "company_id": company_id,
                        "company_name": company_name,
                        "error": str(e)
                    })
                    logger.warning(f"  ❌ Failed to update {company_name} (ID:{company_id}): {e}")

            if len(companies) < batch_size:
                break
            start_idx += batch_size

    except Exception as e:
        logger.error(f"Sales Rep Sync: Error during scan: {e}")
        return {
            "success": False,
            "error": str(e),
            "scanned": total_scanned,
            "mismatches": len(mismatches),
            "updated": len(updated),
            "errors": len(errors)
        }

    duration = (datetime.utcnow() - start_time).total_seconds()

    if updated:
        logger.info(
            f"✅ Sales Rep Sync Complete: scanned={total_scanned}, "
            f"mismatches={len(mismatches)}, updated={len(updated)}, "
            f"errors={len(errors)}, duration={duration:.1f}s"
        )
    else:
        logger.info(
            f"✅ Sales Rep Sync Complete: scanned={total_scanned}, "
            f"no mismatches found, duration={duration:.1f}s"
        )

    return {
        "success": True,
        "scanned": total_scanned,
        "mismatches": len(mismatches),
        "updated": len(updated),
        "errors": len(errors),
        "duration_seconds": round(duration, 1),
        "details": {
            "updated": updated,
            "errors": errors
        }
    }
