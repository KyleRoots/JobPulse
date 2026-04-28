"""Resume processing builtins: reparser, email extractor, occupation/title extractor, plus shared download/extract helpers.

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


class ResumeMixin:
    _GARBLED_PATTERNS = ["WW8Num", "OJQJ", "^J ", "phOJQJ", "OJQJo", "Num1z", "OJQJ^J"]

    def _is_garbled_description(self, text):
        if not text:
            return False
        matches = sum(1 for p in self._GARBLED_PATTERNS if p in text)
        return matches >= 3

    def _builtin_resume_reparser(self, params):
        dry_run = params.get("dry_run", True)
        days_back = params.get("days_back", 5)
        limit = params.get("limit", 100)
        fix_garbled = params.get("fix_garbled", False)
        candidate_ids = params.get("candidate_ids", [])

        cutoff_ts = int((datetime.utcnow() - timedelta(days=days_back)).timestamp() * 1000)
        search_url = f"{self._bh_url()}search/Candidate"
        candidates_to_process = []

        if candidate_ids:
            for cid in candidate_ids:
                try:
                    resp = requests.get(
                        f"{self._bh_url()}entity/Candidate/{cid}",
                        headers=self._bh_headers(),
                        params={"fields": "id,firstName,lastName,email,description"},
                        timeout=15
                    )
                    resp.raise_for_status()
                    data = resp.json().get("data", {})
                    desc = data.get("description") or ""
                    reason = "garbled" if self._is_garbled_description(desc) else ("empty" if not desc.strip() else "force")
                    data["_reason"] = reason
                    candidates_to_process.append(data)
                except Exception as e:
                    self.logger.warning(f"resume_reparser: could not fetch candidate {cid}: {e}")
        else:
            try:
                resp = requests.get(search_url, headers=self._bh_headers(), params={
                    "query": f"dateAdded:[{cutoff_ts} TO *] AND -description:[* TO *]",
                    "fields": "id,firstName,lastName,email,description,dateAdded",
                    "count": limit,
                    "sort": "-dateAdded"
                }, timeout=30)
                resp.raise_for_status()
                for c in resp.json().get("data", []):
                    c["_reason"] = "empty"
                    candidates_to_process.append(c)
            except Exception as e:
                self.logger.warning(f"resume_reparser: empty-description search failed: {e}")

            if fix_garbled:
                try:
                    resp2 = requests.get(search_url, headers=self._bh_headers(), params={
                        "query": f"dateAdded:[{cutoff_ts} TO *] AND description:[* TO *]",
                        "fields": "id,firstName,lastName,email,description,dateAdded",
                        "count": max(limit, 200),
                        "sort": "-dateAdded"
                    }, timeout=30)
                    resp2.raise_for_status()
                    existing_ids = {c["id"] for c in candidates_to_process}
                    for c in resp2.json().get("data", []):
                        if c["id"] in existing_ids:
                            continue
                        if self._is_garbled_description(c.get("description") or ""):
                            c["_reason"] = "garbled"
                            candidates_to_process.append(c)
                except Exception as e:
                    self.logger.warning(f"resume_reparser: garbled-description search failed: {e}")

        garbled_found = sum(1 for c in candidates_to_process if c.get("_reason") == "garbled")
        results = {
            "candidates_found": len(candidates_to_process),
            "garbled_found": garbled_found,
            "with_resume": 0,
            "no_file": 0,
            "parsed": 0,
            "cleared": 0,
            "failed": 0,
        }
        candidate_details = []

        for cand in candidates_to_process:
            cid = cand.get("id")
            name = f"{cand.get('firstName', '')} {cand.get('lastName', '')}".strip()
            reason = cand.get("_reason", "empty")

            file_url = f"{self._bh_url()}entity/Candidate/{cid}/fileAttachments"
            try:
                file_resp = requests.get(file_url, headers=self._bh_headers(),
                                         params={"fields": "id,name,type,contentType"}, timeout=15)
                file_resp.raise_for_status()
                files = file_resp.json().get("data", [])
                resume_files = [f for f in files if
                                (f.get("type", "").lower() == "resume") or
                                f.get("name", "").lower().endswith((".pdf", ".doc", ".docx"))]
            except Exception:
                resume_files = []

            if not resume_files:
                results["no_file"] += 1
                if reason == "garbled" and not dry_run:
                    update_url = f"{self._bh_url()}entity/Candidate/{cid}"
                    try:
                        requests.post(update_url,
                                      headers={**self._bh_headers(), "Content-Type": "application/json"},
                                      json={"description": ""}, timeout=15)
                        results["cleared"] += 1
                    except Exception as e:
                        self.logger.warning(f"resume_reparser: failed to clear garbled description for {cid}: {e}")
                continue

            results["with_resume"] += 1
            detail = {
                "candidate_id": cid,
                "name": name,
                "email": cand.get("email", ""),
                "resume_file": resume_files[0].get("name", "unknown"),
                "reason": reason,
                "status": "would_process" if dry_run else None
            }

            if not dry_run:
                try:
                    text = self._download_and_extract_text(cid, resume_files[0])
                    if text and len(text.strip()) > 50:
                        update_url = f"{self._bh_url()}entity/Candidate/{cid}"
                        requests.post(update_url,
                                      headers={**self._bh_headers(), "Content-Type": "application/json"},
                                      json={"description": text[:20000]}, timeout=15)
                        results["parsed"] += 1
                        detail["status"] = "parsed"
                    elif reason == "garbled":
                        update_url = f"{self._bh_url()}entity/Candidate/{cid}"
                        requests.post(update_url,
                                      headers={**self._bh_headers(), "Content-Type": "application/json"},
                                      json={"description": ""}, timeout=15)
                        results["cleared"] += 1
                        detail["status"] = "cleared_garbled"
                    else:
                        results["failed"] += 1
                        detail["status"] = "failed"
                except Exception as e:
                    self.logger.warning(f"resume_reparser: failed for candidate {cid}: {e}")
                    results["failed"] += 1
                    detail["status"] = "error"

            candidate_details.append(detail)

        mode_desc = "specific IDs" if candidate_ids else ("empty + garbled descriptions" if fix_garbled else "empty descriptions")
        summary_parts = [f"{'DRY RUN: ' if dry_run else ''}Scanned {results['candidates_found']} candidates ({mode_desc})"]
        if garbled_found:
            summary_parts.append(f"{garbled_found} garbled")
        summary_parts.append(f"{results['with_resume']} have resume files")
        if not dry_run:
            summary_parts.append(f"parsed {results['parsed']}, cleared {results['cleared']}, failed {results['failed']}")
        return {
            "summary": ", ".join(summary_parts),
            "dry_run": dry_run,
            "fix_garbled": fix_garbled,
            **results,
            "candidates": candidate_details[:50]
        }

    def _download_and_extract_text(self, candidate_id, resume_file_info):
        import base64
        import tempfile
        import os

        file_id = resume_file_info.get("id")
        filename = resume_file_info.get("name", "resume.pdf")

        dl_url = f"{self._bh_url()}file/Candidate/{candidate_id}/{file_id}"
        dl_resp = requests.get(dl_url, headers=self._bh_headers(), timeout=30)
        dl_resp.raise_for_status()
        file_data = dl_resp.json()
        file_content = file_data.get("File", {}).get("fileContent", "")

        if not file_content:
            return None

        raw_bytes = base64.b64decode(file_content)

        suffix = ""
        lower_name = filename.lower()
        if lower_name.endswith(".pdf"):
            suffix = ".pdf"
        elif lower_name.endswith(".docx"):
            suffix = ".docx"
        elif lower_name.endswith(".doc"):
            suffix = ".doc"
        else:
            suffix = ".pdf"

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(raw_bytes)
                tmp_path = tmp.name

            from resume_parser import ResumeParser
            parser = ResumeParser()
            result = parser.parse_resume(tmp_path, quick_mode=True, skip_cache=True)

            formatted_html = result.get("formatted_html", "")
            if formatted_html and len(formatted_html.strip()) > 50:
                return formatted_html

            raw_text = result.get("raw_text", "")
            if not raw_text or len(raw_text.strip()) < 50:
                return raw_text

            return self._plain_text_to_html(raw_text)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _plain_text_to_html(self, text):
        import html as html_lib
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        parts = []
        for para in paragraphs:
            escaped = html_lib.escape(para)
            escaped = escaped.replace('\n', '<br>')
            parts.append(f"<p>{escaped}</p>")
        return "\n".join(parts)

    def _builtin_email_extractor(self, params):
        import re as _re

        dry_run = params.get("dry_run", True)
        if isinstance(dry_run, str):
            dry_run = dry_run.lower() not in ('false', '0', 'no')
        days_back = int(params.get("days_back", 365))
        limit = int(params.get("limit", 50))
        candidate_ids_raw = params.get("candidate_ids", "")

        EMAIL_RE = _re.compile(
            r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b',
            _re.IGNORECASE
        )

        JUNK_DOMAINS = {
            "example.com", "test.com", "email.com", "youremail.com",
            "company.com", "domain.com", "placeholder.com",
        }

        def _is_valid_email(email_str):
            if not email_str or len(email_str) < 5 or len(email_str) > 254:
                return False
            local, _, domain = email_str.partition("@")
            if not domain or domain.lower() in JUNK_DOMAINS:
                return False
            if local.startswith(".") or local.endswith(".") or ".." in local:
                return False
            return True

        specific_ids = []
        if candidate_ids_raw:
            if isinstance(candidate_ids_raw, str):
                specific_ids = [int(x.strip()) for x in candidate_ids_raw.split(",") if x.strip().isdigit()]
            elif isinstance(candidate_ids_raw, (list, tuple)):
                specific_ids = [int(x) for x in candidate_ids_raw if str(x).strip().isdigit()]

        if specific_ids:
            candidates = []
            search_url = f"{self._bh_url()}search/Candidate"
            chunk_size = 100
            for i in range(0, len(specific_ids), chunk_size):
                chunk = specific_ids[i:i + chunk_size]
                id_clause = " OR ".join(str(c) for c in chunk)
                try:
                    resp = requests.get(
                        search_url,
                        headers=self._bh_headers(),
                        params={
                            "query": f"id:({id_clause})",
                            "fields": "id,firstName,lastName,email,dateAdded",
                            "count": chunk_size,
                            "sort": "id",
                        },
                        timeout=15
                    )
                    resp.raise_for_status()
                    candidates.extend(resp.json().get("data", []))
                except Exception as e:
                    self.logger.warning(f"email_extractor: could not fetch candidate batch {chunk}: {e}")
            total_available = len(candidates)
        else:
            cutoff_ts = int((datetime.utcnow() - timedelta(days=days_back)).timestamp() * 1000)
            url = f"{self._bh_url()}search/Candidate"
            p = {
                "query": f"dateAdded:[{cutoff_ts} TO *] AND -email:[* TO *]",
                "fields": "id,firstName,lastName,email,dateAdded",
                "count": min(limit, 500),
                "sort": "-dateAdded"
            }
            resp = requests.get(url, headers=self._bh_headers(), params=p, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            total_available = data.get("total", 0)
            candidates = data.get("data", [])

        also_empty = []
        if not specific_ids and len(candidates) < limit:
            empty_url = f"{self._bh_url()}search/Candidate"
            empty_p = {
                "query": f'dateAdded:[{cutoff_ts} TO *] AND email:""',
                "fields": "id,firstName,lastName,email,dateAdded",
                "count": min(limit - len(candidates), 500),
                "sort": "-dateAdded"
            }
            try:
                empty_resp = requests.get(empty_url, headers=self._bh_headers(), params=empty_p, timeout=30)
                empty_resp.raise_for_status()
                empty_data = empty_resp.json()
                total_available += empty_data.get("total", 0)
                existing_ids = {c["id"] for c in candidates}
                also_empty = [c for c in empty_data.get("data", []) if c["id"] not in existing_ids]
                candidates.extend(also_empty[:limit - len(candidates)])
            except Exception:
                pass

        results = {
            "total_without_email": total_available,
            "candidates_in_batch": len(candidates),
            "with_resume": 0,
            "no_file": 0,
            "emails_found": 0,
            "emails_updated": 0,
            "no_email_in_resume": 0,
            "failed": 0,
        }
        candidate_details = []
        updated_samples = []

        for cand in candidates:
            cid = cand.get("id")
            name = f"{cand.get('firstName', '')} {cand.get('lastName', '')}".strip()

            file_url = f"{self._bh_url()}entity/Candidate/{cid}/fileAttachments"
            try:
                file_resp = requests.get(file_url, headers=self._bh_headers(),
                                         params={"fields": "id,name,type,contentType"}, timeout=15)
                file_resp.raise_for_status()
                files = file_resp.json().get("data", [])
                resume_files = [f for f in files if
                                (f.get("type", "").lower() == "resume") or
                                f.get("name", "").lower().endswith((".pdf", ".doc", ".docx"))]
            except Exception:
                resume_files = []

            if not resume_files:
                results["no_file"] += 1
                candidate_details.append({
                    "candidate_id": cid, "name": name,
                    "status": "no_resume_file"
                })
                continue

            results["with_resume"] += 1
            resume_file = resume_files[0]

            if dry_run:
                candidate_details.append({
                    "candidate_id": cid, "name": name,
                    "resume_file": resume_file.get("name", "unknown"),
                    "status": "would_process"
                })
                continue

            try:
                text = self._download_and_extract_text(cid, resume_file)
                if not text or len(text.strip()) < 10:
                    results["failed"] += 1
                    candidate_details.append({
                        "candidate_id": cid, "name": name,
                        "status": "parse_failed"
                    })
                    continue

                emails_found = EMAIL_RE.findall(text)
                cleaned_emails = []
                for raw_email in emails_found:
                    cleaned = raw_email.strip()
                    cleaned = cleaned.lstrip("(<[")
                    cleaned = cleaned.rstrip(")>].,;:!?\"'")
                    if cleaned:
                        cleaned_emails.append(cleaned)
                valid_emails = [e.lower() for e in cleaned_emails if _is_valid_email(e)]

                seen = set()
                unique_emails = []
                for e in valid_emails:
                    if e not in seen:
                        seen.add(e)
                        unique_emails.append(e)

                if not unique_emails:
                    results["no_email_in_resume"] += 1
                    candidate_details.append({
                        "candidate_id": cid, "name": name,
                        "resume_file": resume_file.get("name", ""),
                        "status": "no_email_in_resume"
                    })
                    continue

                results["emails_found"] += 1
                chosen_email = unique_emails[0]

                update_url = f"{self._bh_url()}entity/Candidate/{cid}"
                upd_resp = requests.post(
                    update_url,
                    headers={**self._bh_headers(), "Content-Type": "application/json"},
                    json={"email": chosen_email},
                    timeout=15
                )
                upd_body = upd_resp.json() if upd_resp.status_code in (200, 201) else {}
                if upd_body.get("changeType") == "UPDATE" or upd_body.get("changedEntityId"):
                    results["emails_updated"] += 1
                    detail = {
                        "candidate_id": cid, "name": name,
                        "email_extracted": chosen_email,
                        "status": "updated"
                    }
                    if len(unique_emails) > 1:
                        detail["other_emails_found"] = unique_emails[1:4]
                    candidate_details.append(detail)
                    if len(updated_samples) < 10:
                        updated_samples.append({"id": cid, "name": name, "email": chosen_email})
                else:
                    results["failed"] += 1
                    candidate_details.append({
                        "candidate_id": cid, "name": name,
                        "email_extracted": chosen_email,
                        "status": "update_failed",
                        "response": str(upd_body)[:200]
                    })
            except Exception as e:
                results["failed"] += 1
                candidate_details.append({
                    "candidate_id": cid, "name": name,
                    "status": "error",
                    "error": str(e)[:150]
                })

            time.sleep(0.1)

        if dry_run:
            summary = (
                f"DRY RUN: {results['total_without_email']:,} candidates have no email address "
                f"(past {days_back} days). This batch covers {results['candidates_in_batch']}. "
                f"{results['with_resume']} have resume files, {results['no_file']} have no files attached. "
                f"Re-run with dry_run=false to extract and update emails."
            )
        else:
            summary = (
                f"Processed {results['candidates_in_batch']} candidates: "
                f"{results['emails_found']} emails extracted from resumes, "
                f"{results['emails_updated']} successfully updated in Bullhorn. "
                f"{results['no_email_in_resume']} resumes had no email, "
                f"{results['no_file']} had no resume file, "
                f"{results['failed']} failed."
            )

        return {
            "summary": summary,
            "dry_run": dry_run,
            **results,
            "updated_samples": updated_samples,
            "candidates": candidate_details[:50],
        }

    def _builtin_occupation_extractor(self, params):
        dry_run = params.get("dry_run", True)
        if isinstance(dry_run, str):
            dry_run = dry_run.lower() not in ('false', '0', 'no')
        days_back = int(params.get("days_back", 30))
        limit = int(params.get("limit", 50))

        cutoff_ts = int((datetime.utcnow() - timedelta(days=days_back)).timestamp() * 1000)
        search_url = f"{self._bh_url()}search/Candidate"
        fields = "id,firstName,lastName,email,occupation,dateAdded,owner"
        batch_count = min(limit, 500)
        candidates = []
        total_available = 0
        seen_ids = set()

        for query_str in [
            f"dateAdded:[{cutoff_ts} TO *] AND -occupation:[* TO *]",
            f'dateAdded:[{cutoff_ts} TO *] AND occupation:""',
        ]:
            try:
                resp = requests.get(search_url, headers=self._bh_headers(), params={
                    "query": query_str,
                    "fields": fields,
                    "count": batch_count,
                    "sort": "-dateAdded"
                }, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                total_available += data.get("total", 0)
                for c in data.get("data", []):
                    if c.get("id") not in seen_ids:
                        seen_ids.add(c["id"])
                        candidates.append(c)
            except Exception as e:
                self.logger.error(f"occupation_extractor: search failed ({query_str[:40]}): {e}")

        if not candidates and total_available == 0:
            return {"updated": 0, "total_without_occupation": 0, "summary": "No candidates with missing occupation found"}

        results = {
            "total_without_occupation": total_available,
            "candidates_in_batch": len(candidates),
            "with_resume": 0,
            "no_file": 0,
            "titles_found": 0,
            "updated": 0,
            "no_title_in_resume": 0,
            "failed": 0,
        }
        candidate_details = []
        updated_samples = []

        for cand in candidates:
            cid = cand.get("id")
            name = f"{cand.get('firstName', '')} {cand.get('lastName', '')}".strip()

            file_url = f"{self._bh_url()}entity/Candidate/{cid}/fileAttachments"
            try:
                file_resp = requests.get(file_url, headers=self._bh_headers(),
                                         params={"fields": "id,name,type,contentType"}, timeout=15)
                file_resp.raise_for_status()
                files = file_resp.json().get("data", [])
                resume_files = [f for f in files if
                                (f.get("type", "").lower() == "resume") or
                                f.get("name", "").lower().endswith((".pdf", ".doc", ".docx"))]
            except Exception:
                resume_files = []

            if not resume_files:
                results["no_file"] += 1
                candidate_details.append({
                    "candidate_id": cid, "name": name,
                    "status": "no_resume_file"
                })
                continue

            results["with_resume"] += 1
            resume_file = resume_files[0]

            if dry_run:
                candidate_details.append({
                    "candidate_id": cid, "name": name,
                    "resume_file": resume_file.get("name", "unknown"),
                    "status": "would_process"
                })
                continue

            try:
                text = self._download_and_extract_resume_raw_text(cid, resume_file)
                if not text or len(text.strip()) < 50:
                    results["failed"] += 1
                    candidate_details.append({
                        "candidate_id": cid, "name": name,
                        "status": "parse_failed"
                    })
                    continue

                title = self._extract_title_from_resume_text(text, name)

                if not title:
                    results["no_title_in_resume"] += 1
                    candidate_details.append({
                        "candidate_id": cid, "name": name,
                        "resume_file": resume_file.get("name", ""),
                        "status": "no_title_in_resume"
                    })
                    continue

                results["titles_found"] += 1

                update_url = f"{self._bh_url()}entity/Candidate/{cid}"
                upd_resp = requests.post(
                    update_url,
                    headers={**self._bh_headers(), "Content-Type": "application/json"},
                    json={"occupation": title},
                    timeout=15
                )
                upd_body = upd_resp.json() if upd_resp.status_code in (200, 201) else {}
                if upd_body.get("changeType") == "UPDATE" or upd_body.get("changedEntityId"):
                    results["updated"] += 1
                    detail = {
                        "candidate_id": cid, "name": name,
                        "title_extracted": title,
                        "status": "updated"
                    }
                    candidate_details.append(detail)
                    if len(updated_samples) < 10:
                        updated_samples.append({"id": cid, "name": name, "occupation": title})
                    self.logger.info(f"occupation_extractor: updated {cid} ({name}) -> '{title}'")
                else:
                    results["failed"] += 1
                    candidate_details.append({
                        "candidate_id": cid, "name": name,
                        "title_extracted": title,
                        "status": "update_failed",
                        "response": str(upd_body)[:200]
                    })
            except Exception as e:
                results["failed"] += 1
                candidate_details.append({
                    "candidate_id": cid, "name": name,
                    "status": "error",
                    "error": str(e)[:150]
                })

            time.sleep(0.2)

        if dry_run:
            summary = (
                f"DRY RUN: {results['total_without_occupation']:,} candidates have no occupation/title "
                f"(past {days_back} days). This batch covers {results['candidates_in_batch']}. "
                f"{results['with_resume']} have resume files, {results['no_file']} have no files attached."
            )
        else:
            summary = (
                f"Processed {results['candidates_in_batch']} candidates: "
                f"{results['titles_found']} titles extracted, "
                f"{results['updated']} updated in Bullhorn. "
                f"{results['no_title_in_resume']} resumes had no extractable title, "
                f"{results['no_file']} had no resume file, "
                f"{results['failed']} failed."
            )

        return {
            "summary": summary,
            "dry_run": dry_run,
            **results,
            "updated_samples": updated_samples,
            "candidates": candidate_details[:50],
        }

    def _download_and_extract_resume_raw_text(self, candidate_id, resume_file_info):
        import base64
        import tempfile
        import os

        file_id = resume_file_info.get("id")
        filename = resume_file_info.get("name", "resume.pdf")

        dl_url = f"{self._bh_url()}file/Candidate/{candidate_id}/{file_id}"
        dl_resp = requests.get(dl_url, headers=self._bh_headers(), timeout=30)
        dl_resp.raise_for_status()
        file_data = dl_resp.json()
        file_content = file_data.get("File", {}).get("fileContent", "")

        if not file_content:
            return None

        raw_bytes = base64.b64decode(file_content)
        lower_name = filename.lower()
        if lower_name.endswith(".pdf"):
            suffix = ".pdf"
        elif lower_name.endswith(".docx"):
            suffix = ".docx"
        elif lower_name.endswith(".doc"):
            suffix = ".doc"
        else:
            suffix = ".pdf"

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(raw_bytes)
                tmp_path = tmp.name

            from resume_parser import ResumeParser
            parser = ResumeParser()
            result = parser.parse_resume(tmp_path, quick_mode=True, skip_cache=True)
            return result.get("raw_text", "")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _extract_title_from_resume_text(self, resume_text, candidate_name=""):
        try:
            from openai import OpenAI
            client = OpenAI()

            prompt = (
                f"Extract the most recent/current job title from this resume. "
                f"Return ONLY the job title as a short string (e.g. 'Senior Data Engineer', 'Software Developer', 'Project Manager'). "
                f"If you cannot determine a clear job title, return exactly: NONE\n\n"
                f"Resume text:\n{resume_text[:6000]}"
            )

            response = client.chat.completions.create(
                model="gpt-5.4",
                messages=[
                    {"role": "system", "content": "You extract job titles from resumes. Return only the job title, nothing else."},
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=500
            )

            title = response.choices[0].message.content.strip()
            title = title.strip('"\'')

            if not title or title.upper() == "NONE" or len(title) > 100 or len(title) < 2:
                return None

            return title
        except Exception as e:
            self.logger.warning(f"occupation_extractor: AI title extraction failed: {e}")
            return None

