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

    def _get_cooldown_candidate_ids(self, max_candidates=50000):
        try:
            from models import OwnerReassignmentCooldown
            rows = (
                OwnerReassignmentCooldown.query
                .order_by(OwnerReassignmentCooldown.last_evaluated_at.desc())
                .limit(max_candidates)
                .all()
            )
            return [r.candidate_id for r in rows]
        except Exception as exc:
            logger.warning(f"cleanup_duplicate_notes: could not load cooldown candidates — {exc}")
            return []

    def _get_vetting_candidate_ids(self, max_candidates=50000):
        try:
            from models import CandidateVettingLog
            rows = (
                CandidateVettingLog.query
                .filter(CandidateVettingLog.is_sandbox.is_(False))
                .with_entities(CandidateVettingLog.bullhorn_candidate_id)
                .distinct()
                .order_by(CandidateVettingLog.bullhorn_candidate_id.desc())
                .limit(max_candidates)
                .all()
            )
            return [r.bullhorn_candidate_id for r in rows if r.bullhorn_candidate_id]
        except Exception as exc:
            logger.warning(f"cleanup_duplicate_notes: could not load vetting candidates — {exc}")
            return []

    def _get_all_bullhorn_candidates(self, max_candidates=50000):
        all_ids = []
        start = 0
        page_size = 500
        while len(all_ids) < max_candidates:
            try:
                url = f"{self._bh_url()}search/Candidate"
                params = {
                    "query": "id:[1 TO *]",
                    "fields": "id",
                    "count": min(page_size, max_candidates - len(all_ids)),
                    "start": start,
                    "sort": "-dateLastModified"
                }
                resp = requests.get(url, headers=self._bh_headers(), params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("data", [])
                total = data.get("total", 0)
                all_ids.extend(c.get("id") for c in batch if c.get("id"))
                start += len(batch)
                if not batch or start >= total:
                    break
                time.sleep(0.05)
            except Exception as exc:
                logger.warning(f"cleanup_duplicate_notes: Bullhorn candidate pagination error at start={start} — {exc}")
                break
        logger.info(f"cleanup_duplicate_notes: loaded {len(all_ids)} candidates from Bullhorn (paginated)")
        return all_ids[:max_candidates]

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

    def _find_duplicates_in_notes(self, cand_notes, time_window_minutes):
        cand_notes.sort(key=lambda n: n.get("dateAdded", 0))
        groups = {}
        for note in cand_notes:
            author_id = (note.get("commentingPerson") or {}).get("id", 0)
            comments = (note.get("comments") or "").strip()
            if not comments:
                continue
            key = (author_id, comments)
            groups.setdefault(key, []).append(note)

        to_delete = []
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
                    to_delete.append({
                        "id": note["id"],
                        "note_id": note["id"],
                        "action": note.get("action", ""),
                        "gap_minutes": round(gap_min, 1),
                        "gap_min": round(gap_min, 1),
                        "author": author_name or f"user:{author_id}",
                        "comments": (comments[:80] + ("..." if len(comments) > 80 else "")),
                    })
        return to_delete

    def _builtin_cleanup_duplicate_notes(self, params):
        dry_run = params.get("dry_run", True)
        if isinstance(dry_run, str):
            dry_run = dry_run.lower() not in ('false', '0', '')
        note_age_days = int(params.get("days_back", 30))
        max_candidates = int(params.get("max_candidates", 50000))
        time_window_minutes = int(params.get("time_window_minutes", 60))
        action_filter = (params.get("action_filter") or "").strip()

        cutoff = datetime.utcnow() - timedelta(days=note_age_days)
        cutoff_ts = int(cutoff.timestamp() * 1000)

        AI_VETTING_ACTIONS = {"AI Vetter - Accept", "AI Vetter - Reject"}
        if action_filter == "Owner Reassignment":
            candidate_ids = self._get_cooldown_candidate_ids(max_candidates)
            candidate_source = "cooldown_table (all records)"
        elif action_filter in AI_VETTING_ACTIONS:
            candidate_ids = self._get_vetting_candidate_ids(max_candidates)
            candidate_source = "vetting_log (all records)"
        else:
            candidate_ids = self._get_all_bullhorn_candidates(max_candidates)
            candidate_source = "bullhorn_paginated (all candidates)"

        logger.info(
            f"cleanup_duplicate_notes: source={candidate_source}, "
            f"{len(candidate_ids)} candidates, "
            f"action_filter={action_filter!r}, "
            f"note_age={note_age_days}d, window={time_window_minutes}min, "
            f"max_candidates={max_candidates}, dry_run={dry_run}"
        )

        total_notes_fetched = 0
        candidates_with_notes = 0
        skipped_action_filter = 0
        fetch_errors = 0
        total_candidates = len(candidate_ids)
        scan_start = time.time()

        duplicates = {}
        total_delete = 0
        deleted = 0
        failed = 0
        preview_rows = []

        for idx, cid in enumerate(candidate_ids):
            cand_notes = []
            try:
                notes = self._get_candidate_entity_notes(cid, count=500)
                for note in notes:
                    date_added = note.get("dateAdded", 0)
                    if date_added < cutoff_ts:
                        continue
                    note_action = (note.get("action") or "").strip()
                    if action_filter and note_action != action_filter:
                        skipped_action_filter += 1
                        continue
                    total_notes_fetched += 1
                    cand_notes.append(note)
            except Exception as exc:
                fetch_errors += 1
                if fetch_errors <= 5:
                    logger.warning(f"cleanup_duplicate_notes: entity fetch failed for candidate {cid} — {exc}")

            if len(cand_notes) >= 2:
                candidates_with_notes += 1
                to_delete = self._find_duplicates_in_notes(cand_notes, time_window_minutes)
                if to_delete:
                    duplicates[cid] = to_delete
                    total_delete += len(to_delete)
                    if len(preview_rows) < 25:
                        for d in to_delete[:3]:
                            preview_rows.append({"candidate_id": cid, **d})
                    if not dry_run:
                        for note in to_delete:
                            try:
                                self._soft_delete_note(note["id"])
                                deleted += 1
                            except Exception:
                                failed += 1
                            time.sleep(0.02)

            progress_n = idx + 1
            if progress_n % 500 == 0 or progress_n == 1 or progress_n == total_candidates:
                elapsed = time.time() - scan_start
                rate = progress_n / elapsed if elapsed > 0 else 0
                remaining = (total_candidates - progress_n) / rate if rate > 0 else 0
                logger.info(
                    f"cleanup_duplicate_notes: progress {progress_n}/{total_candidates} "
                    f"({progress_n * 100 // total_candidates}%), "
                    f"dupes_found={total_delete}, errors={fetch_errors}, "
                    f"elapsed={elapsed:.0f}s, ETA={remaining:.0f}s"
                )
            time.sleep(0.01)

        total_elapsed = time.time() - scan_start
        logger.info(
            f"cleanup_duplicate_notes: complete — scanned {total_candidates} candidates in {total_elapsed:.0f}s, "
            f"found {total_notes_fetched} matching notes across {candidates_with_notes} candidates "
            f"(action filter skipped {skipped_action_filter}, fetch errors {fetch_errors})"
        )

        candidates_scanned = len(candidate_ids)
        mode_label = "DRY RUN: " if dry_run else ""
        summary = (
            f"{mode_label}Found {total_delete} duplicate note(s) across "
            f"{len(duplicates)} candidate(s) "
            f"[candidates_checked={candidates_scanned}, with_matching_notes={candidates_with_notes}, "
            f"matching_notes={total_notes_fetched}, action_skipped={skipped_action_filter}, "
            f"fetch_errors={fetch_errors}, elapsed={total_elapsed:.0f}s]"
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
            "elapsed_seconds": round(total_elapsed),
            "preview": preview_rows,
            "candidate_breakdown": {
                str(k): len(v) for k, v in list(duplicates.items())[:30]
            },
        }

