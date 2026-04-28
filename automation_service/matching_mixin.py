"""Match-related builtins: zero-match scan, qualified export, incomplete rescreen, salesrep sync, bulk field update, and screening audit.

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


class MatchingMixin:
    def _builtin_find_zero_match(self, params):
        dry_run = params.get("dry_run", True)
        hours_back = params.get("hours_back", 6)
        do_delete = params.get("delete", False) and not dry_run

        cutoff_ts = int((datetime.utcnow() - timedelta(hours=hours_back)).timestamp() * 1000)
        url = f"{self._bh_url()}search/Note"
        all_notes = []
        start = 0

        while True:
            p = {
                "query": f"dateAdded:[{cutoff_ts} TO *]",
                "fields": "id,action,comments,dateAdded,personReference(id,firstName,lastName,email)",
                "count": 500,
                "start": start,
                "sort": "-dateAdded"
            }
            resp = requests.get(url, headers=self._bh_headers(), params=p, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("data", [])
            total = data.get("total", 0)

            matching = [n for n in batch if (n.get("action") or "") == "AI Vetting - Not Recommended"]
            all_notes.extend(matching)
            start += len(batch)
            if len(batch) < 500 or start >= total:
                break

        zero_match = []
        for note in all_notes:
            comments = note.get("comments") or ""
            if "Highest Match Score: 0%" in comments:
                person = note.get("personReference") or {}
                zero_match.append({
                    "note_id": note["id"],
                    "candidate_id": person.get("id"),
                    "candidate_name": f"{person.get('firstName', '')} {person.get('lastName', '')}".strip(),
                    "email": person.get("email", ""),
                    "comments_preview": comments[:150]
                })

        deleted = 0
        if do_delete:
            for item in zero_match:
                try:
                    self._soft_delete_note(item["note_id"])
                    deleted += 1
                except Exception:
                    pass

        return {
            "summary": f"{'DRY RUN: ' if dry_run else ''}Found {len(zero_match)} zero-match candidates in last {hours_back} hours"
                       + (f". Deleted {deleted} notes" if do_delete else ""),
            "dry_run": dry_run,
            "total_notes_scanned": len(all_notes),
            "zero_match_found": len(zero_match),
            "deleted": deleted,
            "candidates": zero_match[:50]
        }

    def _builtin_export_qualified(self, params):
        job_ids = params.get("job_ids", [])
        if not job_ids:
            return {"error": "job_ids parameter is required (list of job IDs)"}

        qualifying_actions = [
            "Scout Screen - Qualified",
            "AI Vetting - Qualified",
            "AI Vetting - Recommended",
            "AI Vetted - Accept",
        ]

        qualified = []
        all_actions_seen = set()

        for job_id in job_ids:
            subs = []
            start = 0
            while True:
                url = f"{self._bh_url()}search/JobSubmission"
                p = {
                    "query": f"jobOrder.id:{job_id} AND isDeleted:0",
                    "fields": "id,status,dateAdded,candidate(id,firstName,lastName,email,phone,occupation,source)",
                    "count": 500,
                    "start": start,
                    "sort": "-dateAdded"
                }
                resp = requests.get(url, headers=self._bh_headers(), params=p, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("data", [])
                subs.extend(batch)
                if len(batch) < 500 or start + 500 >= data.get("total", 0):
                    break
                start += 500
                time.sleep(0.1)

            cand_by_id = {
                sub.get("candidate", {}).get("id"): sub.get("candidate", {})
                for sub in subs
                if (sub.get("candidate") or {}).get("id")
            }
            if cand_by_id:
                note_url = f"{self._bh_url()}search/Note"
                action_clause = " OR ".join(f'"{a}"' for a in qualifying_actions)
                qualifying_notes = {}
                chunk_ids = list(cand_by_id.keys())
                for i in range(0, len(chunk_ids), 100):
                    chunk = chunk_ids[i:i + 100]
                    id_clause = " OR ".join(str(c) for c in chunk)
                    note_start = 0
                    while True:
                        np = {
                            "query": f"personReference.id:({id_clause}) AND action:({action_clause}) AND isDeleted:false",
                            "fields": "id,action,personReference(id)",
                            "count": 500,
                            "start": note_start,
                            "sort": "-dateAdded",
                        }
                        try:
                            nr = requests.get(note_url, headers=self._bh_headers(), params=np, timeout=30)
                            nr.raise_for_status()
                            nd = nr.json()
                            note_batch = nd.get("data", [])
                            for note in note_batch:
                                action = note.get("action") or ""
                                if action:
                                    all_actions_seen.add(action)
                                cid = (note.get("personReference") or {}).get("id")
                                if cid and cid not in qualifying_notes and action in qualifying_actions:
                                    qualifying_notes[cid] = action
                            note_start += len(note_batch)
                            if len(note_batch) < 500 or note_start >= nd.get("total", 0):
                                break
                        except Exception:
                            break
                        time.sleep(0.05)

                for cid, cand in cand_by_id.items():
                    if cid in qualifying_notes:
                        qualified.append({
                            "candidate_id": cid,
                            "first_name": cand.get("firstName", ""),
                            "last_name": cand.get("lastName", ""),
                            "email": cand.get("email", ""),
                            "phone": cand.get("phone", ""),
                            "source": cand.get("source", ""),
                            "occupation": cand.get("occupation", ""),
                            "job_id": job_id,
                            "note_action": qualifying_notes[cid]
                        })

        unique_ids = set(c["candidate_id"] for c in qualified)
        return {
            "summary": f"Found {len(qualified)} qualified candidates ({len(unique_ids)} unique) across {len(job_ids)} jobs",
            "total_rows": len(qualified),
            "unique_candidates": len(unique_ids),
            "by_job": {str(jid): len([c for c in qualified if c["job_id"] == jid]) for jid in job_ids},
            "all_actions_seen": sorted(all_actions_seen),
            "candidates": qualified
        }

    def _builtin_incomplete_rescreen(self, params):
        """
        Finds inbound tearsheet candidates (from ParsedEmail records, March 5 2026 onwards)
        whose Bullhorn 'description' field is empty but who have an attached resume file.
        For each such candidate:
          1. Downloads and parses the resume file → writes it back to Bullhorn description.
          2. Resets ParsedEmail.vetted_at = None so the next vetting cycle re-evaluates them.
        Candidate records that already have a description, or have no resume file, are skipped.
        """
        from datetime import timezone
        dry_run = params.get("dry_run", False)
        batch_size = params.get("batch_size", 20)

        # Hard cutoff — only process candidates who came in on or after Mar 5 2026
        CUTOFF = datetime(2026, 3, 5, 0, 0, 0)

        results = {
            "candidates_checked": 0,
            "already_have_description": 0,
            "no_resume_file": 0,
            "parsed_and_queued": 0,
            "parse_failed": 0,
        }
        candidate_details = []

        try:
            from models import ParsedEmail
            from extensions import db

            # Find completed ParsedEmail records with a candidate, received after the cutoff,
            # whose vetted_at is either None (never vetted) or already set (was vetted with
            # empty description — needs a re-run after parsing).
            # We filter for records whose candidate description is empty by checking Bullhorn.
            candidates_q = (
                ParsedEmail.query
                .filter(
                    ParsedEmail.status == 'completed',
                    ParsedEmail.bullhorn_candidate_id.isnot(None),
                    ParsedEmail.received_at >= CUTOFF,
                )
                .order_by(ParsedEmail.received_at.asc())
                .limit(batch_size * 5)  # over-fetch to account for already-described candidates
                .all()
            )
        except Exception as e:
            self.logger.error(f"incomplete_rescreen: DB query failed: {e}")
            return {"summary": f"DB error: {e}", **results}

        # Deduplicate by candidate ID — one ParsedEmail per candidate is enough
        seen_candidate_ids = set()
        unique_records = []
        for pe in candidates_q:
            if pe.bullhorn_candidate_id not in seen_candidate_ids:
                seen_candidate_ids.add(pe.bullhorn_candidate_id)
                unique_records.append(pe)

        processed = 0
        for pe in unique_records:
            if processed >= batch_size:
                break

            cid = pe.bullhorn_candidate_id
            results["candidates_checked"] += 1

            # Step 1: Check whether the candidate already has a description in Bullhorn
            try:
                cand_resp = requests.get(
                    f"{self._bh_url()}entity/Candidate/{cid}",
                    headers=self._bh_headers(),
                    params={"fields": "id,firstName,lastName,description"},
                    timeout=15
                )
                cand_resp.raise_for_status()
                cand_data = cand_resp.json().get("data", {})
            except Exception as e:
                self.logger.warning(f"incomplete_rescreen: could not fetch candidate {cid}: {e}")
                results["parse_failed"] += 1
                continue

            desc = (cand_data.get("description") or "").strip()
            name = f"{cand_data.get('firstName', '')} {cand_data.get('lastName', '')}".strip()

            if desc:
                results["already_have_description"] += 1
                # Mark vetted_at so this candidate is not re-checked next cycle
                if not pe.vetted_at:
                    try:
                        pe.vetted_at = datetime.utcnow()
                        from extensions import db
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                continue

            # Step 2: Find a resume file attachment
            try:
                file_resp = requests.get(
                    f"{self._bh_url()}entity/Candidate/{cid}/fileAttachments",
                    headers=self._bh_headers(),
                    params={"fields": "id,name,type,contentType"},
                    timeout=15
                )
                file_resp.raise_for_status()
                files = file_resp.json().get("data", [])
                resume_files = [
                    f for f in files if
                    (f.get("type", "").lower() == "resume") or
                    f.get("name", "").lower().endswith((".pdf", ".doc", ".docx"))
                ]
            except Exception as e:
                self.logger.warning(f"incomplete_rescreen: file fetch failed for {cid}: {e}")
                resume_files = []

            if not resume_files:
                results["no_resume_file"] += 1
                candidate_details.append({"candidate_id": cid, "name": name, "status": "no_resume_file"})
                continue

            processed += 1

            if dry_run:
                results["parsed_and_queued"] += 1
                candidate_details.append({
                    "candidate_id": cid,
                    "name": name,
                    "resume_file": resume_files[0].get("name", "unknown"),
                    "status": "would_process"
                })
                continue

            # Step 3: Parse resume → write back to Bullhorn
            try:
                text = self._download_and_extract_text(cid, resume_files[0])
                if not text or len(text.strip()) < 50:
                    self.logger.warning(f"incomplete_rescreen: resume parse returned empty text for {cid}")
                    results["parse_failed"] += 1
                    candidate_details.append({"candidate_id": cid, "name": name, "status": "parse_empty"})
                    continue

                requests.post(
                    f"{self._bh_url()}entity/Candidate/{cid}",
                    headers={**self._bh_headers(), "Content-Type": "application/json"},
                    json={"description": text[:20000]},
                    timeout=15
                )
                self.logger.info(f"incomplete_rescreen: reparsed description for candidate {cid} ({name})")
            except Exception as e:
                self.logger.warning(f"incomplete_rescreen: parse/write failed for {cid}: {e}")
                results["parse_failed"] += 1
                candidate_details.append({"candidate_id": cid, "name": name, "status": "error"})
                continue

            # Step 4: Clear old 0% vetting logs so the candidate is treated as brand new
            try:
                from extensions import db
                from models import CandidateVettingLog, CandidateJobMatch
                old_logs = CandidateVettingLog.query.filter_by(
                    bullhorn_candidate_id=cid,
                    highest_match_score=0
                ).all()
                for old_log in old_logs:
                    CandidateJobMatch.query.filter_by(vetting_log_id=old_log.id).delete()
                    db.session.delete(old_log)
                if old_logs:
                    self.logger.info(f"incomplete_rescreen: cleared {len(old_logs)} old 0% vetting log(s) for candidate {cid}")
            except Exception as e:
                db.session.rollback()
                self.logger.warning(f"incomplete_rescreen: could not clear old vetting logs for {cid}: {e}")

            # Step 5: Reset vetted_at so the vetting cycle re-evaluates this candidate
            try:
                pe.vetted_at = None
                db.session.commit()
                self.logger.info(f"incomplete_rescreen: reset vetted_at for ParsedEmail {pe.id} (candidate {cid})")
            except Exception as e:
                db.session.rollback()
                self.logger.warning(f"incomplete_rescreen: could not reset vetted_at for {cid}: {e}")

            results["parsed_and_queued"] += 1
            candidate_details.append({
                "candidate_id": cid,
                "name": name,
                "resume_file": resume_files[0].get("name", "unknown"),
                "status": "reparsed_and_queued"
            })

        summary_parts = [
            f"{'DRY RUN: ' if dry_run else ''}Checked {results['candidates_checked']} inbound candidates (since Mar 8 2026)",
            f"{results['already_have_description']} already have a description",
            f"{results['no_resume_file']} have no resume file",
            f"{results['parsed_and_queued']} reparsed {'(would requeue)' if dry_run else 'and queued for re-screening'}",
        ]
        if results["parse_failed"]:
            summary_parts.append(f"{results['parse_failed']} failed")

        return {
            "summary": " · ".join(summary_parts),
            "dry_run": dry_run,
            **results,
            "candidates": candidate_details[:50]
        }

    def _builtin_salesrep_sync(self, params):
        from salesrep_sync_service import run_salesrep_sync
        result = run_salesrep_sync(self.bullhorn)
        return {
            "summary": f"Sales Rep Sync: scanned {result.get('scanned', 0)}, "
                       f"updated {result.get('updated', 0)}, errors {result.get('errors', 0)}",
            **result
        }

    def _builtin_update_field_bulk(self, params):
        # THREAD-SAFETY RULE: This built-in runs in a background daemon thread.
        # All Bullhorn HTTP calls here MUST use standalone requests.get/post —
        # never bh.session.* — because requests.Session is shared with the main
        # Flask app and the APScheduler and is NOT thread-safe. Using the shared
        # session causes silent write failures (Bullhorn returns changeType:UPDATE
        # but data never persists). The other long-running built-ins already follow
        # this pattern. The Scout Automation Module must do the same.
        entity = params.get("entity", "Candidate")
        query = params.get("query", "").strip()
        updates = params.get("updates", {})
        batch_size = min(int(params.get("batch_size", 500)), 500)
        dry_run = params.get("dry_run", True)
        limit = params.get("limit")

        if not query:
            return {"error": "query parameter is required (Lucene search string)"}
        if not updates or not isinstance(updates, dict):
            return {"error": "updates parameter is required (dict of field→value pairs)"}

        bh = self.bullhorn
        search_url = f"{self._bh_url()}search/{entity}"

        sample_resp = requests.get(search_url, headers=self._bh_headers(), params={
            "query": query, "fields": "id", "count": 5, "start": 0,
        }, timeout=30)
        sample_data = sample_resp.json()
        total = sample_data.get("total", 0)
        sample_ids = [r["id"] for r in sample_data.get("data", [])]
        effective_total = min(total, int(limit)) if limit else total

        if dry_run:
            batches = (effective_total + batch_size - 1) // batch_size if effective_total else 0
            return {
                "summary": (
                    f"DRY RUN: Found {total:,} {entity} records matching '{query}'. "
                    + (f"Capped to {effective_total:,} by limit. " if limit else "")
                    + f"Would update {updates} across ~{batches:,} batch(es). "
                    f"Re-run with dry_run=false to execute."
                ),
                "dry_run": True,
                "entity": entity,
                "total_found": total,
                "effective_total": effective_total,
                "sample_ids": sample_ids,
                "fields_to_update": updates,
                "estimated_batches": batches,
            }

        succeeded = 0
        failed = 0
        failed_ids = []
        sample_updated_ids = []
        start = 0
        batch_number = 0
        first_batch_verified = False

        while start < effective_total:
            this_count = min(batch_size, effective_total - start)

            # Refresh Bullhorn auth token every 50 batches (~25k records, ~10 min) to prevent expiry
            if batch_number > 0 and batch_number % 50 == 0:
                try:
                    bh.authenticate()
                    self.logger.info(f"update_field_bulk: refreshed Bullhorn auth at batch {batch_number}")
                except Exception as auth_err:
                    self.logger.warning(f"update_field_bulk: auth refresh failed at batch {batch_number}: {auth_err}")

            resp = requests.get(search_url, headers=self._bh_headers(), params={
                "query": query, "fields": "id",
                "count": this_count, "start": start,
            }, timeout=30)
            batch_ids = [r["id"] for r in resp.json().get("data", [])]

            if not batch_ids:
                break

            for record_id in batch_ids:
                try:
                    upd = requests.post(
                        f"{self._bh_url()}entity/{entity}/{record_id}",
                        headers=self._bh_headers(),
                        json=updates, timeout=15
                    )
                    # Parse response body — Bullhorn returns HTTP 200 even for errors
                    try:
                        upd_body = upd.json()
                    except Exception:
                        upd_body = {}

                    bh_error = upd_body.get("errorCode") or upd_body.get("errors")
                    bh_confirmed = (
                        upd_body.get("changeType") == "UPDATE"
                        or upd_body.get("changedEntityId") is not None
                    )

                    if upd.status_code in (200, 201) and not bh_error and bh_confirmed:
                        succeeded += 1
                        if len(sample_updated_ids) < 5:
                            sample_updated_ids.append(record_id)

                        # Read-back spot-check after the very first confirmed update
                        if not first_batch_verified and succeeded == 1:
                            first_batch_verified = True
                            try:
                                check = requests.get(
                                    f"{self._bh_url()}entity/{entity}/{record_id}",
                                    headers=self._bh_headers(),
                                    params={"fields": ",".join(updates.keys())},
                                    timeout=15
                                )
                                check_data = check.json()
                                record_data = check_data.get("data", check_data)
                                mismatches = {
                                    field: {"expected": val, "actual": record_data.get(field)}
                                    for field, val in updates.items()
                                    if record_data.get(field) != val
                                }
                                if mismatches:
                                    return {
                                        "error": (
                                            f"Read-back verification FAILED after first update (ID {record_id}). "
                                            f"Changes did not persist in Bullhorn. Halting to prevent wasted API calls."
                                        ),
                                        "record_id": record_id,
                                        "mismatches": mismatches,
                                        "raw_readback": record_data,
                                        "succeeded_before_halt": succeeded,
                                    }
                                self.logger.info(
                                    f"update_field_bulk: read-back verified for ID {record_id} — changes confirmed"
                                )
                            except Exception as verify_err:
                                self.logger.warning(f"update_field_bulk: read-back check failed: {verify_err}")
                    else:
                        failed += 1
                        if len(failed_ids) < 10:
                            failed_ids.append({
                                "id": record_id,
                                "status": upd.status_code,
                                "bh_error": bh_error if bh_error else None,
                                "response": upd_body if upd_body else upd.text[:300]
                            })
                except Exception as e:
                    failed += 1
                    if len(failed_ids) < 10:
                        failed_ids.append({"id": record_id, "error": str(e)[:100]})

            start += len(batch_ids)
            batch_number += 1
            time.sleep(0.05)

        return {
            "summary": (
                f"Bulk update complete: {succeeded:,} {entity} records updated "
                f"({updates}), {failed:,} failed."
            ),
            "dry_run": False,
            "entity": entity,
            "total_processed": succeeded + failed,
            "succeeded": succeeded,
            "failed": failed,
            "sample_updated_ids": sample_updated_ids,
            "failed_ids": failed_ids,
        }

    def _builtin_screening_audit(self, params):
        from vetting_audit_service import VettingAuditService

        batch_size = int(params.get("batch_size", 20))
        svc = VettingAuditService()
        result = svc.run_audit_cycle(batch_size=batch_size)

        details_summary = ""
        if result.get("details"):
            items = []
            for d in result["details"][:10]:
                items.append(
                    f"{d.get('candidate_name', 'Unknown')} — {d.get('finding_type', '')} "
                    f"(original {d.get('original_score', 0):.0f}%, action: {d.get('action_taken', 'none')})"
                )
            details_summary = "; ".join(items)

        return {
            "summary": (
                f"Audited {result['total_audited']} result(s): "
                f"{result['issues_found']} issue(s) found, "
                f"{result['revets_triggered']} re-vet(s) triggered. "
                + (details_summary if details_summary else "No issues detected.")
            ),
            "total_audited": result["total_audited"],
            "issues_found": result["issues_found"],
            "revets_triggered": result["revets_triggered"],
            "details": result.get("details", []),
        }

