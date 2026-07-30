"""Normalize incorrect Bullhorn Candidate country fields from resume evidence."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

import requests

from utils.candidate_country import (
    COUNTRIES,
    CountryResolution,
    country_definition,
    infer_country_from_resume,
)


logger = logging.getLogger(__name__)

SEARCH_FIELDS = (
    "id,firstName,lastName,status,source,dateAdded,dateLastModified,description,"
    "address(address1,address2,city,state,zip,countryID,countryCode,countryName)"
)
ADDRESS_WRITE_FIELDS = ("address1", "address2", "city", "state", "zip")

# Canadian provinces/territories that are not US state codes. Used only to
# prioritize the high-ROI wrong-US cohort; inference still requires résumé
# corroboration before any write.
CANADA_BACKFILL_STATES = (
    "ON", "BC", "AB", "QC", "NS", "MB", "SK", "NB", "NL", "PE", "NU", "YT",
)
BACKFILL_PHASES = ("canada", "historical", "done")


def _country_ids_for_environment(bullhorn) -> Dict[str, int]:
    """Overlay verified static IDs with this environment's Country options."""
    country_ids = {
        name: definition.bullhorn_id
        for name, definition in COUNTRIES.items()
    }
    try:
        options = bullhorn.get_options("Country") or []
        for option in options:
            label = (
                option.get("label")
                or option.get("name")
                or option.get("display")
                or option.get("description")
            )
            definition = country_definition(label)
            raw_id = (
                option.get("id")
                or option.get("value")
                or option.get("countryID")
            )
            if definition and raw_id is not None and str(raw_id).isdigit():
                country_ids[definition.name] = int(raw_id)
    except Exception:
        logger.warning(
            "candidate_country_normalizer: Country options unavailable; "
            "using verified Bullhorn standard IDs",
            exc_info=True,
        )
    return country_ids


def _require_audit_table() -> None:
    """Fail closed before remote writes if the audit table is unavailable."""
    from models import CandidateCountryCorrectionLog

    # A bounded primary-key projection is portable across SQLite/Postgres and
    # proves the model/table can be queried without loading sensitive rows.
    CandidateCountryCorrectionLog.query.with_entities(
        CandidateCountryCorrectionLog.id
    ).limit(1).all()


def _lookback_floor_ms(lookback_hours: float) -> int:
    """Epoch-ms floor for the configured lookback window."""
    since = datetime.utcnow() - timedelta(hours=max(1.0, lookback_hours))
    return int(since.timestamp() * 1000)


def _candidate_date_added_ms(candidate: Dict) -> int:
    """Best-effort Candidate dateAdded as epoch milliseconds."""
    try:
        return int(candidate.get("dateAdded") or 0)
    except (TypeError, ValueError):
        return 0


def _canada_backfill_query(*, after_id: int = 0) -> str:
    """Lucene query for US-default candidates with Canadian province codes."""
    state_clause = " OR ".join(
        f"address.state:{state}" for state in CANADA_BACKFILL_STATES
    )
    return (
        "isDeleted:0 AND -status:Archive AND address.countryID:1 "
        f"AND ({state_clause}) AND id:[{max(0, int(after_id)) + 1} TO *]"
    )


def _historical_backfill_query(
    *,
    before_ms: int,
    after_ms: int = 0,
) -> str:
    """Lucene query for older US-default candidates outside the live window.

    Canadian-province rows are excluded so the canada phase owns that cohort.
    Inference still decides whether any overseas/other mapping is safe.
    """
    excluded = " AND ".join(
        f"-address.state:{state}" for state in CANADA_BACKFILL_STATES
    )
    lower = max(0, int(after_ms))
    upper = max(lower, int(before_ms))
    return (
        "isDeleted:0 AND -status:Archive AND address.countryID:1 "
        f"AND {excluded} AND dateAdded:[{lower} TO {upper}]"
    )


