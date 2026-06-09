"""Tests for the resume_freshness_sync built-in.

The built-in re-parses the candidate resume field (Bullhorn `description`)
from the MOST RECENT resume file in the Files tab, but only when that file
was added after the candidate record was last modified (preserving manual
edits and skipping already-current records).

These tests exercise the pure logic by stubbing the Bullhorn HTTP layer.
"""
import types
from unittest.mock import patch

from automation_service.resume_mixin import ResumeMixin


HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeBullhorn:
    """Routes GET/POST by URL against an in-memory candidate/file fixture."""

    def __init__(self, candidates, files_by_cid):
        # candidates: list of dicts (search results)
        # files_by_cid: {cid: [file dicts]}
        self.candidates = candidates
        self.files_by_cid = files_by_cid
        self.posts = []  # records (url, json_body)

    def get(self, url, headers=None, params=None, timeout=None):
        params = params or {}
        if url.endswith("search/Candidate"):
            query = params.get("query", "")
            if query.startswith("id:("):
                wanted = {
                    int(x) for x in query[len("id:("):-1].replace(" OR ", " ").split()
                    if x.isdigit()
                }
                data = [c for c in self.candidates if c["id"] in wanted]
                return _Resp({"data": data, "total": len(data)})
            # base scan: id:[1 TO *] with start/count
            start = int(params.get("start", 0))
            count = int(params.get("count", 500))
            ordered = sorted(self.candidates, key=lambda c: c["id"])
            page = ordered[start:start + count]
            return _Resp({"data": page, "total": len(ordered)})
        if "/fileAttachments" in url:
            cid = int(url.split("/Candidate/")[1].split("/")[0])
            return _Resp({"data": self.files_by_cid.get(cid, [])})
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append((url, json))
        return _Resp({"changeType": "UPDATE", "changedEntityId": 1}, status=200)


def _make_service(candidates, files_by_cid, extract_return="<p>NEW RESUME</p>"):
    svc = ResumeMixin()
    svc._bh_url = lambda: "https://bh.example.com/rest/"
    svc._bh_headers = lambda: {"Authorization": "x"}
    svc.logger = types.SimpleNamespace(
        warning=lambda *a, **k: None,
        info=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )
    svc._extract_calls = []

    def _fake_extract(cid, file_info, quick_mode=True):
        svc._extract_calls.append((cid, file_info.get("id"), quick_mode))
        return extract_return

    svc._download_and_extract_text = _fake_extract
    fake = _FakeBullhorn(candidates, files_by_cid)
    return svc, fake


def test_picks_most_recent_file_and_flags_stale():
    """Newest file added well after last-modified -> flagged stale (dry run)."""
    candidates = [{"id": 1, "firstName": "A", "lastName": "B",
                   "email": "a@b.com", "dateLastModified": 1_000_000}]
    files_by_cid = {1: [
        {"id": 10, "name": "old.pdf", "type": "Resume", "dateAdded": 900_000},
        {"id": 11, "name": "new.pdf", "type": "Resume", "dateAdded": 1_000_000 + 2 * DAY_MS},
    ]}
    svc, fake = _make_service(candidates, files_by_cid)
    with patch("automation_service.resume_mixin.requests", fake):
        res = svc._builtin_resume_freshness_sync(
            {"dry_run": True, "candidate_ids": "1"})

    assert res["stale_found"] == 1
    assert res["with_resume"] == 1
    assert res["updated"] == 0  # dry run never writes
    assert fake.posts == []
    detail = res["candidates"][0]
    assert detail["newest_file"] == "new.pdf"  # newest, not first
    assert detail["status"] == "would_update"


def test_skips_when_file_older_than_last_modified():
    """A manual edit (last-modified after the newest file) is preserved."""
    candidates = [{"id": 2, "firstName": "C", "lastName": "D",
                   "dateLastModified": 5_000_000}]
    files_by_cid = {2: [
        {"id": 20, "name": "resume.pdf", "type": "Resume", "dateAdded": 4_000_000},
    ]}
    svc, fake = _make_service(candidates, files_by_cid)
    with patch("automation_service.resume_mixin.requests", fake):
        res = svc._builtin_resume_freshness_sync(
            {"dry_run": True, "candidate_ids": "2"})

    assert res["stale_found"] == 0
    assert res["skipped_current"] == 1
    assert fake.posts == []


