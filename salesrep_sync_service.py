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


def _auth_params(bullhorn_service, **extra):
    """Bullhorn REST expects BhRestToken as a query param, not only a header.

    Putting the token only in headers produced HTTP 400 on query/ClientCorporation
    (URL had no BhRestToken). Matches bullhorn_service.query_entity / entity calls.
    """
    params = {'BhRestToken': bullhorn_service.rest_token}
    params.update(extra)
    return params


def _redact_token(text, token):
    if not text or not token:
        return text
    return text.replace(token, "[REDACTED]")


def _safe_error(exc, bullhorn_service):
    """Strip BhRestToken from exception text (requests embeds full URL)."""
    return _redact_token(str(exc), getattr(bullhorn_service, "rest_token", None))


def _check_response(resp, context):
    """Raise without requests' URL-bearing HTTPError (would leak BhRestToken in logs)."""
    if resp.status_code >= 400:
        body = (resp.text or "")[:200]
        raise RuntimeError(f"{context}: HTTP {resp.status_code} {body}")


def _resolve_user(rest_url, bullhorn_service, user_id):
    global _user_cache
    if user_id in _user_cache:
        return _user_cache[user_id]

    try:
        url = f"{rest_url}entity/CorporateUser/{user_id}"
        params = _auth_params(bullhorn_service, fields="id,firstName,lastName,name")
        resp = requests.get(url, params=params, timeout=15)
        _check_response(resp, f"CorporateUser {user_id} lookup")
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
        logger.warning(
            f"Failed to resolve CorporateUser {user_id}: "
            f"{_safe_error(e, bullhorn_service)}"
        )
        _user_cache[user_id] = None
        return None


def run_salesrep_sync(bullhorn_service, source_field=None, display_field=None):
    global _user_cache
    _user_cache = {}

    # Per-environment field names fall back to the historical Myticas defaults
    # (customText3 → customText6) when not supplied, so existing callers and the
    # default environment behave byte-for-byte as before.
    source_field = (source_field or "").strip() or SOURCE_FIELD
    display_field = (display_field or "").strip() or DISPLAY_FIELD

    start_time = datetime.utcnow()
    logger.info(
        f"🔄 Sales Rep Sync: Starting sync cycle "
        f"(source={source_field}, display={display_field})..."
    )

    try:
        if not bullhorn_service.rest_token or not bullhorn_service.base_url:
            bullhorn_service.rest_token = None
            bullhorn_service.base_url = None
            bullhorn_service.authenticate()
        rest_url = bullhorn_service.base_url
        if not rest_url.endswith('/'):
            rest_url = rest_url + '/'
    except Exception as e:
        logger.error(f"Sales Rep Sync: Failed to get Bullhorn connection: {e}")
        return {"success": False, "error": str(e)}

    fields = f"id,name,{source_field},{display_field}"
    mismatches = []
    updated = []
    errors = []
    total_scanned = 0
    start_idx = 0
    batch_size = 200

    try:
        while True:
            # ClientCorporation customText is Lucene-indexed; /query/ BQL rejects
            # ``<> ''`` (and often IS NOT NULL) on customText fields → HTTP 400.
            # Search with field:* returns non-empty values; empty/non-numeric IDs
            # are skipped client-side below.
            url = f"{rest_url}search/ClientCorporation"
            params = _auth_params(
                bullhorn_service,
                query=f"{source_field}:*",
                fields=fields,
                count=batch_size,
                start=start_idx,
                sort="id",
            )

            resp = requests.get(url, params=params, timeout=30)
            _check_response(resp, "Sales Rep Sync scan")
            data = resp.json()
            companies = data.get("data", [])

            if not companies:
                break

            for company in companies:
                total_scanned += 1
                company_id = company.get("id")
                company_name = (company.get("name") or "").strip()
                source_val = (company.get(source_field) or "").strip()
                current_display = _safe_str(company.get(display_field))

                if not _is_numeric_id(source_val):
                    continue

                resolved_name = _resolve_user(rest_url, bullhorn_service, source_val)
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
                        params=_auth_params(bullhorn_service),
                        headers={"Content-Type": "application/json", "Accept": "application/json"},
                        json={display_field: resolved_name},
                        timeout=15
                    )
                    _check_response(update_resp, f"Update ClientCorporation {company_id}")
                    updated.append({
                        "company_id": company_id,
                        "company_name": company_name,
                        "old": current_display or "(empty)",
                        "new": resolved_name
                    })
                    logger.info(f"  ✅ Updated {company_name} (ID:{company_id}): '{current_display}' → '{resolved_name}'")
                except Exception as e:
                    safe_err = _safe_error(e, bullhorn_service)
                    errors.append({
                        "company_id": company_id,
                        "company_name": company_name,
                        "error": safe_err
                    })
                    logger.warning(
                        f"  ❌ Failed to update {company_name} (ID:{company_id}): {safe_err}"
                    )

            if len(companies) < batch_size:
                break
            start_idx += batch_size

    except Exception as e:
        safe_err = _safe_error(e, bullhorn_service)
        logger.error(f"Sales Rep Sync: Error during scan: {safe_err}")
        return {
            "success": False,
            "error": safe_err,
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
