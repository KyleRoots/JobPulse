"""Note-cleanup builtins: AI-note bulk delete and duplicate-note dedup.

Part of the `automation_service` package — the monolithic
`automation_service.py` (1,839 lines) was split into focused mixins so
each cluster of related builtins lives next to its helpers.
"""
import json
import logging
import time
import threading
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from extensions import db
from models import AutomationTask, AutomationLog, AutomationChat

logger = logging.getLogger(__name__)


class NotesMixin:
    AI_ACTION_PATTERNS = ["AI Vetting", "AI Resume Summary", "AI Vetted"]

    def _builtin_cleanup_ai_notes(self, params):
        dry_run = params.get("dry_run", True)
        candidate_ids = params.get("candidate_ids")
        max_candidates = params.get("max_candidates", 500)

        note_url = f"{self._bh_url()}search/Note"
        ai_notes = []
        scanned_ids = set()

        def _fetch_notes_batch(query_str):
            start = 0
            while True:
                p = {
                    "query": query_str,
                    "fields": "id,action,comments,dateAdded,personReference(id)",
                    "count": 500,
                    "start": start,
                    "sort": "-dateAdded",
                }
                resp = requests.get(note_url, headers=self._bh_headers(), params=p, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("data", [])
                total = data.get("total", 0)
                for note in batch:
                    action = note.get("action") or ""
                    if any(pat in action for pat in self.AI_ACTION_PATTERNS):
                        cid = (note.get("personReference") or {}).get("id")
                        if cid:
                            scanned_ids.add(cid)
                            ai_notes.append({
                                "note_id": note["id"],
                                "candidate_id": cid,
                                "action": action,
                                "comments_preview": (note.get("comments") or "")[:150],
                            })
                start += len(batch)
                if len(batch) < 500 or start >= total:
                    break
                time.sleep(0.05)

        if candidate_ids:
            chunk_size = 100
            for i in range(0, len(candidate_ids), chunk_size):
                chunk = candidate_ids[i:i + chunk_size]
                id_clause = " OR ".join(str(c) for c in chunk)
                try:
                    _fetch_notes_batch(f"personReference.id:({id_clause}) AND isDeleted:false")
                except Exception:
                    pass
        else:
            action_clause = " OR ".join(
                f'"{pat} - Not Recommended" OR "{pat} - Qualified" OR "{pat} - Recommended"'
                if pat == "AI Vetting" else f'"{pat}"'
                for pat in self.AI_ACTION_PATTERNS
            )
            try:
                _fetch_notes_batch(f"action:({action_clause}) AND isDeleted:false")
            except Exception:
                pass

        candidate_ids = list(scanned_ids)

        deleted = 0
        failed = 0
        if not dry_run:
            for note_info in ai_notes:
                try:
                    self._soft_delete_note(note_info["note_id"])
                    deleted += 1
                except Exception:
                    failed += 1

        return {
            "summary": f"{'DRY RUN: ' if dry_run else ''}Found {len(ai_notes)} AI notes across {len(candidate_ids)} candidates scanned"
                       + (f". Deleted {deleted}, failed {failed}" if not dry_run else ""),
            "dry_run": dry_run,
            "candidates_scanned": len(candidate_ids),
            "ai_notes_found": len(ai_notes),
            "deleted": deleted,
            "failed": failed,
            "notes": ai_notes[:50]
        }

    def _builtin_cleanup_duplicate_notes(self, params):
        dry_run = params.get("dry_run", True)
        days_back = params.get("days_back", 5)
        max_candidates = params.get("max_candidates", 500)
        chain_window = 60

        cutoff = datetime.utcnow() - timedelta(days=days_back)
        cutoff_ts = int(cutoff.timestamp() * 1000)

        notes_by_candidate = {}
        note_url = f"{self._bh_url()}search/Note"
        start = 0
        while True:
            p = {
                "query": f'action:"AI Vetting - Not Recommended" AND dateAdded:[{cutoff_ts} TO *] AND isDeleted:false',
                "fields": "id,action,dateAdded,personReference(id)",
                "count": 500,
                "start": start,
                "sort": "dateAdded",
            }
            try:
                resp = requests.get(note_url, headers=self._bh_headers(), params=p, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("data", [])
                total = data.get("total", 0)
                for note in batch:
                    cid = (note.get("personReference") or {}).get("id")
                    if cid:
                        notes_by_candidate.setdefault(cid, []).append(note)
                start += len(batch)
                if len(batch) < 500 or start >= total:
                    break
            except Exception:
                break
            time.sleep(0.05)

        duplicates = {}
        total_delete = 0

        for cid, target_notes in notes_by_candidate.items():
            if len(target_notes) < 2:
                continue

            target_notes.sort(key=lambda n: n.get("dateAdded", 0))
            keep = [target_notes[0]]
            to_delete = []
            prev_time = target_notes[0].get("dateAdded", 0)

            for note in target_notes[1:]:
                gap_ms = note.get("dateAdded", 0) - prev_time
                gap_min = gap_ms / 60000
                if gap_min <= chain_window:
                    to_delete.append({
                        "id": note["id"],
                        "gap_minutes": round(gap_min, 1)
                    })
                else:
                    keep.append(note)
                prev_time = note.get("dateAdded", 0)

            if to_delete:
                duplicates[cid] = {"keep": len(keep), "delete": to_delete}
                total_delete += len(to_delete)

        deleted = 0
        failed = 0
        if not dry_run:
            for cid, data in duplicates.items():
                for note in data["delete"]:
                    try:
                        self._soft_delete_note(note["id"])
                        deleted += 1
                    except Exception:
                        failed += 1

        candidates_scanned = len(notes_by_candidate)
        return {
            "summary": f"{'DRY RUN: ' if dry_run else ''}Found {total_delete} duplicate notes across {len(duplicates)} candidates"
                       + (f". Deleted {deleted}, failed {failed}" if not dry_run else ""),
            "dry_run": dry_run,
            "candidates_scanned": candidates_scanned,
            "candidates_with_duplicates": len(duplicates),
            "duplicate_notes_found": total_delete,
            "deleted": deleted,
            "failed": failed,
            "details": {str(k): {"keep": v["keep"], "delete_count": len(v["delete"])} for k, v in list(duplicates.items())[:20]}
        }