def _search_candidates(
    bullhorn,
    *,
    query: str,
    limit: int,
    sort: str,
) -> List[Dict]:
    """Page a bounded Candidate search, re-authenticating once on 401."""
    url = f"{bullhorn.base_url}search/Candidate"
    maximum = max(1, min(int(limit), 2000))
    params = {
        "query": query,
        "fields": SEARCH_FIELDS,
        "count": min(maximum, 500),
        "start": 0,
        "sort": sort,
    }

    def request():
        headers = {
            "BhRestToken": bullhorn.rest_token,
            "Accept": "application/json",
        }
        return requests.get(url, headers=headers, params=dict(params), timeout=45)

    candidates = []
    while len(candidates) < maximum:
        params["start"] = len(candidates)
        params["count"] = min(500, maximum - len(candidates))
        response = request()
        if response.status_code == 401:
            bullhorn.rest_token = None
            if not bullhorn.authenticate():
                raise RuntimeError("Bullhorn re-authentication failed")
            response = request()
        response.raise_for_status()
        body = response.json()
        if body.get("errorCode") or body.get("errors"):
            raise RuntimeError(
                "Bullhorn candidate search error: "
                f"{body.get('errors') or body.get('errorMessage')}"
            )
        page = body.get("data", [])
        candidates.extend(page)
        total = int(body.get("total", len(candidates)) or 0)
        if not page or len(candidates) >= total:
            break
    return candidates[:maximum]


def _recent_candidates(
    bullhorn,
    *,
    lookback_hours: float,
    limit: int,
    since_ms: Optional[int] = None,
) -> List[Dict]:
    """Fetch a bounded batch of recent, non-archived candidates.

    Production incident (Jul 30 2026): an ascending search briefly returned
    ancient rows, the high-water cursor parked at year 2000, and later cycles
    queried ``dateAdded:[950162400000 TO *]`` which Bullhorn answered with
    zero hits — permanently disabling corrections. Always clamp the cursor to
    the lookback floor and discard out-of-window rows before processing.
    """
    floor_ms = _lookback_floor_ms(lookback_hours)
    if since_ms is None or int(since_ms) < floor_ms:
        effective_since = floor_ms
        sort = "-dateAdded"
    else:
        # Resume inside the live window so a volume spike cannot starve older
        # in-window candidates forever.
        effective_since = int(since_ms) + 1
        sort = "dateAdded"

    recent_query = (
        f"isDeleted:0 AND -status:Archive AND dateAdded:[{effective_since} TO *]"
    )
    candidates = _search_candidates(
        bullhorn,
        query=recent_query,
        limit=limit,
        sort=sort,
    )
    in_window = [
        candidate
        for candidate in candidates
        if _candidate_date_added_ms(candidate) >= floor_ms
    ]
    if candidates and not in_window:
        logger.error(
            "candidate_country_normalizer: Bullhorn returned %s candidates "
            "older than lookback floor_ms=%s (since_ms=%s sort=%s); "
            "retrying newest-first inside the lookback window",
            len(candidates),
            floor_ms,
            effective_since,
            sort,
        )
        candidates = _search_candidates(
            bullhorn,
            query=(
                "isDeleted:0 AND -status:Archive "
                f"AND dateAdded:[{floor_ms} TO *]"
            ),
            limit=limit,
            sort="-dateAdded",
        )
        in_window = [
            candidate
            for candidate in candidates
            if _candidate_date_added_ms(candidate) >= floor_ms
        ]
    elif len(in_window) != len(candidates):
        logger.warning(
            "candidate_country_normalizer: dropped %s out-of-window "
            "candidates (floor_ms=%s)",
            len(candidates) - len(in_window),
            floor_ms,
        )
    return in_window


