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

    def _get_api_user_ids(self):
        try:
            from models import VettingConfig
            row = VettingConfig.query.filter_by(setting_key='api_user_ids').first()
            if row and row.setting_value:
                return [int(x.strip()) for x in row.setting_value.split(',') if x.strip().isdigit()]
        except Exception as exc:
            logger.warning(f"cleanup_duplicate_notes: could not load api_user_ids — {exc}")
        return []

    def _get_cooldown_candidate_ids(self, cutoff, limit=500):
        try:
            from models import OwnerReassignmentCooldown
            rows = (
                OwnerReassignmentCooldown.query
                .filter(OwnerReassignmentCooldown.last_evaluated_at >= cutoff)
                .order_by(OwnerReassignmentCooldown.last_evaluated_at.desc())
                .limit(limit)
                .all()
            )
            return [r.candidate_id for r in rows]
        except Exception as exc:
            logger.warning(f"cleanup_duplicate_notes: could not load cooldown candidates — {exc}")
            return []

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
        if isinstance(dry_run, str):
            dry_run = dry_run.lower() not in ('false', '0', '')
        days_back = int(params.get("days_back", 5))
        max_candidates = int(params.get("max_candidates", 500))
        time_window_minutes = int(params.get("time_window_minutes", 60))
        action_filter = (params.get("action_filter") or "").strip()

        cutoff = datetime.utcnow() - timedelta(days=days_back)
        cutoff_ts = int(cutoff.timestamp() * 1000)

        if action_filter == "Owner Reassignment":
            candidate_ids = self._get_cooldown_candidate_ids(cutoff, max_candidates)
            candidate_source = "cooldown_table"
        else:
            candidate_ids = self._get_recent_candidates(max_count=max_candidates)
            candidate_source = "recent_candidates"

        logger.info(
            f"cleanup_duplicate_notes: source={candidate_source}, "
            f"{len(candidate_ids)} candidates, "
            f"action_filter={action_filter!r}, "
            f"days_back={days_back}, window={time_window_minutes}min, "
            f"max_candidates={max_candidates}, dry_run={dry_run}"
        )

        notes_by_candidate = {}
        total_notes_fetched = 0
        skipped_action_filter = 0
        fetch_errors = 0
        total_candidates = len(candidate_ids)
        for idx, cid in enumerate(candidate_ids):
            try:
                notes = self._get_candidate_entity_notes(cid, count=200)
                for note in notes:
                    date_added = note.get("dateAdded", 0)
                    if date_added < cutoff_ts:
                        continue
                    note_action = (note.get("action") or "").strip()
                    if action_filter and note_action != action_filter:
                        skipped_action_filter += 1
                        continue
                    total_notes_fetched += 1
                    notes_by_candidate.setdefault(cid, []).append(note)
            except Exception as exc:
                fetch_errors += 1
                if fetch_errors <= 3:
                    logger.warning(f"cleanup_duplicate_notes: entity fetch failed for candidate {cid} — {exc}")
            if (idx + 1) % 100 == 0 or idx == 0:
                logger.info(
                    f"cleanup_duplicate_notes: progress {idx + 1}/{total_candidates}, "
                    f"matching_notes={total_notes_fetched}, errors={fetch_errors}"
                )
            time.sleep(0.01)

        logger.info(
            f"cleanup_duplicate_notes: scanned {len(candidate_ids)} candidates via entity lookup, "
            f"found {total_notes_fetched} matching notes across {len(notes_by_candidate)} candidates "
            f"(action filter skipped {skipped_action_filter}, fetch errors {fetch_errors})"
        )

        if len(notes_by_candidate) > max_candidates:
            trimmed = dict(list(notes_by_candidate.items())[:max_candidates])
            notes_by_candidate = trimmed

        duplicates = {}
        total_delete = 0
        preview_rows = []

        for cid, cand_notes in notes_by_candidate.items():
            if len(cand_notes) < 2:
                continue

            cand_notes.sort(key=lambda n: n.get("dateAdded", 0))

            groups = {}
            for note in cand_notes:
                author_id = (note.get("commentingPerson") or {}).get("id", 0)
                comments = (note.get("comments") or "").strip()
                if not comments:
                    continue
                key = (author_id, comments)
                groups.setdefault(key, []).append(note)

            to_delete_for_cid = []
            for (author_id, comments), group in groups.items():
                if len(group) < 2:
                    continue

                group.sort(key=lambda n: n.get("dateAdded", 0))
                newest = group[-1]

                for note in group[:-1]:
                    gap_ms = newest.get("dateAdded", 0) - note.get("dateAdded", 0)
                    gap_min = gap_ms / 60000
                    if gap_min <= time_window_minutes:
                        author_name = ""
                        cp = note.get("commentingPerson") or {}
                        if cp.get("firstName"):
                            author_name = f"{cp.get('firstName', '')} {cp.get('lastName', '')}".strip()
                        to_delete_for_cid.append({
                            "id": note["id"],
                            "action": note.get("action", ""),
                            "gap_minutes": round(gap_min, 1),
                            "author": author_name or f"user:{author_id}",
                            "comments_preview": comments[:80] + ("..." if len(comments) > 80 else ""),
                        })

            if to_delete_for_cid:
                duplicates[cid] = to_delete_for_cid
                total_delete += len(to_delete_for_cid)
                if len(preview_rows) < 25:
                    for d in to_delete_for_cid[:3]:
                        preview_rows.append({
                            "candidate_id": cid,
                            "note_id": d["id"],
                            "action": d["action"],
                            "author": d["author"],
                            "gap_min": d["gap_minutes"],
                            "comments": d["comments_preview"],
                        })

        deleted = 0
        failed = 0
        if not dry_run:
            for cid, notes_list in duplicates.items():
                for note in notes_list:
                    try:
                        self._soft_delete_note(note["id"])
                        deleted += 1
                    except Exception:
                        failed += 1
                    time.sleep(0.02)

        candidates_scanned = len(candidate_ids)
        candidates_with_notes = len(notes_by_candidate)
        mode_label = "DRY RUN: " if dry_run else ""
        summary = (
            f"{mode_label}Found {total_delete} duplicate note(s) across "
            f"{len(duplicates)} candidate(s) "
            f"[candidates_checked={candidates_scanned}, with_matching_notes={candidates_with_notes}, "
            f"matching_notes={total_notes_fetched}, action_skipped={skipped_action_filter}, "
            f"fetch_errors={fetch_errors}]"
        )
        if not dry_run:
            summary += f" Deleted {deleted}, failed {failed}."

        return {
            "summary": summary,
            "dry_run": dry_run,
            "lookup_method": "entity_association",
            "candidate_source": candidate_source,
            "candidates_checked": candidates_scanned,
            "candidates_with_matching_notes": candidates_with_notes,
            "matching_notes": total_notes_fetched,
            "skipped_action_filter": skipped_action_filter,
            "fetch_errors": fetch_errors,
            "candidates_with_duplicates": len(duplicates),
            "duplicate_notes_found": total_delete,
            "deleted": deleted,
            "failed": failed,
            "time_window_minutes": time_window_minutes,
            "action_filter": action_filter or "(all actions)",
            "preview": preview_rows,
            "candidate_breakdown": {
                str(k): len(v) for k, v in list(duplicates.items())[:30]
            },
        }

