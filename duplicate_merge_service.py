import json
import logging
import time
from datetime import datetime, timedelta
from collections import defaultdict
from extensions import db

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.80
BATCH_SIZE = 200
RECENT_WINDOW_HOURS = 2


class DuplicateMergeService:
    def __init__(self):
        self._bullhorn = None

    @property
    def bullhorn(self):
        if self._bullhorn is None:
            from bullhorn_service import BullhornService
            self._bullhorn = BullhornService()
        return self._bullhorn

    def _ensure_auth(self):
        if not self.bullhorn.authenticate():
            raise RuntimeError("Bullhorn authentication failed")

    def _candidate_name(self, candidate):
        first = candidate.get('firstName', '') or ''
        last = candidate.get('lastName', '') or ''
        return f"{first} {last}".strip()

    def _has_active_placement(self, candidate_id):
        try:
            where = f"candidate.id={candidate_id} AND status='Approved'"
            url = f"{self.bullhorn.base_url}query/Placement"
            params = {
                'where': where,
                'fields': 'id,status',
                'count': 1,
                'BhRestToken': self.bullhorn.rest_token
            }
            resp = self.bullhorn.session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                placements = data.get('data', [])
                return len(placements) > 0
        except Exception as e:
            logger.warning(f"Error checking placements for candidate {candidate_id}: {e}")
        return False

    def determine_primary(self, candidate_a, candidate_b):
        id_a = candidate_a.get('id')
        id_b = candidate_b.get('id')

        a_has_placement = self._has_active_placement(id_a)
        b_has_placement = self._has_active_placement(id_b)

        if a_has_placement and b_has_placement:
            return None, None, "both_active_placements"

        if a_has_placement:
            return candidate_a, candidate_b, "active_placement"
        if b_has_placement:
            return candidate_b, candidate_a, "active_placement"

        date_a = candidate_a.get('dateAdded', 0)
        date_b = candidate_b.get('dateAdded', 0)
        if isinstance(date_a, (int, float)) and isinstance(date_b, (int, float)):
            if date_a >= date_b:
                return candidate_a, candidate_b, "most_recent"
            else:
                return candidate_b, candidate_a, "most_recent"

        return candidate_a, candidate_b, "default"

    def _get_candidate_submissions(self, candidate_id):
        try:
            url = f"{self.bullhorn.base_url}query/JobSubmission"
            params = {
                'where': f"candidate.id={candidate_id}",
                'fields': 'id,jobOrder,status,dateWebResponse,dateAdded,source',
                'count': 500,
                'BhRestToken': self.bullhorn.rest_token
            }
            resp = self.bullhorn.session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json().get('data', [])
        except Exception as e:
            logger.error(f"Error fetching submissions for candidate {candidate_id}: {e}")
        return []

    def _get_candidate_notes(self, candidate_id):
        try:
            url = f"{self.bullhorn.base_url}entity/Candidate/{candidate_id}/notes"
            params = {
                'fields': 'id,action,comments,dateAdded,commentingPerson',
                'count': 500,
                'BhRestToken': self.bullhorn.rest_token
            }
            resp = self.bullhorn.session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json().get('data', [])
        except Exception as e:
            logger.error(f"Error fetching notes for candidate {candidate_id}: {e}")
        return []

    def _get_candidate_files(self, candidate_id):
        try:
            url = f"{self.bullhorn.base_url}entityFiles/Candidate/{candidate_id}"
            params = {'BhRestToken': self.bullhorn.rest_token}
            resp = self.bullhorn.session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json().get('EntityFiles', [])
        except Exception as e:
            logger.error(f"Error fetching files for candidate {candidate_id}: {e}")
        return []

    def _transfer_submission(self, primary_id, submission):
        job_order = submission.get('jobOrder', {})
        job_id = job_order.get('id') if isinstance(job_order, dict) else job_order
        if not job_id:
            return False

        try:
            existing = self._get_candidate_submissions(primary_id)
            existing_job_ids = {
                (s.get('jobOrder', {}).get('id') if isinstance(s.get('jobOrder'), dict) else s.get('jobOrder'))
                for s in existing
            }
            if job_id in existing_job_ids:
                logger.debug(f"  Submission for job {job_id} already exists on primary {primary_id}, skipping")
                return False

            payload = {
                'candidate': {'id': primary_id},
                'jobOrder': {'id': job_id},
                'status': submission.get('status', 'New Lead'),
                'dateWebResponse': submission.get('dateWebResponse'),
                'source': submission.get('source', 'Merged from duplicate'),
            }
            url = f"{self.bullhorn.base_url}entity/JobSubmission"
            params = {'BhRestToken': self.bullhorn.rest_token}
            resp = self.bullhorn.session.put(url, json=payload, params=params, timeout=30)
            if resp.status_code in (200, 201):
                logger.info(f"  ✅ Transferred submission for job {job_id} to primary {primary_id}")
                return True
            else:
                logger.warning(f"  ⚠️ Failed to transfer submission for job {job_id}: {resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"  Error transferring submission: {e}")
            return False

    def _transfer_note(self, primary_id, note):
        try:
            commenting_person_id = self.bullhorn.user_id or 1147490
            payload = {
                'personReference': {'id': primary_id},
                'candidates': [{'id': primary_id}],
                'action': note.get('action', 'General Notes'),
                'comments': note.get('comments', ''),
                'commentingPerson': {'id': commenting_person_id},
                'isDeleted': False,
            }
            url = f"{self.bullhorn.base_url}entity/Note"
            params = {'BhRestToken': self.bullhorn.rest_token}
            resp = self.bullhorn.session.put(url, json=payload, params=params, timeout=30)
            if resp.status_code in (200, 201):
                return True
            else:
                logger.warning(f"  ⚠️ Failed to transfer note {note.get('id')}: {resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"  Error transferring note: {e}")
            return False

    def _transfer_file(self, primary_id, duplicate_id, file_info):
        try:
            file_id = file_info.get('id')
            if not file_id:
                return False

            get_url = f"{self.bullhorn.base_url}file/Candidate/{duplicate_id}/{file_id}"
            params = {'BhRestToken': self.bullhorn.rest_token}
            resp = self.bullhorn.session.get(get_url, params=params, timeout=60)
            if resp.status_code != 200:
                logger.warning(f"  ⚠️ Could not download file {file_id} from candidate {duplicate_id}")
                return False

            file_data = resp.json().get('File', {})
            file_content = file_data.get('fileContent')
            file_name = file_data.get('name', file_info.get('name', 'transferred_file'))
            file_type = file_info.get('type', 'Resume')

            if not file_content:
                logger.warning(f"  ⚠️ File {file_id} has no content")
                return False

            upload_url = f"{self.bullhorn.base_url}file/Candidate/{primary_id}"
            upload_payload = {
                'externalID': f'merged_{duplicate_id}_{file_id}',
                'fileContent': file_content,
                'fileType': file_type,
                'name': file_name,
                'description': f'Merged from candidate {duplicate_id}',
            }
            upload_resp = self.bullhorn.session.put(upload_url, json=upload_payload, params=params, timeout=60)
            if upload_resp.status_code in (200, 201):
                logger.info(f"  ✅ Transferred file '{file_name}' to primary {primary_id}")
                return True
            else:
                logger.warning(f"  ⚠️ Failed to upload file to primary: {upload_resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"  Error transferring file: {e}")
            return False

    def _enrich_primary(self, primary_id, duplicate):
        try:
            primary = self.bullhorn.get_candidate(primary_id)
            if not primary:
                return

            enrichable = ['phone', 'mobile', 'occupation', 'companyName', 'skillSet',
                          'employmentPreference', 'email2', 'email3']
            update_data = {}
            for field in enrichable:
                existing_val = primary.get(field)
                dup_val = duplicate.get(field)
                if dup_val and not existing_val:
                    update_data[field] = dup_val

            primary_addr = primary.get('address') or {}
            dup_addr = duplicate.get('address') or {}
            if dup_addr and isinstance(dup_addr, dict):
                addr_update = {}
                for af in ['address1', 'city', 'state', 'zip', 'countryID']:
                    if dup_addr.get(af) and not primary_addr.get(af):
                        addr_update[af] = dup_addr[af]
                if addr_update:
                    update_data['address'] = addr_update

            if update_data:
                self.bullhorn.update_candidate(primary_id, update_data)
                logger.info(f"  📝 Enriched primary {primary_id} with fields: {list(update_data.keys())}")
        except Exception as e:
            logger.error(f"  Error enriching primary {primary_id}: {e}")

    def _archive_duplicate(self, candidate_id):
        try:
            self.bullhorn.update_candidate(candidate_id, {'status': 'Archive'})
            logger.info(f"  🗄️ Archived duplicate candidate {candidate_id}")
            return True
        except Exception as e:
            logger.error(f"  Error archiving candidate {candidate_id}: {e}")
            return False

    def _format_bullhorn_ts(self, ts_value):
        if not ts_value:
            return 'N/A'
        try:
            if isinstance(ts_value, (int, float)):
                dt = datetime.utcfromtimestamp(ts_value / 1000)
                return dt.strftime('%m/%d/%Y %I:%M %p')
            return str(ts_value)
        except Exception:
            return str(ts_value)

    def _add_merge_note(self, candidate_id, other_id, is_primary=True, original_dates=None, transferred=None):
        try:
            if is_primary:
                comment = f"[Scout Genius Auto-Merge] This record received data merged from duplicate candidate ID {other_id}."
                if original_dates and transferred:
                    comment += "\n\nOriginal timestamps from duplicate record (Bullhorn resets Date Added on transfer):"
                    if original_dates.get('submissions'):
                        comment += "\n\n📋 Submissions:"
                        for s in original_dates['submissions']:
                            comment += f"\n  • Job #{s['job_id']} — Original Date Added: {s['dateAdded']}, Web Response: {s['dateWebResponse']}"
                    if original_dates.get('notes'):
                        comment += "\n\n📝 Notes:"
                        for n in original_dates['notes']:
                            comment += f"\n  • {n['action']} — Original Date Added: {n['dateAdded']}"
                    if original_dates.get('files'):
                        comment += "\n\n📎 Files:"
                        for f in original_dates['files']:
                            comment += f"\n  • {f['name']} — Original Date Added: {f['dateAdded']}"
            else:
                comment = f"[Scout Genius Auto-Merge] This record was identified as a duplicate of candidate ID {other_id}. All data has been transferred and this record has been archived."

            self.bullhorn.create_candidate_note(
                candidate_id=candidate_id,
                note_text=comment,
                action='General Notes'
            )
        except Exception as e:
            logger.warning(f"  Could not add merge note to {candidate_id}: {e}")

    def merge_candidates(self, primary, duplicate, confidence, match_field, merge_type='scheduled'):
        from models import CandidateMergeLog

        primary_id = primary.get('id')
        duplicate_id = duplicate.get('id')
        primary_name = self._candidate_name(primary)
        dup_name = self._candidate_name(duplicate)

        logger.info(f"🔀 MERGING: {dup_name} (ID:{duplicate_id}) → {primary_name} (ID:{primary_id}) [confidence={confidence:.2f}, field={match_field}]")

        transferred = {'submissions': 0, 'notes': 0, 'files': 0}
        original_dates = {'submissions': [], 'notes': [], 'files': []}

        submissions = self._get_candidate_submissions(duplicate_id)
        for sub in submissions:
            if self._transfer_submission(primary_id, sub):
                transferred['submissions'] += 1
                job_order = sub.get('jobOrder', {})
                job_id = job_order.get('id') if isinstance(job_order, dict) else job_order
                original_dates['submissions'].append({
                    'job_id': job_id,
                    'dateAdded': self._format_bullhorn_ts(sub.get('dateAdded')),
                    'dateWebResponse': self._format_bullhorn_ts(sub.get('dateWebResponse')),
                })
            time.sleep(0.5)

        notes = self._get_candidate_notes(duplicate_id)
        for note in notes:
            if self._transfer_note(primary_id, note):
                transferred['notes'] += 1
                original_dates['notes'].append({
                    'action': note.get('action', 'Note'),
                    'dateAdded': self._format_bullhorn_ts(note.get('dateAdded')),
                })
            time.sleep(0.5)

        files = self._get_candidate_files(duplicate_id)
        for f in files:
            if self._transfer_file(primary_id, duplicate_id, f):
                transferred['files'] += 1
                original_dates['files'].append({
                    'name': f.get('name', 'Unknown'),
                    'dateAdded': self._format_bullhorn_ts(f.get('dateAdded')),
                })
            time.sleep(0.5)

        self._enrich_primary(primary_id, duplicate)

        self._add_merge_note(primary_id, duplicate_id, is_primary=True, original_dates=original_dates, transferred=transferred)
        self._add_merge_note(duplicate_id, primary_id, is_primary=False)

        self._archive_duplicate(duplicate_id)

        merge_log = CandidateMergeLog(
            primary_candidate_id=primary_id,
            duplicate_candidate_id=duplicate_id,
            primary_name=primary_name,
            duplicate_name=dup_name,
            confidence_score=confidence,
            match_field=match_field,
            merge_type=merge_type,
            items_transferred=json.dumps(transferred),
            merged_by='system',
            skipped=False,
        )
        db.session.add(merge_log)
        db.session.commit()

        logger.info(f"  ✅ Merge complete: {transferred['submissions']} submissions, {transferred['notes']} notes, {transferred['files']} files transferred")
        return transferred

    def _log_skip(self, candidate_a, candidate_b, confidence, match_field, reason, merge_type='scheduled'):
        from models import CandidateMergeLog

        merge_log = CandidateMergeLog(
            primary_candidate_id=candidate_a.get('id', 0),
            duplicate_candidate_id=candidate_b.get('id', 0),
            primary_name=self._candidate_name(candidate_a),
            duplicate_name=self._candidate_name(candidate_b),
            confidence_score=confidence,
            match_field=match_field,
            merge_type=merge_type,
            skipped=True,
            skip_reason=reason,
            merged_by='system',
        )
        db.session.add(merge_log)
        db.session.commit()

    def _normalize_name(self, name_str):
        if not name_str:
            return ''
        import re
        return re.sub(r'[^a-z]', '', name_str.strip().lower())

    def _names_match(self, candidate_a, candidate_b):
        first_a = self._normalize_name(candidate_a.get('firstName'))
        last_a = self._normalize_name(candidate_a.get('lastName'))
        first_b = self._normalize_name(candidate_b.get('firstName'))
        last_b = self._normalize_name(candidate_b.get('lastName'))

        if not first_a or not first_b or not last_a or not last_b:
            return False

        if first_a == first_b and last_a == last_b:
            return True

        if first_a == first_b and (last_a.startswith(last_b[:3]) or last_b.startswith(last_a[:3])):
            return True

        if last_a == last_b and (first_a.startswith(first_b[:3]) or first_b.startswith(first_a[:3])):
            return True

        return False

    def _compute_match_confidence(self, candidate_a, candidate_b):
        email_a = (candidate_a.get('email') or '').strip().lower()
        email2_a = (candidate_a.get('email2') or '').strip().lower()
        email3_a = (candidate_a.get('email3') or '').strip().lower()
        emails_a = {e for e in [email_a, email2_a, email3_a] if e}

        email_b = (candidate_b.get('email') or '').strip().lower()
        email2_b = (candidate_b.get('email2') or '').strip().lower()
        email3_b = (candidate_b.get('email3') or '').strip().lower()
        emails_b = {e for e in [email_b, email2_b, email3_b] if e}

        shared_emails = emails_a & emails_b
        if shared_emails:
            if email_a and email_a in emails_b:
                return 1.0, 'email'
            return 0.95, 'email_secondary'

        phone_a_digits = ''.join(filter(str.isdigit, candidate_a.get('phone') or ''))
        mobile_a_digits = ''.join(filter(str.isdigit, candidate_a.get('mobile') or ''))
        phone_b_digits = ''.join(filter(str.isdigit, candidate_b.get('phone') or ''))
        mobile_b_digits = ''.join(filter(str.isdigit, candidate_b.get('mobile') or ''))

        phones_a = {p for p in [phone_a_digits, mobile_a_digits] if len(p) >= 10}
        phones_b = {p for p in [phone_b_digits, mobile_b_digits] if len(p) >= 10}
        if phones_a & phones_b:
            if self._names_match(candidate_a, candidate_b):
                return 0.90, 'phone+name'
            else:
                logger.debug(f"  Phone match rejected — names differ: "
                             f"{candidate_a.get('firstName')} {candidate_a.get('lastName')} vs "
                             f"{candidate_b.get('firstName')} {candidate_b.get('lastName')}")
                return 0.0, 'phone_name_mismatch'

        return 0.0, 'none'

    def _search_all_candidates_batch(self, start=0, count=BATCH_SIZE):
        try:
            url = f"{self.bullhorn.base_url}search/Candidate"
            params = {
                'query': 'isDeleted:0 AND -status:Archive',
                'fields': 'id,firstName,lastName,email,email2,email3,phone,mobile,dateAdded,status',
                'count': count,
                'start': start,
                'sort': 'id',
                'BhRestToken': self.bullhorn.rest_token
            }
            resp = self.bullhorn.session.get(url, params=params, timeout=60)
            if resp.status_code == 200:
                data = resp.json().get('data', [])
                logger.info(f"🔍 Bulk scan batch: start={start}, returned {len(data)} candidate(s)")
                return data
            else:
                logger.error(
                    f"🔍 Bulk scan: Bullhorn search/Candidate returned status {resp.status_code} "
                    f"at start={start}: {resp.text[:500]}"
                )
        except Exception as e:
            logger.error(f"Error fetching candidate batch at start={start}: {e}", exc_info=True)
        return []

    def _search_recent_candidates(self, hours=RECENT_WINDOW_HOURS):
        try:
            cutoff_ms = int((datetime.utcnow() - timedelta(hours=hours)).timestamp() * 1000)
            lucene_query = f'isDeleted:0 AND -status:Archive AND dateAdded:[{cutoff_ms} TO *]'
            url = f"{self.bullhorn.base_url}search/Candidate"
            params = {
                'query': lucene_query,
                'fields': 'id,firstName,lastName,email,email2,email3,phone,mobile,dateAdded,status',
                'count': 500,
                'sort': '-dateAdded',
                'BhRestToken': self.bullhorn.rest_token
            }
            logger.info(f"🔍 Dedup: searching recent candidates (last {hours}h), cutoff_ms={cutoff_ms}")
            resp = self.bullhorn.session.get(url, params=params, timeout=60)
            if resp.status_code == 200:
                data = resp.json().get('data', [])
                logger.info(f"🔍 Dedup: found {len(data)} recent candidate(s)")
                return data
            else:
                logger.error(
                    f"🔍 Dedup: Bullhorn search/Candidate returned status {resp.status_code}: "
                    f"{resp.text[:500]}"
                )
        except Exception as e:
            logger.error(f"Error fetching recent candidates: {e}", exc_info=True)
        return []

    def _find_matches_for_candidate(self, candidate):
        email = (candidate.get('email') or '').strip().lower()
        phone_digits = ''.join(filter(str.isdigit, candidate.get('phone') or ''))
        mobile_digits = ''.join(filter(str.isdigit, candidate.get('mobile') or ''))

        matches = []

        if email:
            search_query = f'(email:"{email}" OR email2:"{email}" OR email3:"{email}")'
            try:
                url = f"{self.bullhorn.base_url}search/Candidate"
                params = {
                    'query': search_query,
                    'fields': 'id,firstName,lastName,email,email2,email3,phone,mobile,dateAdded,status',
                    'count': 20,
                    'BhRestToken': self.bullhorn.rest_token
                }
                resp = self.bullhorn.session.get(url, params=params, timeout=30)
                if resp.status_code == 200:
                    results = resp.json().get('data', [])
                    for r in results:
                        if r.get('id') != candidate.get('id') and (r.get('status') or '').lower() != 'archive':
                            matches.append(r)
            except Exception as e:
                logger.warning(f"Error searching by email for candidate {candidate.get('id')}: {e}")

        if not matches and (len(phone_digits) >= 10 or len(mobile_digits) >= 10):
            search_phone = phone_digits if len(phone_digits) >= 10 else mobile_digits
            search_query = f'(phone:"{search_phone}" OR mobile:"{search_phone}")'
            try:
                url = f"{self.bullhorn.base_url}search/Candidate"
                params = {
                    'query': search_query,
                    'fields': 'id,firstName,lastName,email,email2,email3,phone,mobile,dateAdded,status',
                    'count': 20,
                    'BhRestToken': self.bullhorn.rest_token
                }
                resp = self.bullhorn.session.get(url, params=params, timeout=30)
                if resp.status_code == 200:
                    results = resp.json().get('data', [])
                    for r in results:
                        if r.get('id') != candidate.get('id') and (r.get('status') or '').lower() != 'archive':
                            already_found = {m.get('id') for m in matches}
                            if r.get('id') not in already_found:
                                matches.append(r)
            except Exception as e:
                logger.warning(f"Error searching by phone for candidate {candidate.get('id')}: {e}")

        return matches

    def run_bulk_scan(self, progress_callback=None):
        from models import CandidateMergeLog

        self._ensure_auth()

        stats = {
            'candidates_scanned': 0,
            'duplicates_found': 0,
            'merged': 0,
            'skipped_below_threshold': 0,
            'skipped_both_placements': 0,
            'skipped_already_processed': 0,
            'errors': 0,
            'started_at': datetime.utcnow().isoformat(),
        }

        processed_ids = set()

        already_merged = set()
        existing_logs = CandidateMergeLog.query.filter_by(skipped=False).all()
        for log in existing_logs:
            already_merged.add(log.duplicate_candidate_id)

        start = 0
        while True:
            batch = self._search_all_candidates_batch(start=start, count=BATCH_SIZE)
            if not batch:
                break

            stats['candidates_scanned'] += len(batch)

            for candidate in batch:
                cid = candidate.get('id')
                if cid in processed_ids or cid in already_merged:
                    continue

                matches = self._find_matches_for_candidate(candidate)
                if not matches:
                    continue

                for match in matches:
                    mid = match.get('id')
                    if mid in processed_ids or mid in already_merged:
                        continue

                    confidence, match_field = self._compute_match_confidence(candidate, match)

                    if confidence < CONFIDENCE_THRESHOLD:
                        stats['skipped_below_threshold'] += 1
                        continue

                    stats['duplicates_found'] += 1

                    primary, duplicate, reason = self.determine_primary(candidate, match)

                    if primary is None:
                        logger.warning(f"⚠️ SKIP: Both candidates {cid} and {mid} have active placements")
                        self._log_skip(candidate, match, confidence, match_field,
                                       "Both records have active placements", merge_type='bulk')
                        stats['skipped_both_placements'] += 1
                        continue

                    try:
                        self.merge_candidates(primary, duplicate, confidence, match_field, merge_type='bulk')
                        dup_id = duplicate.get('id')
                        processed_ids.add(dup_id)
                        already_merged.add(dup_id)
                        stats['merged'] += 1
                    except Exception as e:
                        logger.error(f"  ❌ Merge failed for {cid} + {mid}: {e}")
                        stats['errors'] += 1

                    time.sleep(1.0)

                processed_ids.add(cid)
                time.sleep(0.3)

            if progress_callback:
                progress_callback(stats)

            start += len(batch)
            logger.info(f"📊 Bulk scan progress: scanned={stats['candidates_scanned']}, merged={stats['merged']}, skipped_placement={stats['skipped_both_placements']}")

            if len(batch) < BATCH_SIZE:
                break

            time.sleep(2)

        stats['completed_at'] = datetime.utcnow().isoformat()
        logger.info(f"✅ Bulk scan complete: {json.dumps(stats, indent=2)}")
        return stats

    def run_scheduled_check(self):
        from models import CandidateMergeLog

        self._ensure_auth()

        stats = {
            'candidates_checked': 0,
            'merged': 0,
            'skipped': 0,
            'errors': 0,
        }

        already_merged = set()
        recent_logs = CandidateMergeLog.query.filter(
            CandidateMergeLog.merged_at >= datetime.utcnow() - timedelta(hours=24)
        ).all()
        for log in recent_logs:
            already_merged.add(log.duplicate_candidate_id)
            already_merged.add(log.primary_candidate_id)

        recent_candidates = self._search_recent_candidates()
        stats['candidates_checked'] = len(recent_candidates)

        if not recent_candidates:
            logger.info("🔍 Scheduled dedup check: no recent candidates found")
            return stats

        for candidate in recent_candidates:
            cid = candidate.get('id')
            if cid in already_merged:
                stats['skipped'] += 1
                continue

            matches = self._find_matches_for_candidate(candidate)
            if not matches:
                continue

            for match in matches:
                mid = match.get('id')
                if mid in already_merged:
                    continue

                confidence, match_field = self._compute_match_confidence(candidate, match)

                if confidence < CONFIDENCE_THRESHOLD:
                    continue

                primary, duplicate, reason = self.determine_primary(candidate, match)

                if primary is None:
                    self._log_skip(candidate, match, confidence, match_field,
                                   "Both records have active placements", merge_type='scheduled')
                    stats['skipped'] += 1
                    already_merged.add(cid)
                    already_merged.add(mid)
                    continue

                try:
                    self.merge_candidates(primary, duplicate, confidence, match_field, merge_type='scheduled')
                    dup_id = duplicate.get('id')
                    already_merged.add(dup_id)
                    already_merged.add(primary.get('id'))
                    stats['merged'] += 1
                except Exception as e:
                    logger.error(f"Scheduled merge failed for {cid} + {mid}: {e}")
                    stats['errors'] += 1

                time.sleep(0.5)

        logger.info(f"🔍 Scheduled dedup check complete: checked={stats['candidates_checked']}, merged={stats['merged']}, skipped={stats['skipped']}")
        return stats