def test_min_gap_filters_near_simultaneous_files():
    """A file only minutes newer than the record is treated as current."""
    candidates = [{"id": 3, "firstName": "E", "lastName": "F",
                   "dateLastModified": 10_000_000}]
    files_by_cid = {3: [
        # 10 minutes newer — under the default 60-minute gap
        {"id": 30, "name": "resume.pdf", "type": "Resume",
         "dateAdded": 10_000_000 + 10 * 60 * 1000},
    ]}
    svc, fake = _make_service(candidates, files_by_cid)
    with patch("automation_service.resume_mixin.requests", fake):
        res = svc._builtin_resume_freshness_sync(
            {"dry_run": True, "candidate_ids": "3", "min_gap_minutes": 60})

    assert res["stale_found"] == 0
    assert res["skipped_current"] == 1


def test_no_resume_file_is_skipped():
    candidates = [{"id": 4, "firstName": "G", "lastName": "H",
                   "dateLastModified": 1_000_000}]
    files_by_cid = {4: [
        {"id": 40, "name": "cover_letter.txt", "type": "Cover Letter", "dateAdded": 9_000_000},
    ]}
    svc, fake = _make_service(candidates, files_by_cid)
    with patch("automation_service.resume_mixin.requests", fake):
        res = svc._builtin_resume_freshness_sync(
            {"dry_run": True, "candidate_ids": "4"})

    assert res["no_file"] == 1
    assert res["with_resume"] == 0
    assert res["stale_found"] == 0


def test_real_run_writes_description_from_newest_file():
    candidates = [{"id": 5, "firstName": "I", "lastName": "J",
                   "dateLastModified": 1_000_000}]
    files_by_cid = {5: [
        {"id": 50, "name": "old.docx", "type": "Resume", "dateAdded": 900_000},
        {"id": 51, "name": "latest.pdf", "type": "Resume", "dateAdded": 1_000_000 + 3 * DAY_MS},
    ]}
    latest_html = "<h2>Experience</h2><p>" + ("Senior engineer with deep experience. " * 5) + "</p>"
    svc, fake = _make_service(candidates, files_by_cid, extract_return=latest_html)
    with patch("automation_service.resume_mixin.requests", fake):
        res = svc._builtin_resume_freshness_sync(
            {"dry_run": False, "candidate_ids": "5"})

    assert res["updated"] == 1
    assert res["failed"] == 0
    # Parsed the NEWEST file (id 51) with AI formatting on (quick_mode=False)
    assert svc._extract_calls == [(5, 51, False)]
    assert len(fake.posts) == 1
    url, body = fake.posts[0]
    assert url.endswith("entity/Candidate/5")
    assert body["description"] == latest_html


def test_base_scan_reports_next_start():
    candidates = [
        {"id": i, "firstName": "x", "lastName": "y", "dateLastModified": 1_000_000}
        for i in range(1, 4)
    ]
    files_by_cid = {i: [] for i in range(1, 4)}
    svc, fake = _make_service(candidates, files_by_cid)
    with patch("automation_service.resume_mixin.requests", fake):
        res = svc._builtin_resume_freshness_sync(
            {"dry_run": True, "start": 0, "limit": 2})

    assert res["candidates_scanned"] == 2
    assert res["next_start"] == 2
    assert res["total_candidates"] == 3


def test_missing_last_modified_is_skipped_conservatively():
    candidates = [{"id": 6, "firstName": "K", "lastName": "L"}]  # no dateLastModified
    files_by_cid = {6: [
        {"id": 60, "name": "resume.pdf", "type": "Resume", "dateAdded": 9_000_000},
    ]}
    svc, fake = _make_service(candidates, files_by_cid)
    with patch("automation_service.resume_mixin.requests", fake):
        res = svc._builtin_resume_freshness_sync(
            {"dry_run": True, "candidate_ids": "6"})

    assert res["stale_found"] == 0
    assert res["skipped_current"] == 1