def _candidate_id(candidate: Dict) -> int:
    try:
        return int(candidate.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _backfill_candidates(
    bullhorn,
    *,
    phase: str,
    limit: int,
    after_id: int = 0,
    before_ms: Optional[int] = None,
    after_ms: int = 0,
) -> List[Dict]:
    """Fetch one bounded backfill page for the active phase."""
    normalized_phase = (phase or "").strip().lower()
    if normalized_phase == "canada":
        return _search_candidates(
            bullhorn,
            query=_canada_backfill_query(after_id=after_id),
            limit=limit,
            sort="id",
        )
    if normalized_phase == "historical":
        if before_ms is None:
            before_ms = _lookback_floor_ms(48.0) - 1
        return _search_candidates(
            bullhorn,
            query=_historical_backfill_query(
                before_ms=before_ms,
                after_ms=after_ms,
            ),
            limit=limit,
            # Newest-first inside the historical window so recent overseas
            # wrong-US defaults are repaired before ancient noise.
            sort="-dateAdded",
        )
    return []


def _audit(
    candidate: Dict,
    resolution: CountryResolution,
    *,
    previous_country: Optional[str],
    previous_country_id: Optional[int],
    trigger: str,
    status: str,
    environment_id: Optional[int] = None,
    corrected_country_id: Optional[int] = None,
    error_message: Optional[str] = None,
) -> bool:
    """Persist a correction attempt without ever breaking the main cycle."""
    try:
        from extensions import db
        from models import CandidateCountryCorrectionLog

        address = candidate.get("address") or {}
        row = CandidateCountryCorrectionLog(
            environment_id=environment_id,
            bullhorn_candidate_id=int(candidate["id"]),
            state=str(address.get("state") or "")[:255] or None,
            previous_country_name=str(previous_country or "")[:255] or None,
            previous_country_id=previous_country_id,
            corrected_country_name=resolution.country.name,
            corrected_country_id=(
                corrected_country_id
                if corrected_country_id is not None
                else resolution.country.bullhorn_id
            ),
            confidence=resolution.confidence,
            evidence=resolution.evidence,
            trigger=trigger[:50],
            status=status[:30],
            error_message=error_message,
        )
        db.session.add(row)
        db.session.commit()
        return True
    except Exception:
        try:
            from extensions import db
            db.session.rollback()
        except Exception:
            pass
        logger.exception(
            "candidate_country_normalizer: failed to persist audit for candidate %s",
            candidate.get("id"),
        )
        return False


def normalize_candidate_country(
    candidate: Dict,
    bullhorn,
    *,
    dry_run: bool = False,
    trigger: str = "scheduled",
    environment_id: Optional[int] = None,
    country_ids: Optional[Dict[str, int]] = None,
) -> Dict:
    """Correct one candidate if high-confidence resume evidence contradicts Bullhorn."""
    candidate_id = candidate.get("id")
    address = candidate.get("address") or {}
    if not candidate_id or not isinstance(address, dict):
        return {"status": "skipped", "reason": "missing candidate/address"}

    resolution = infer_country_from_resume(
        address.get("city"),
        address.get("state"),
        candidate.get("description"),
    )
    if not resolution or resolution.confidence != "high":
        return {"status": "skipped", "reason": "no high-confidence resume evidence"}
    target_country_id = (country_ids or {}).get(
        resolution.country.name,
        resolution.country.bullhorn_id,
    )

    current_name = address.get("countryName")
    current_id = address.get("countryID")
    current_by_name = country_definition(
        current_name or address.get("countryCode")
    )
    if (
        current_by_name
        and current_by_name.name == resolution.country.name
        and current_id == target_country_id
    ):
        return {"status": "unchanged", "country": current_by_name.name}

    result = {
        "status": "dry_run" if dry_run else "corrected",
        "candidate_id": int(candidate_id),
        "previous_country": current_name,
        "corrected_country": resolution.country.name,
        "evidence": resolution.evidence,
    }
    if dry_run:
        return result

    # Include all populated address values so the nested address write cannot
    # clear city/state/zip while replacing countryID.
    updated_address = {
        field: address[field]
        for field in ADDRESS_WRITE_FIELDS
        if address.get(field) not in (None, "")
    }
    updated_address["countryID"] = target_country_id

    try:
        updated_id = bullhorn.update_candidate(
            int(candidate_id),
            {"address": updated_address},
        )
        if not updated_id:
            raise RuntimeError("Bullhorn update_candidate returned no candidate ID")

        # Read-after-write verifies the field Bullhorn search actually uses.
        verified = bullhorn.get_candidate(int(candidate_id)) or {}
        verified_address = verified.get("address") or {}
        verified_id = verified_address.get("countryID")
        if verified_id != target_country_id:
            raise RuntimeError(
                "Bullhorn country verification failed "
                f"(read back {verified_address.get('countryName')!r}/"
                f"{verified_id!r}; expected ID "
                f"{target_country_id})"
            )

        audit_saved = _audit(
            candidate,
            resolution,
            previous_country=current_name,
            previous_country_id=current_id,
            trigger=trigger,
            status="corrected",
            environment_id=environment_id,
            corrected_country_id=target_country_id,
        )
        if not audit_saved:
            logger.critical(
                "event=candidate_country_corrected_without_audit "
                "candidate_id=%s environment_id=%s",
                candidate_id,
                environment_id,
            )
            return {
                **result,
                "status": "corrected_unlogged",
                "error": "Bullhorn updated but audit persistence failed",
            }
        logger.warning(
            "event=candidate_country_corrected candidate_id=%s state=%r "
            "previous_country=%r corrected_country=%r evidence=%r",
            candidate_id,
            address.get("state"),
            current_name,
            resolution.country.name,
            resolution.evidence[:300],
        )
        return result
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        _audit(
            candidate,
            resolution,
            previous_country=current_name,
            previous_country_id=current_id,
            trigger=trigger,
            status="failed",
            environment_id=environment_id,
            corrected_country_id=target_country_id,
            error_message=message,
        )
        logger.error(
            "event=candidate_country_correction_failed candidate_id=%s error=%s",
            candidate_id,
            message,
        )
        return {
            **result,
            "status": "failed",
            "error": message,
        }


def normalize_candidates(
    candidates: Iterable[Dict],
    bullhorn,
    *,
    dry_run: bool = False,
    trigger: str = "scheduled",
    environment_id: Optional[int] = None,
    country_ids: Optional[Dict[str, int]] = None,
) -> Dict:
    """Normalize a supplied candidate batch and return bounded cycle metrics."""
    stats = {
        "scanned": 0,
        "corrected": 0,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
        "corrected_unlogged": 0,
        "dry_run": 0,
    }
    changes = []
    for candidate in candidates:
        stats["scanned"] += 1
        result = normalize_candidate_country(
            candidate,
            bullhorn,
            dry_run=dry_run,
            trigger=trigger,
            environment_id=environment_id,
            country_ids=country_ids,
        )
        status = result.get("status", "failed")
        if status not in stats:
            status = "failed"
        stats[status] += 1
        if result.get("candidate_id"):
            changes.append(result)
    return {**stats, "changes": changes}


def run_candidate_country_normalization(
    *,
    lookback_hours: float = 48.0,
    limit: int = 1000,
    dry_run: bool = False,
    candidate_ids: Optional[Iterable[int]] = None,
    trigger: str = "scheduled",
    environment=None,
    since_ms: Optional[int] = None,
) -> Dict:
    """Authenticate, collect a bounded candidate batch, and normalize it."""
    from utils.bullhorn_helpers import get_bullhorn_service

    bullhorn = get_bullhorn_service(environment)
    if not bullhorn:
        raise RuntimeError("Bullhorn service is unavailable for environment")
    if not bullhorn.authenticate():
        raise RuntimeError("Bullhorn authentication failed")
    if not dry_run:
        _require_audit_table()

    if candidate_ids:
        candidates = []
        for candidate_id in candidate_ids:
            candidate = bullhorn.get_candidate(int(candidate_id))
            if candidate:
                candidates.append(candidate)
    else:
        candidates = _recent_candidates(
            bullhorn,
            lookback_hours=lookback_hours,
            limit=limit,
            since_ms=since_ms,
        )

    result = normalize_candidates(
        candidates,
        bullhorn,
        dry_run=dry_run,
        trigger=trigger,
        environment_id=(environment.id if environment else None),
        country_ids=_country_ids_for_environment(bullhorn),
    )
    floor_ms = _lookback_floor_ms(lookback_hours)
    result["lookback_floor_ms"] = floor_ms
    result["max_date_added_ms"] = max(
        (
            _candidate_date_added_ms(candidate)
            for candidate in candidates
            if _candidate_date_added_ms(candidate) >= floor_ms
        ),
        default=0,
    )
    logger.info(
        "candidate_country_normalizer: collected=%s floor_ms=%s "
        "max_date_added_ms=%s since_ms=%s",
        len(candidates),
        floor_ms,
        result["max_date_added_ms"],
        since_ms,
    )
    return result


def run_candidate_country_backfill(
    *,
    phase: str = "canada",
    limit: int = 200,
    dry_run: bool = False,
    trigger: str = "backfill",
    environment=None,
    after_id: int = 0,
    before_ms: Optional[int] = None,
    after_ms: int = 0,
    live_lookback_hours: float = 48.0,
) -> Dict:
    """Run one bounded phased backfill page using the same write gates."""
    from utils.bullhorn_helpers import get_bullhorn_service

    normalized_phase = (phase or "").strip().lower()
    if normalized_phase not in BACKFILL_PHASES:
        raise ValueError(f"Unsupported backfill phase: {phase!r}")
    if normalized_phase == "done":
        return {
            "scanned": 0,
            "corrected": 0,
            "unchanged": 0,
            "skipped": 0,
            "failed": 0,
            "corrected_unlogged": 0,
            "dry_run": 0,
            "changes": [],
            "phase": "done",
            "phase_complete": True,
            "max_candidate_id": 0,
            "min_date_added_ms": 0,
            "lookback_floor_ms": _lookback_floor_ms(live_lookback_hours),
        }

    bullhorn = get_bullhorn_service(environment)
    if not bullhorn:
        raise RuntimeError("Bullhorn service is unavailable for environment")
    if not bullhorn.authenticate():
        raise RuntimeError("Bullhorn authentication failed")
    if not dry_run:
        _require_audit_table()

    floor_ms = _lookback_floor_ms(live_lookback_hours)
    if before_ms is None:
        before_ms = max(0, floor_ms - 1)

    candidates = _backfill_candidates(
        bullhorn,
        phase=normalized_phase,
        limit=limit,
        after_id=after_id,
        before_ms=before_ms,
        after_ms=after_ms,
    )
    result = normalize_candidates(
        candidates,
        bullhorn,
        dry_run=dry_run,
        trigger=f"{trigger}:{normalized_phase}",
        environment_id=(environment.id if environment else None),
        country_ids=_country_ids_for_environment(bullhorn),
    )
    result["phase"] = normalized_phase
    result["lookback_floor_ms"] = floor_ms
    result["max_candidate_id"] = max(
        (_candidate_id(candidate) for candidate in candidates),
        default=0,
    )
    result["min_date_added_ms"] = min(
        (
            _candidate_date_added_ms(candidate)
            for candidate in candidates
            if _candidate_date_added_ms(candidate) > 0
        ),
        default=0,
    )
    # Empty page or short final page means this phase has no more work.
    result["phase_complete"] = len(candidates) < max(1, int(limit))
    logger.info(
        "candidate_country_normalizer: backfill phase=%s collected=%s "
        "max_candidate_id=%s min_date_added_ms=%s phase_complete=%s",
        normalized_phase,
        len(candidates),
        result["max_candidate_id"],
        result["min_date_added_ms"],
        result["phase_complete"],
    )
    return result
