"""Background cleanup of duplicate Bullhorn Email notes (Outlook sync doubles).

Recruiters' emails often land twice on a candidate (same author, same body,
same minute). Automation Hub already has an on-demand ``cleanup_duplicate_notes``
builtin; this module runs a scoped, scheduled pass so Email twins do not pile up.

Scope (intentionally narrow):
  - Action filter: ``Email`` only (never Scout / Owner Reassignment / calls)
  - Same commentingPerson + identical comments within ``time_window_minutes``
  - Soft-delete older copies; keep the newest
  - Candidate set: recently modified Bullhorn candidates (bounded)

Kill switches:
  - ``EMAIL_NOTE_DEDUP_ENABLED`` (default true)
  - ``EMAIL_NOTE_DEDUP_DRY_RUN`` (default false; when true, count only)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

DEFAULT_ACTION = "Email"
DEFAULT_WINDOW_MINUTES = 60
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_MAX_CANDIDATES = 400
NOTE_FIELDS = "id,action,comments,dateAdded,commentingPerson(id,firstName,lastName)"


def env_flag(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def find_duplicate_notes(
    notes: List[Dict[str, Any]],
    *,
    action_filter: str = DEFAULT_ACTION,
    time_window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> List[Dict[str, Any]]:
    """Return older twin notes to soft-delete (keeps newest in each group)."""
    filtered = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        action = (note.get("action") or "").strip()
        if action_filter and action != action_filter:
            continue
        comments = (note.get("comments") or "").strip()
        if not comments:
            continue
        author = note.get("commentingPerson") or {}
        author_id = author.get("id") if isinstance(author, dict) else None
        if not author_id:
            continue
        filtered.append(note)

    groups: Dict[Tuple[Any, str], List[Dict[str, Any]]] = {}
    for note in filtered:
        author_id = (note.get("commentingPerson") or {}).get("id")
        comments = (note.get("comments") or "").strip()
        groups.setdefault((author_id, comments), []).append(note)

    to_delete: List[Dict[str, Any]] = []
    for (_author_id, comments), group in groups.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda n: int(n.get("dateAdded") or 0))
        newest = group[-1]
        newest_ts = int(newest.get("dateAdded") or 0)
        for note in group[:-1]:
            gap_min = (newest_ts - int(note.get("dateAdded") or 0)) / 60000.0
            if gap_min <= time_window_minutes:
                author = note.get("commentingPerson") or {}
                to_delete.append({
                    "id": note.get("id"),
                    "author": f"{author.get('firstName', '')} {author.get('lastName', '')}".strip(),
                    "action": (note.get("action") or "").strip(),
                    "gap_minutes": round(gap_min, 1),
                    "comments_preview": (comments[:80] + ("..." if len(comments) > 80 else "")),
                })
    return to_delete


class EmailNoteDedupService:
    """Scheduled pass over recently touched candidates."""

    def __init__(
        self,
        *,
        bullhorn=None,
        enabled: Optional[bool] = None,
        dry_run: Optional[bool] = None,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        time_window_minutes: int = DEFAULT_WINDOW_MINUTES,
        action_filter: str = DEFAULT_ACTION,
    ):
        self.bullhorn = bullhorn
        self.enabled = env_flag("EMAIL_NOTE_DEDUP_ENABLED", True) if enabled is None else enabled
        self.dry_run = env_flag("EMAIL_NOTE_DEDUP_DRY_RUN", False) if dry_run is None else dry_run
        self.lookback_days = lookback_days
        self.max_candidates = max_candidates
        self.time_window_minutes = time_window_minutes
        self.action_filter = action_filter

    def _ensure_bh(self):
        if self.bullhorn is not None:
            return self.bullhorn
        from utils.bullhorn_helpers import get_bullhorn_service
        bh = get_bullhorn_service()
        if not bh or not bh.authenticate():
            raise RuntimeError("Bullhorn auth failed for email note dedup")
        self.bullhorn = bh
        return bh

    def _headers(self) -> Dict[str, str]:
        bh = self._ensure_bh()
        return {"BhRestToken": bh.rest_token}

    def _base(self) -> str:
        bh = self._ensure_bh()
        return bh.base_url.rstrip("/") + "/"

    def _recent_candidate_ids(self) -> List[int]:
        cutoff = datetime.utcnow() - timedelta(days=self.lookback_days)
        cutoff_ms = int(cutoff.timestamp() * 1000)
        url = f"{self._base()}search/Candidate"
        ids: List[int] = []
        start = 0
        page = 100
        while len(ids) < self.max_candidates:
            params = {
                "query": f"dateLastModified:[{cutoff_ms} TO *] AND isDeleted:false",
                "fields": "id",
                "count": min(page, self.max_candidates - len(ids)),
                "start": start,
                "sort": "-dateLastModified",
            }
            resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
            if resp.status_code != 200:
                logger.warning(
                    "email_note_dedup: candidate search HTTP %s %s",
                    resp.status_code,
                    (resp.text or "")[:200],
                )
                break
            batch = (resp.json() or {}).get("data") or []
            for row in batch:
                cid = row.get("id") if isinstance(row, dict) else None
                if cid:
                    ids.append(int(cid))
            start += len(batch)
            if len(batch) < page:
                break
            time.sleep(0.05)
        return ids[: self.max_candidates]

    def _candidate_notes(self, candidate_id: int) -> List[Dict[str, Any]]:
        url = f"{self._base()}entity/Candidate/{candidate_id}/notes"
        params = {"fields": NOTE_FIELDS, "count": 200}
        resp = requests.get(url, headers=self._headers(), params=params, timeout=25)
        if resp.status_code != 200:
            logger.warning(
                "email_note_dedup: notes fetch candidate=%s HTTP %s",
                candidate_id,
                resp.status_code,
            )
            return []
        body = resp.json() or {}
        if isinstance(body, dict):
            return body.get("data") or []
        if isinstance(body, list):
            return body
        return []

    def _soft_delete(self, note_id: int) -> bool:
        url = f"{self._base()}entity/Note/{note_id}"
        resp = requests.post(
            url,
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"isDeleted": True},
            timeout=15,
        )
        if resp.status_code >= 400:
            logger.warning(
                "email_note_dedup: soft-delete note=%s HTTP %s %s",
                note_id,
                resp.status_code,
                (resp.text or "")[:200],
            )
            return False
        return True

    def run(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "enabled": self.enabled,
            "dry_run": self.dry_run,
            "candidates_checked": 0,
            "candidates_with_duplicates": 0,
            "duplicates_found": 0,
            "deleted": 0,
            "failed": 0,
            "errors": [],
            "preview": [],
        }
        if not self.enabled:
            result["message"] = "disabled (EMAIL_NOTE_DEDUP_ENABLED=false)"
            logger.info("email_note_dedup: disabled — skipping")
            return result

        try:
            candidate_ids = self._recent_candidate_ids()
        except Exception as exc:
            result["errors"].append(f"candidate_search: {exc}")
            logger.error("email_note_dedup: candidate search failed: %s", exc)
            return result

        result["candidates_checked"] = len(candidate_ids)
        for cid in candidate_ids:
            try:
                notes = self._candidate_notes(cid)
                twins = find_duplicate_notes(
                    notes,
                    action_filter=self.action_filter,
                    time_window_minutes=self.time_window_minutes,
                )
                if not twins:
                    continue
                result["candidates_with_duplicates"] += 1
                result["duplicates_found"] += len(twins)
                if len(result["preview"]) < 25:
                    for twin in twins[:3]:
                        result["preview"].append({"candidate_id": cid, **twin})
                if self.dry_run:
                    continue
                for twin in twins:
                    nid = twin.get("id")
                    if not nid:
                        continue
                    if self._soft_delete(int(nid)):
                        result["deleted"] += 1
                    else:
                        result["failed"] += 1
                    time.sleep(0.05)
            except Exception as exc:
                result["errors"].append(f"candidate {cid}: {exc}")
                logger.warning("email_note_dedup: candidate %s failed: %s", cid, exc)

        logger.info(
            "email_note_dedup: checked=%s with_dups=%s found=%s deleted=%s failed=%s dry_run=%s",
            result["candidates_checked"],
            result["candidates_with_duplicates"],
            result["duplicates_found"],
            result["deleted"],
            result["failed"],
            self.dry_run,
        )
        return result


def run_email_note_dedup() -> Dict[str, Any]:
    """Scheduler entrypoint."""
    return EmailNoteDedupService().run()
