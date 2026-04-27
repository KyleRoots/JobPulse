"""AI Fuzzy Duplicate Matcher for Candidates (Task #57)

Catches duplicates the exact-field matcher in ``duplicate_merge_service``
cannot: candidates who re-apply with both a NEW email AND a NEW phone.

Architecture (mirrors the existing two-layer pattern in ``embedding_service``):

  Layer A: Embedding pre-filter
    - Build a normalised profile string per candidate
      (name + work history + skills + location + education).
    - Generate a ``text-embedding-3-large`` vector.
    - Cache the vector in ``candidate_profile_embedding`` keyed by
      candidate id, with a SHA-256 ``profile_hash`` so we can detect
      profile changes and refresh just those rows.
    - For a target candidate, cosine-compare its vector against the cached
      pool (most-recent N to bound work) and keep the top-N above a
      coarse cosine threshold.

  Layer B: GPT-5.4 final scoring
    - For each top-N candidate, ask GPT-5.4 to compare the two profile
      texts and return JSON ``{"confidence": float, "reasoning": str}``.
    - Only pairs scoring at or above ``AI_CONFIDENCE_THRESHOLD`` (0.90)
      are returned for merging — calibrated higher than the exact-match
      path since AI inference carries more uncertainty.

This module is a *supplement* to the exact-match engine, never a
replacement. It is invoked from ``DuplicateMergeService.run_scheduled_check``
after the exact pass has run, with a per-cycle budget so the existing
hourly job window is preserved.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Coarse cosine threshold for the embedding pre-filter (Layer A).
# Pairs below this never reach the expensive GPT call.
PRE_FILTER_COSINE_THRESHOLD = 0.75

# Number of top candidates per target to send to Layer B.
PRE_FILTER_TOP_N = 5

# Final AI confidence threshold for merging (Layer B). Calibrated higher
# than the exact-match path's 0.80 because AI inference is less certain.
AI_CONFIDENCE_THRESHOLD = 0.90

# Budget caps so we never blow the hourly scheduled-job window.
# Long-tail candidates simply ride along to the next cycle.
MAX_CANDIDATES_PER_CYCLE = 25
EMBEDDING_CACHE_SCAN_LIMIT = 2000  # most-recently-updated cache rows scanned

# Rolling backfill cap per cycle. Each scheduled run embeds up to this many
# historical (uncached) candidates so the cosine pre-filter pool grows
# beyond just newly-modified records — required to catch re-applicants
# whose original profile was created months/years ago.
BACKFILL_PER_CYCLE = 50
BACKFILL_PAGE_SIZE = 200
BACKFILL_MAX_PAGES = 5


def _normalise(text: Optional[str]) -> str:
    if not text:
        return ''
    return re.sub(r'\s+', ' ', str(text).strip().lower())


def _format_year(epoch_ms) -> str:
    if not epoch_ms:
        return ''
    try:
        return datetime.utcfromtimestamp(int(epoch_ms) / 1000).strftime('%Y')
    except (TypeError, ValueError):
        return ''


class FuzzyDuplicateMatcher:
    """Embedding + GPT-5.4 layer that supplements the exact-match dedup engine."""

    def __init__(
        self,
        bullhorn_service,
        embedding_service=None,
        openai_client=None,
        model_chat: str = 'gpt-5.4',
        ai_confidence_threshold: float = AI_CONFIDENCE_THRESHOLD,
        pre_filter_cosine_threshold: float = PRE_FILTER_COSINE_THRESHOLD,
        pre_filter_top_n: int = PRE_FILTER_TOP_N,
        cache_scan_limit: int = EMBEDDING_CACHE_SCAN_LIMIT,
        backfill_per_cycle: int = BACKFILL_PER_CYCLE,
        backfill_page_size: int = BACKFILL_PAGE_SIZE,
        backfill_max_pages: int = BACKFILL_MAX_PAGES,
    ):
        self.bh = bullhorn_service
        self._embedding_service = embedding_service
        self._openai_client = openai_client
        self.model_chat = model_chat
        self.ai_confidence_threshold = ai_confidence_threshold
        self.pre_filter_cosine_threshold = pre_filter_cosine_threshold
        self.pre_filter_top_n = pre_filter_top_n
        self.cache_scan_limit = cache_scan_limit
        self.backfill_per_cycle = backfill_per_cycle
        self.backfill_page_size = backfill_page_size
        self.backfill_max_pages = backfill_max_pages

    # ── Lazy imports keep this importable in tests w/o app context ────────

    @property
    def embedding_service(self):
        if self._embedding_service is None:
            from embedding_service import EmbeddingService
            self._embedding_service = EmbeddingService()
        return self._embedding_service

    @property
    def openai_client(self):
        if self._openai_client is None:
            from openai import OpenAI
            api_key = os.environ.get('OPENAI_API_KEY')
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set; cannot run fuzzy AI matcher")
            self._openai_client = OpenAI(api_key=api_key)
        return self._openai_client

    # ── Profile text builder ────────────────────────────────────────────

    def build_profile_text(
        self,
        candidate: Dict,
        work_history: Optional[List[Dict]] = None,
        education: Optional[List[Dict]] = None,
    ) -> str:
        """Compose the deterministic profile string used for embedding.

        Includes name + work history + skills + location + education
        (per the Task #57 spec). Stable ordering is critical so that the
        SHA-256 hash only changes when the underlying data does.
        """
        first = (candidate.get('firstName') or '').strip()
        last = (candidate.get('lastName') or '').strip()
        name = f"{first} {last}".strip()

        addr = candidate.get('address') or {}
        if isinstance(addr, dict):
            location_parts = [
                addr.get('city') or '',
                addr.get('state') or '',
                addr.get('countryName') or addr.get('countryID') or '',
                addr.get('zip') or '',
            ]
        else:
            location_parts = []
        location = ', '.join([p for p in (str(x).strip() for x in location_parts) if p])

        skills_raw = candidate.get('skillSet') or candidate.get('skills') or ''
        if isinstance(skills_raw, list):
            skills = ', '.join(str(s) for s in skills_raw if s)
        else:
            skills = str(skills_raw or '').strip()

        occupation = (candidate.get('occupation') or '').strip()
        company = (candidate.get('companyName') or '').strip()

        # Work history rows (sorted oldest-first by start year for stability)
        wh_rows: List[str] = []
        for wh in sorted(
            work_history or [],
            key=lambda w: int(w.get('startDate') or 0),
        ):
            title = (wh.get('title') or '').strip()
            wh_company = (wh.get('companyName') or '').strip()
            start_y = _format_year(wh.get('startDate'))
            end_y = _format_year(wh.get('endDate')) or ('present' if wh.get('isLastJob') else '')
            row = f"  - {title} @ {wh_company} ({start_y}–{end_y})".rstrip()
            if title or wh_company or start_y:
                wh_rows.append(row)

        # Education rows (sorted oldest-first by graduation date)
        edu_rows: List[str] = []
        for edu in sorted(
            education or [],
            key=lambda e: int(e.get('graduationDate') or 0),
        ):
            degree = (edu.get('degree') or '').strip()
            major = (edu.get('major') or '').strip()
            school = (edu.get('school') or '').strip()
            year = _format_year(edu.get('graduationDate'))
            row = f"  - {degree} {major} @ {school} ({year})".strip()
            if degree or major or school:
                edu_rows.append(row)

        sections: List[str] = []
        sections.append(f"NAME: {name}")
        if location:
            sections.append(f"LOCATION: {location}")
        if occupation or company:
            current_role = ' @ '.join([p for p in (occupation, company) if p])
            sections.append(f"CURRENT ROLE: {current_role}")
        if skills:
            sections.append(f"SKILLS: {skills}")
        if wh_rows:
            sections.append("WORK HISTORY:\n" + "\n".join(wh_rows))
        if edu_rows:
            sections.append("EDUCATION:\n" + "\n".join(edu_rows))

        return "\n".join(sections).strip()

    @staticmethod
    def compute_profile_hash(profile_text: str) -> str:
        normalised = _normalise(profile_text)
        return hashlib.sha256(normalised.encode('utf-8')).hexdigest()

    # ── Bullhorn fetch helpers ──────────────────────────────────────────

    @staticmethod
    def _is_archived_status(status) -> bool:
        """Mirror the exact-match path's ``-status:Archive`` filter so the
        fuzzy pass never proposes merging into a stale archived record."""
        if not status:
            return False
        return str(status).strip().lower() == 'archive'

    def _fetch_full_candidate(self, candidate_id: int) -> Optional[Dict]:
        """Fetch a candidate with all fields needed for the profile text."""
        try:
            url = f"{self.bh.base_url}entity/Candidate/{candidate_id}"
            params = {
                'fields': (
                    'id,firstName,lastName,email,email2,email3,phone,mobile,'
                    'occupation,companyName,skillSet,status,'
                    'address(address1,city,state,zip,countryName,countryID)'
                ),
                'BhRestToken': self.bh.rest_token,
            }
            resp = self.bh.session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json().get('data', {}) or {}
        except Exception as e:
            logger.warning(f"FuzzyMatcher: failed to fetch candidate {candidate_id}: {e}")
        return None

    def _fetch_work_history(self, candidate_id: int) -> List[Dict]:
        try:
            url = f"{self.bh.base_url}query/CandidateWorkHistory"
            params = {
                'where': f"candidate.id={candidate_id}",
                'fields': 'id,companyName,title,startDate,endDate,isLastJob',
                'count': 100,
                'BhRestToken': self.bh.rest_token,
            }
            resp = self.bh.session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json().get('data', []) or []
        except Exception as e:
            logger.warning(f"FuzzyMatcher: failed to fetch work history for {candidate_id}: {e}")
        return []

    def _fetch_education(self, candidate_id: int) -> List[Dict]:
        try:
            url = f"{self.bh.base_url}query/CandidateEducation"
            params = {
                'where': f"candidate.id={candidate_id}",
                'fields': 'id,school,degree,major,graduationDate',
                'count': 50,
                'BhRestToken': self.bh.rest_token,
            }
            resp = self.bh.session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json().get('data', []) or []
        except Exception as e:
            logger.warning(f"FuzzyMatcher: failed to fetch education for {candidate_id}: {e}")
        return []

    # ── Embedding cache I/O ─────────────────────────────────────────────

    def get_or_create_profile_embedding(
        self,
        candidate: Dict,
    ) -> Tuple[Optional[List[float]], Optional[str]]:
        """Return (embedding_vector, profile_text) for the candidate.

        Uses the ``candidate_profile_embedding`` cache; only re-embeds if
        the deterministic profile hash has changed.
        """
        from models import CandidateProfileEmbedding
        from extensions import db

        cid = candidate.get('id')
        if not cid:
            return None, None

        # Build profile text (fetch work history + education on demand)
        full = self._fetch_full_candidate(cid) or candidate
        wh = self._fetch_work_history(cid)
        edu = self._fetch_education(cid)
        profile_text = self.build_profile_text(full, wh, edu)
        if not profile_text:
            return None, None

        profile_hash = self.compute_profile_hash(profile_text)

        try:
            cached = CandidateProfileEmbedding.query.filter_by(
                bullhorn_candidate_id=cid
            ).first()

            if cached and cached.profile_hash == profile_hash and cached.embedding_vector:
                try:
                    return json.loads(cached.embedding_vector), profile_text
                except (TypeError, ValueError):
                    pass  # fall through to regenerate

            vector = self.embedding_service.generate_embedding(profile_text)
            if not vector:
                return None, profile_text

            vector_json = json.dumps(vector)
            name = f"{(full.get('firstName') or '').strip()} {(full.get('lastName') or '').strip()}".strip()

            if cached:
                cached.profile_hash = profile_hash
                cached.embedding_vector = vector_json
                cached.candidate_name = name
                cached.embedding_model = self.embedding_service.embedding_model
                cached.profile_text_snippet = profile_text[:1000]
                cached.updated_at = datetime.utcnow()
                logger.debug(f"FuzzyMatcher: refreshed profile embedding for candidate {cid}")
            else:
                db.session.add(CandidateProfileEmbedding(
                    bullhorn_candidate_id=cid,
                    candidate_name=name,
                    profile_hash=profile_hash,
                    embedding_vector=vector_json,
                    embedding_model=self.embedding_service.embedding_model,
                    profile_text_snippet=profile_text[:1000],
                ))
                logger.debug(f"FuzzyMatcher: cached new profile embedding for candidate {cid}")

            db.session.commit()
            return vector, profile_text

        except Exception as e:
            logger.error(f"FuzzyMatcher: cache I/O failed for candidate {cid}: {e}")
            try:
                db.session.rollback()
            except Exception:
                pass
            # Fall through — return live embedding without caching
            vector = self.embedding_service.generate_embedding(profile_text)
            return vector, profile_text

    # ── Rolling backfill (cold-start coverage) ──────────────────────────

    # Persistent cursor key in GlobalSettings — stores the highest Bullhorn
    # candidate id we've already considered so the next cycle resumes from
    # there. Without persistence we'd re-scan the same first ~1000 IDs on
    # every cycle and never reach the long tail.
    BACKFILL_CURSOR_KEY = 'fuzzy_dedup_backfill_cursor_id'

    def _read_backfill_cursor(self) -> int:
        try:
            from models import GlobalSettings
            raw = GlobalSettings.get_value(self.BACKFILL_CURSOR_KEY, default='0')
            return int(raw or 0)
        except Exception as e:
            logger.warning(f"FuzzyMatcher: backfill cursor read failed: {e}")
            return 0

    def _write_backfill_cursor(self, value: int) -> None:
        try:
            from models import GlobalSettings
            GlobalSettings.set_value(
                self.BACKFILL_CURSOR_KEY,
                str(int(value)),
                description='AI fuzzy dedup: highest Bullhorn candidate id seen by rolling backfill',
                category='dedup',
            )
        except Exception as e:
            logger.warning(f"FuzzyMatcher: backfill cursor write failed: {e}")

    def _fetch_candidate_page_after(self, after_id: int, count: int) -> List[Dict]:
        """Fetch the next page of active Bullhorn candidates with id > ``after_id``,
        sorted by id ascending. This lets us advance through the historical
        corpus deterministically across cycles via the persistent cursor.
        """
        try:
            url = f"{self.bh.base_url}search/Candidate"
            params = {
                'query': f'isDeleted:0 AND -status:Archive AND id:[{int(after_id) + 1} TO *]',
                'fields': 'id,firstName,lastName,email,email2,email3,phone,mobile,occupation,companyName,skillSet,address',
                'count': count,
                'start': 0,
                'sort': 'id',
                'BhRestToken': self.bh.rest_token,
            }
            resp = self.bh.session.get(url, params=params, timeout=60)
            if resp.status_code == 200:
                return resp.json().get('data', []) or []
        except Exception as e:
            logger.warning(
                f"FuzzyMatcher: backfill page fetch failed at after_id={after_id}: {e}"
            )
        return []

    def backfill_uncached_candidates(self, limit: Optional[int] = None) -> int:
        """Embed up to ``limit`` historical Bullhorn candidates that are not
        yet present in ``candidate_profile_embedding``.

        This is the cold-start coverage path: without it, the cosine
        pre-filter would only ever see candidates whose embeddings happen
        to have been generated as scheduler "targets" — meaning a re-applicant
        whose original record is months old could never be matched.

        Cursor-driven: each cycle resumes from the highest Bullhorn id we've
        previously inspected (stored in ``GlobalSettings`` under
        ``BACKFILL_CURSOR_KEY``) and walks forward via ``id > cursor``. When
        the corpus is exhausted (a page comes back empty) the cursor wraps
        back to 0 so we periodically re-scan for newly-added candidates that
        slipped between cycles. Per-cycle work is bounded by ``limit`` and
        ``backfill_max_pages`` so the hourly job window holds.

        Returns the number of new embeddings created.
        """
        from models import CandidateProfileEmbedding

        cap = limit if limit is not None else self.backfill_per_cycle
        if cap <= 0:
            return 0

        cursor = self._read_backfill_cursor()
        starting_cursor = cursor
        embedded = 0
        wrapped_already = False

        for _ in range(self.backfill_max_pages):
            if embedded >= cap:
                break
            page = self._fetch_candidate_page_after(cursor, self.backfill_page_size)
            if not page:
                # Exhausted the tail — wrap back to 0 once so we eventually
                # rescan early IDs that may now lack an embedding (e.g. a
                # candidate added with a low recycled id, or a previous
                # transient failure). Stop after one wrap to avoid infinite
                # loops when the corpus is truly empty.
                if wrapped_already or cursor == 0:
                    break
                logger.info(
                    f"🤖 FuzzyMatcher backfill: cursor wrapped from {cursor} -> 0 "
                    "(end of corpus reached)"
                )
                cursor = 0
                wrapped_already = True
                continue

            # Always advance cursor to the max id we just saw, even if every
            # row was already cached — otherwise a cluster of cached rows
            # would stall the cursor forever.
            page_max_id = max((s.get('id') or 0) for s in page)
            if page_max_id > cursor:
                cursor = page_max_id

            # Per-page existence check: only ask the DB about THIS page's
            # IDs (≤ backfill_page_size rows) instead of pre-loading the
            # entire cache. Cost is O(page_size) per cycle regardless of
            # how large the cache grows.
            page_ids = [s.get('id') for s in page if s.get('id')]
            cached_ids: set = set()
            if page_ids:
                try:
                    cached_rows = (
                        CandidateProfileEmbedding.query
                        .with_entities(CandidateProfileEmbedding.bullhorn_candidate_id)
                        .filter(CandidateProfileEmbedding.bullhorn_candidate_id.in_(page_ids))
                        .all()
                    )
                    cached_ids = {row[0] for row in cached_rows}
                except Exception as e:
                    logger.warning(
                        f"FuzzyMatcher: backfill could not check cache for page: {e}"
                    )
                    # Fail safe: skip this page rather than re-embed candidates
                    # we may already have cached.
                    continue

            for stub in page:
                if embedded >= cap:
                    break
                cid = stub.get('id')
                if not cid or cid in cached_ids:
                    continue
                try:
                    vec, _ = self.get_or_create_profile_embedding(stub)
                    if vec:
                        embedded += 1
                except Exception as e:
                    logger.warning(
                        f"FuzzyMatcher: backfill embedding failed for candidate {cid}: {e}"
                    )
                    continue

        # Persist the new cursor so the next scheduled cycle resumes here.
        if cursor != starting_cursor:
            self._write_backfill_cursor(cursor)

        if embedded:
            logger.info(
                f"🤖 FuzzyMatcher backfill: embedded {embedded} historical candidate(s) "
                f"(cursor: {starting_cursor} -> {cursor}, pool now ≈ {len(cached_ids)} cached)"
            )
        return embedded

    # ── Layer A: cosine pre-filter ──────────────────────────────────────

    def find_top_candidates_by_cosine(
        self,
        target_vector: List[float],
        exclude_ids: Iterable[int],
    ) -> List[Tuple[int, float, str, str]]:
        """Return up to N (candidate_id, similarity, name, profile_snippet) tuples.

        Scans the most-recently-updated rows in
        ``candidate_profile_embedding`` (capped by ``cache_scan_limit``) so
        the work per cycle is bounded.
        """
        from models import CandidateProfileEmbedding
        try:
            import numpy as np
        except ImportError:  # pragma: no cover
            np = None  # type: ignore

        excluded = set(exclude_ids)
        results: List[Tuple[int, float, str, str]] = []

        target_arr = None
        if np is not None:
            target_arr = np.asarray(target_vector, dtype=np.float32)
            target_norm = float(np.linalg.norm(target_arr))
            if target_norm == 0:
                return []
        else:
            target_norm = sum(v * v for v in target_vector) ** 0.5
            if target_norm == 0:
                return []

        rows = (
            CandidateProfileEmbedding.query
            .order_by(CandidateProfileEmbedding.updated_at.desc())
            .limit(self.cache_scan_limit)
            .all()
        )

        for row in rows:
            if row.bullhorn_candidate_id in excluded:
                continue
            try:
                vec = json.loads(row.embedding_vector)
            except (TypeError, ValueError):
                continue
            if not vec:
                continue

            if np is not None and target_arr is not None:
                v_arr = np.asarray(vec, dtype=np.float32)
                if v_arr.shape != target_arr.shape:
                    n = min(v_arr.shape[0], target_arr.shape[0])
                    v_arr = v_arr[:n]
                    t_arr = target_arr[:n]
                else:
                    t_arr = target_arr
                v_norm = float(np.linalg.norm(v_arr))
                if v_norm == 0:
                    continue
                sim = float(np.dot(t_arr, v_arr) / (target_norm * v_norm))
            else:
                if len(vec) != len(target_vector):
                    n = min(len(vec), len(target_vector))
                    vec_use = vec[:n]
                    tgt_use = target_vector[:n]
                else:
                    vec_use = vec
                    tgt_use = target_vector
                v_norm = sum(v * v for v in vec_use) ** 0.5
                if v_norm == 0:
                    continue
                dot = sum(a * b for a, b in zip(tgt_use, vec_use))
                sim = dot / (target_norm * v_norm)

            sim = max(-1.0, min(1.0, sim))
            if sim >= self.pre_filter_cosine_threshold:
                results.append((
                    row.bullhorn_candidate_id,
                    sim,
                    row.candidate_name or '',
                    row.profile_text_snippet or '',
                ))

        results.sort(key=lambda r: r[1], reverse=True)
        return results[: self.pre_filter_top_n]

    # ── Layer B: GPT-5.4 final scoring ──────────────────────────────────

    _AI_PROMPT_TEMPLATE = (
        "You are an experienced recruiter performing duplicate-candidate detection.\n"
        "Two candidate profiles are shown below. Both records may belong to the SAME person\n"
        "who re-applied with a new email and a new phone number, or to two DIFFERENT people\n"
        "who happen to have similar resumes.\n\n"
        "Compare them holistically — name, work history (companies + dates), skills,\n"
        "location, and education. Penalise generic matches (common name + common skills).\n"
        "Reward unmistakable signals (same employer + same dates + same school + same city).\n\n"
        "Return ONLY a JSON object on a single line, no markdown, no prose:\n"
        '{{"confidence": 0.0-1.0, "reasoning": "<one short sentence>"}}\n\n'
        "Confidence guide:\n"
        "  0.95+  Almost certainly the same person (multiple unique signals align)\n"
        "  0.90+  Very likely same person (strong work-history + name + location overlap)\n"
        "  0.70-0.89  Possibly same person but ambiguous — DO NOT MERGE\n"
        "  <0.70  Different people\n\n"
        "=== PROFILE A ===\n{profile_a}\n\n"
        "=== PROFILE B ===\n{profile_b}\n"
    )

    def score_pair_with_ai(self, profile_a: str, profile_b: str) -> Tuple[float, str]:
        """Ask GPT-5.4 if A and B are the same person. Returns (confidence, reasoning)."""
        if not profile_a or not profile_b:
            return 0.0, 'empty profile text'

        prompt = self._AI_PROMPT_TEMPLATE.format(profile_a=profile_a, profile_b=profile_b)

        try:
            # Request strict JSON output so the model can't drift into
            # markdown-fenced or prose-prefixed responses that break parse.
            # Falls back to a plain call if the SDK/model rejects the kwarg.
            try:
                response = self.openai_client.chat.completions.create(
                    model=self.model_chat,
                    messages=[{'role': 'user', 'content': prompt}],
                    response_format={'type': 'json_object'},
                )
            except TypeError:
                response = self.openai_client.chat.completions.create(
                    model=self.model_chat,
                    messages=[{'role': 'user', 'content': prompt}],
                )
            if not response.choices:
                return 0.0, 'empty AI response'
            content = (response.choices[0].message.content or '').strip()
            if content.startswith('```'):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content).strip()
            parsed = json.loads(content)
            confidence = float(parsed.get('confidence', 0.0))
            reasoning = str(parsed.get('reasoning', ''))[:500]
            confidence = max(0.0, min(1.0, confidence))
            return confidence, reasoning
        except Exception as e:
            logger.warning(f"FuzzyMatcher: AI scoring failed: {e}")
            return 0.0, f'ai_error: {e}'

    # ── Public entry point ──────────────────────────────────────────────

    def find_fuzzy_duplicates(
        self,
        candidate: Dict,
        exclude_ids: Optional[Iterable[int]] = None,
    ) -> List[Dict]:
        """Return list of {candidate_id, confidence, reasoning, similarity} pairs
        whose AI confidence >= AI_CONFIDENCE_THRESHOLD.

        Caller is responsible for fetching the full duplicate-candidate
        record from Bullhorn and invoking the existing ``merge_candidates``
        pipeline.
        """
        cid = candidate.get('id')
        if not cid:
            return []

        excluded = set(exclude_ids or [])
        excluded.add(cid)

        target_vector, target_profile = self.get_or_create_profile_embedding(candidate)
        if not target_vector or not target_profile:
            logger.debug(f"FuzzyMatcher: no embedding for candidate {cid}, skipping")
            return []

        top = self.find_top_candidates_by_cosine(target_vector, exclude_ids=excluded)
        if not top:
            logger.debug(f"FuzzyMatcher: no cosine candidates >= {self.pre_filter_cosine_threshold} for candidate {cid}")
            return []

        logger.info(
            f"FuzzyMatcher: candidate {cid} → {len(top)} pre-filter candidate(s) "
            f"(top sim={top[0][1]:.3f})"
        )

        accepted: List[Dict] = []
        for other_id, similarity, other_name, other_snippet in top:
            # Pull the freshest profile text for the other candidate so the
            # AI sees the latest data, not a stale snippet.
            other_full = self._fetch_full_candidate(other_id)
            if not other_full:
                continue
            # Skip archived candidates: the cache may hold a row that was
            # cached before the candidate was archived. The exact-match path
            # already filters these out (-status:Archive); the fuzzy path
            # must do the same to avoid suggesting merges into stale records.
            if self._is_archived_status(other_full.get('status')):
                logger.debug(
                    f"FuzzyMatcher: skipping archived candidate {other_id} "
                    "in fuzzy comparison"
                )
                continue
            other_wh = self._fetch_work_history(other_id)
            other_edu = self._fetch_education(other_id)
            other_profile = self.build_profile_text(other_full, other_wh, other_edu)
            if not other_profile:
                continue

            confidence, reasoning = self.score_pair_with_ai(target_profile, other_profile)
            logger.info(
                f"FuzzyMatcher: {cid} vs {other_id} — cosine={similarity:.3f}, "
                f"ai_confidence={confidence:.3f} ({reasoning[:80]})"
            )

            if confidence >= self.ai_confidence_threshold:
                accepted.append({
                    'candidate_id': other_id,
                    'candidate_name': other_name,
                    'confidence': confidence,
                    'reasoning': reasoning,
                    'similarity': similarity,
                    'candidate': other_full,
                })

        return accepted
