"""
Embedding Service for Scout Genius Cost Optimization

Provides Layer 1 (embedding pre-filter) functionality:
- Generates text embeddings using OpenAI text-embedding-3-large
- Caches job description embeddings with hash-based change detection
- Computes cosine similarity between candidate resumes and job descriptions
- Filters irrelevant job-candidate pairs before expensive GPT analysis

Architecture:
  Layer 1: Embedding pre-filter (this module) → cheap, blocks irrelevant pairs
  Layer 2: AI analysis → main vetting (candidate_vetting_service.py)
  Layer 3: AI escalation → borderline candidates re-analyzed
"""

import hashlib
import json
import logging
logger = logging.getLogger(__name__)
import math
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from openai import OpenAI

# Tiktoken for precise token counting (graceful fallback if unavailable)
try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    logger.warning("tiktoken not available; using conservative fallback estimation")


# Default configuration constants
DEFAULT_SIMILARITY_THRESHOLD = 0.25
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 3072
MAX_EMBEDDING_TOKENS = 8000  # Model limit is 8192; 192-token safety buffer


class EmbeddingService:
    """
    Manages embedding generation, caching, and similarity-based job filtering.
    
    Usage:
        service = EmbeddingService()
        relevant_jobs, filtered_log = service.filter_relevant_jobs(
            resume_text, jobs, candidate_info, vetting_log_id
        )
    """
    
    def __init__(self):
        self.openai_client = None
        self.embedding_model = DEFAULT_EMBEDDING_MODEL
        self._init_openai()
        try:
            shadow_on = self._shadow_enabled()
            shadow_cap = self._shadow_max_jobs()
            logger.info(
                f"🧪 EmbeddingService init: A/B shadow enabled={shadow_on}, "
                f"max_jobs_per_candidate={shadow_cap if shadow_cap else 'unlimited'}, "
                f"primary_model={self.embedding_model}"
            )
        except Exception as exc:
            logger.warning(f"EmbeddingService init: shadow flag probe failed: {exc}")
    
    def _init_openai(self):
        """Initialize OpenAI client for embedding generation"""
        api_key = os.environ.get('OPENAI_API_KEY')
        if api_key:
            self.openai_client = OpenAI(api_key=api_key)
        else:
            logger.warning("OPENAI_API_KEY not found - embedding service will not work")
    
    @staticmethod
    def count_tokens(text: str, model: str = DEFAULT_EMBEDDING_MODEL) -> int:
        """
        Count tokens using tiktoken if available, fallback to conservative estimation.
        
        Args:
            text: Input text to count tokens for
            model: Model name for tokenizer selection
            
        Returns:
            Token count (exact with tiktoken, conservative estimate without)
        """
        if not text:
            return 0
        
        if TIKTOKEN_AVAILABLE:
            try:
                encoding = tiktoken.encoding_for_model(model)
                return len(encoding.encode(text))
            except Exception as e:
                logger.warning(f"tiktoken encoding failed: {e}, using fallback")
                return len(text) // 3  # Conservative fallback
        else:
            # Conservative estimation: assume 3 chars/token (safer than 4)
            return len(text) // 3
    
    def _truncate_for_embedding(self, text: str, max_tokens: int = MAX_EMBEDDING_TOKENS) -> Tuple[str, bool, int]:
        """
        Intelligently truncate text to stay under the embedding model's token limit.
        
        Uses tiktoken for precise token counting when available, with a conservative
        character-based fallback. Strategy: keep the first ~75% of the token budget
        (contact info, summary, skills, recent work history) and the last ~25%
        (education, certifications). Drops middle content (older work history,
        verbose descriptions).
        
        Args:
            text: The full resume/document text
            max_tokens: Maximum token budget (default 8000, model limit is 8192)
            
        Returns:
            Tuple of (text, was_truncated, original_token_count):
              - text: Original text if under limit, or truncated text
              - was_truncated: True if truncation was applied
              - original_token_count: Token count of the original text
        """
        original_tokens = self.count_tokens(text)
        if original_tokens <= max_tokens:
            return text, False, original_tokens
        
        # Truncate at token level using tiktoken if available
        if TIKTOKEN_AVAILABLE:
            try:
                encoding = tiktoken.encoding_for_model(self.embedding_model)
                tokens = encoding.encode(text)
                
                head_budget = int(max_tokens * 0.75)  # First 75% → top of resume
                tail_budget = max_tokens - head_budget  # Last 25% → education/certs
                
                head_tokens = tokens[:head_budget]
                tail_tokens = tokens[-tail_budget:]
                
                head_text = encoding.decode(head_tokens)
                tail_text = encoding.decode(tail_tokens)
                
                truncated = head_text + "\n...[truncated]...\n" + tail_text
                
                logger.warning(
                    f"📏 Text truncated for embedding: {original_tokens} tokens → "
                    f"{max_tokens} tokens (head={head_budget}, tail={tail_budget}). "
                    f"Original length: {len(text)} chars."
                )
                
                return truncated, True, original_tokens
            except Exception as e:
                logger.warning(f"tiktoken truncation failed: {e}, using char fallback")
        
        # Fallback: character-based truncation (conservative 3 chars/token)
        max_chars = max_tokens * 3
        head_chars = int(max_chars * 0.75)
        tail_chars = max_chars - head_chars
        
        head = text[:head_chars]
        tail = text[-tail_chars:]
        
        logger.warning(
            f"📏 Text truncated for embedding (char fallback): ~{original_tokens} est. tokens "
            f"→ ~{max_tokens} budget. Kept first {head_chars} + last {tail_chars} chars."
        )
        
        return head + "\n...[truncated]...\n" + tail, True, original_tokens
    
    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate an embedding vector for the given text.
        
        Automatically truncates text exceeding the model's token limit (8192 for
        text-embedding-3-large). If truncation is applied, logs a WARNING. If
        generation fails entirely, returns None (caller should handle gracefully
        to allow candidate through to Layer 2).
        
        Args:
            text: Input text to embed (intelligently truncated to stay under token limit)
            
        Returns:
            List of floats (embedding vector) or None if generation fails
        """
        if not self.openai_client:
            logger.error("OpenAI client not initialized - cannot generate embedding")
            return None
        
        if not text or not text.strip():
            logger.warning("Empty text provided for embedding generation")
            return None
        
        try:
            # Intelligently truncate to avoid token limits
            # (text-embedding-3-large supports max 8192 tokens, budget 8000)
            truncated_text, was_truncated, original_tokens = self._truncate_for_embedding(text)
            
            if was_truncated:
                logger.warning(
                    f"Embedding truncation applied: {original_tokens} tokens → "
                    f"{MAX_EMBEDDING_TOKENS} token budget. "
                    f"Resume length: {len(text)} chars."
                )
            
            from services.openai_helper import resolve_model, log_call
            _model = resolve_model('embedding_service.candidate', self.embedding_model)
            response = self.openai_client.embeddings.create(
                input=truncated_text,
                model=_model
            )
            log_call('embedding_service.candidate', _model, response)

            return response.data[0].embedding
            
        except Exception as e:
            logger.error(f"Failed to generate embedding: {str(e)}")
            return None
    
    @staticmethod
    def compute_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """
        Compute cosine similarity between two vectors.
        
        Args:
            vec_a: First embedding vector
            vec_b: Second embedding vector
            
        Returns:
            Cosine similarity score (0.0 to 1.0)
        """
        if not vec_a or not vec_b:
            return 0.0
        
        if len(vec_a) != len(vec_b):
            min_dim = min(len(vec_a), len(vec_b))
            vec_a = vec_a[:min_dim]
            vec_b = vec_b[:min_dim]
        
        # Compute dot product and magnitudes
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        magnitude_a = math.sqrt(sum(a * a for a in vec_a))
        magnitude_b = math.sqrt(sum(b * b for b in vec_b))
        
        if magnitude_a == 0 or magnitude_b == 0:
            return 0.0
        
        similarity = dot_product / (magnitude_a * magnitude_b)
        
        # Clamp to [0, 1] range (can occasionally exceed due to floating point)
        return max(0.0, min(1.0, similarity))
    
    @staticmethod
    def compute_description_hash(description: str) -> str:
        """
        Compute SHA-256 hash of a job description for change detection.
        
        Args:
            description: Job description text
            
        Returns:
            Hex string of SHA-256 hash
        """
        if not description:
            return hashlib.sha256(b"").hexdigest()
        
        # Normalize whitespace before hashing to avoid false positives
        normalized = " ".join(description.split())
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    
    def get_job_embedding(self, job_id: int, description: str, job_title: str = '') -> Optional[List[float]]:
        """
        Get embedding for a job description, using cache when available.
        
        Checks the JobEmbedding table for a cached embedding. If the description
        hash hasn't changed, returns the cached vector. Otherwise, generates a
        new embedding and updates the cache.
        
        Args:
            job_id: Bullhorn job ID
            description: Job description text
            job_title: Job title (for audit/readability)
            
        Returns:
            Embedding vector or None if generation fails
        """
        from models import JobEmbedding
        from app import db
        
        description_hash = self.compute_description_hash(description)
        
        try:
            # Check cache
            cached = JobEmbedding.query.filter_by(bullhorn_job_id=job_id).first()
            
            if cached and cached.description_hash == description_hash and cached.embedding_vector:
                # Cache hit — description hasn't changed
                return json.loads(cached.embedding_vector)
            
            # Cache miss or description changed — generate new embedding
            embedding = self.generate_embedding(description)
            if not embedding:
                return None
            
            vector_json = json.dumps(embedding)
            
            if cached:
                # Update existing cache entry
                cached.description_hash = description_hash
                cached.embedding_vector = vector_json
                cached.job_title = job_title
                cached.embedding_model = self.embedding_model
                cached.updated_at = datetime.utcnow()
                logger.info(f"🔄 Updated embedding cache for job {job_id} (description changed)")
            else:
                # Create new cache entry
                new_entry = JobEmbedding(
                    bullhorn_job_id=job_id,
                    job_title=job_title,
                    description_hash=description_hash,
                    embedding_vector=vector_json,
                    embedding_model=self.embedding_model
                )
                db.session.add(new_entry)
                logger.info(f"📦 Cached new embedding for job {job_id}: {job_title}")
            
            db.session.commit()
            return embedding
            
        except Exception as e:
            logger.error(f"Error in get_job_embedding for job {job_id}: {str(e)}")
            try:
                db.session.rollback()
            except Exception:
                pass
            # Fall through — generate without caching
            return self.generate_embedding(description)
    
    def get_similarity_threshold(self) -> float:
        """
        Get the current embedding similarity threshold from VettingConfig.
        
        Falls back to DEFAULT_SIMILARITY_THRESHOLD if not configured.
        
        Returns:
            Float threshold value (0.0 to 1.0)
        """
        try:
            from models import VettingConfig
            value = VettingConfig.get_value('embedding_similarity_threshold')
            if value is not None:
                return float(value)
        except Exception as e:
            logger.warning(f"Error reading embedding threshold config: {e}")
        
        return DEFAULT_SIMILARITY_THRESHOLD
    
    def is_filter_enabled(self) -> bool:
        """
        Check if the embedding filter is enabled.
        
        Checks env var first (EMBEDDING_FILTER_ENABLED), then VettingConfig.
        Env var takes precedence.
        
        Returns:
            True if the filter should be active
        """
        # Env var override
        env_value = os.environ.get('EMBEDDING_FILTER_ENABLED')
        if env_value is not None:
            return env_value.lower() in ('true', '1', 'yes')
        
        # Database config
        try:
            from models import VettingConfig
            value = VettingConfig.get_value('embedding_filter_enabled')
            if value is not None:
                return value.lower() in ('true', '1', 'yes')
        except Exception as e:
            logger.warning(f"Error reading embedding filter config: {e}")
        
        # Default: enabled
        return True
    
    def _generate_with_model(self, text: str, model: str, site_id: str) -> Optional[List[float]]:
        """Generate an embedding using an explicit model + cost-telemetry site_id.

        Used by shadow-mode A/B logging so the shadow model's spend shows up as
        a distinct row in the AI cost dashboard rather than mingling with the
        primary embedding cost. Fail-soft: returns None on any error so the
        shadow path can be skipped silently without breaking production.
        """
        if not self.openai_client or not text or not text.strip():
            return None
        try:
            truncated_text, _was_trunc, _orig_tok = self._truncate_for_embedding(text)
            from services.openai_helper import log_call
            response = self.openai_client.embeddings.create(
                input=truncated_text,
                model=model,
            )
            log_call(site_id, model, response)
            return response.data[0].embedding
        except Exception as e:
            logger.warning(f"Shadow embedding generation failed ({model}): {e}")
            return None

    @staticmethod
    def _shadow_enabled() -> bool:
        """Read EMBEDDING_AB_SHADOW_ENABLED env var. Off by default."""
        return os.environ.get('EMBEDDING_AB_SHADOW_ENABLED', '').lower() in ('true', '1', 'yes')

    @staticmethod
    def _shadow_max_jobs() -> int:
        """Per-candidate cap on shadow comparisons. Bounds shadow OpenAI cost.

        Read from EMBEDDING_AB_SHADOW_MAX_JOBS env var (default 25). Set to 0
        for unlimited (not recommended in production). Invalid values fall
        back to the default.
        """
        raw = os.environ.get('EMBEDDING_AB_SHADOW_MAX_JOBS', '25')
        try:
            n = int(raw)
            return n if n >= 0 else 25
        except (TypeError, ValueError):
            return 25

    @staticmethod
    def _pick_shadow_model(primary_model: str) -> str:
        """Choose the OTHER model for shadow comparison.

        If primary is `-3-large`, shadow is `-3-small` (testing the
        cost-savings cutover). If primary is anything else (e.g. -3-small
        post-cutover), shadow is `-3-large` (regression-watch on the
        downgrade). Returns the model id string.
        """
        if 'large' in (primary_model or '').lower():
            return 'text-embedding-3-small'
        return 'text-embedding-3-large'

    def _save_ab_log_batch(self, entries: List[Dict]) -> None:
        """Batch-insert shadow A/B comparison rows on an ISOLATED transaction.

        Critical: shadow logging must NEVER affect the calling request's
        ORM session. We use a short-lived raw connection so a write failure
        rolls back only the AB insert — not any pending production writes
        the caller has staged. Fully fail-soft.
        """
        if not entries:
            return
        try:
            from app import db
            from sqlalchemy import text as _text
            sql = _text(
                "INSERT INTO embedding_ab_log "
                "(vetting_log_id, bullhorn_candidate_id, candidate_name, "
                " bullhorn_job_id, job_title, primary_model, shadow_model, "
                " primary_score, shadow_score, threshold_used, "
                " primary_passed, shadow_would_pass, created_at) "
                "VALUES "
                "(:vetting_log_id, :bullhorn_candidate_id, :candidate_name, "
                " :bullhorn_job_id, :job_title, :primary_model, :shadow_model, "
                " :primary_score, :shadow_score, :threshold_used, "
                " :primary_passed, :shadow_would_pass, :created_at)"
            )
            now = datetime.utcnow()
            payload = [{**e, 'created_at': now} for e in entries]
            with db.engine.begin() as conn:
                conn.execute(sql, payload)
            logger.info(f"📊 Shadow A/B: logged {len(entries)} pair comparisons")
        except Exception as exc:
            # Isolated connection auto-rolls-back on context exit. Caller's
            # ORM session is untouched.
            logger.warning(f"Failed to save embedding A/B log batch: {exc}")

    def filter_relevant_jobs(
        self,
        resume_text: str,
        jobs: List[Dict],
        candidate_info: Dict,
        vetting_log_id: int
    ) -> Tuple[List[Dict], int]:
        """
        Filter jobs to only those semantically relevant to the candidate's resume.
        
        This is Layer 1 of the cost optimization pipeline. It computes cosine
        similarity between the candidate's resume embedding and each job's
        cached embedding, filtering out clearly irrelevant pairs.
        
        Args:
            resume_text: Candidate's resume text
            jobs: List of all active job dictionaries from tearsheets
            candidate_info: Dict with 'id', 'name' for logging
            vetting_log_id: Parent CandidateVettingLog ID for FK
            
        Returns:
            Tuple of (relevant_jobs, filtered_count):
              - relevant_jobs: List of jobs that passed the similarity threshold
              - filtered_count: Number of jobs that were filtered out
        """
        # If filter is disabled, pass all jobs through
        if not self.is_filter_enabled():
            logger.info("🔍 Embedding filter is DISABLED — all jobs passed through")
            return jobs, 0
        
        if not resume_text or not jobs:
            return jobs, 0
        
        # Generate resume embedding (one call per candidate)
        resume_embedding = self.generate_embedding(resume_text)
        if not resume_embedding:
            logger.warning("⚠️ Failed to generate resume embedding — bypassing filter, all jobs passed through")
            return jobs, 0
        
        threshold = self.get_similarity_threshold()
        candidate_id = candidate_info.get('id', 0)
        candidate_name = candidate_info.get('name', 'Unknown')
        resume_snippet = resume_text[:500] if resume_text else ''
        
        relevant_jobs = []
        filtered_entries = []

        # Shadow-mode A/B setup: if EMBEDDING_AB_SHADOW_ENABLED is on, also
        # compute similarities using the OTHER embedding model and log each
        # (candidate × job) comparison for offline analysis. Fully fail-soft —
        # any error in the shadow path leaves production behavior untouched.
        # Per-candidate cap (env var EMBEDDING_AB_SHADOW_MAX_JOBS, default 25)
        # bounds the extra OpenAI cost incurred during the shadow window so
        # large tearsheets don't blow up the cost envelope.
        ab_shadow_on = self._shadow_enabled()
        ab_shadow_remaining = self._shadow_max_jobs()
        ab_log_entries: List[Dict] = []
        shadow_model: Optional[str] = None
        shadow_resume_emb: Optional[List[float]] = None
        primary_model_label = self.embedding_model
        if ab_shadow_on:
            try:
                from services.openai_helper import resolve_model
                primary_model_label = resolve_model('embedding_service.candidate', self.embedding_model)
                shadow_model = self._pick_shadow_model(primary_model_label)
                shadow_resume_emb = self._generate_with_model(
                    resume_text, shadow_model, 'embedding_service.shadow'
                )
                if shadow_resume_emb is None:
                    # Couldn't get shadow resume embedding — disable for this call
                    logger.warning("Shadow A/B: resume embedding failed, skipping shadow comparisons")
                    ab_shadow_on = False
                else:
                    logger.info(
                        f"🧪 Shadow A/B setup OK for candidate {candidate_id}: "
                        f"primary={primary_model_label}, shadow={shadow_model}, "
                        f"job_cap={ab_shadow_remaining if ab_shadow_remaining else 'unlimited'}"
                    )
            except Exception as exc:
                logger.warning(f"Shadow A/B setup failed: {exc}")
                ab_shadow_on = False

        for job in jobs:
            job_id = job.get('id', 0)
            job_title = job.get('title', 'Unknown')
            job_description = job.get('description', '') or job.get('publicDescription', '') or ''
            
            if not job_description.strip():
                # No description to compare — let it through (safe fallback)
                relevant_jobs.append(job)
                continue
            
            # Get cached or generate job embedding
            job_embedding = self.get_job_embedding(job_id, job_description, job_title)
            if not job_embedding:
                # Failed to get embedding — let it through (safe fallback)
                relevant_jobs.append(job)
                continue
            
            # Compute similarity
            similarity = self.compute_similarity(resume_embedding, job_embedding)
            
            primary_passed = similarity >= threshold
            if primary_passed:
                relevant_jobs.append(job)
            else:
                # Filtered — log for audit
                filtered_entries.append({
                    'bullhorn_candidate_id': candidate_id,
                    'candidate_name': candidate_name,
                    'bullhorn_job_id': job_id,
                    'job_title': job_title,
                    'similarity_score': round(similarity, 6),
                    'threshold_used': threshold,
                    'resume_snippet': resume_snippet,
                    'vetting_log_id': vetting_log_id
                })

            # Shadow A/B per-job comparison (best effort, fail-soft).
            # Capped at EMBEDDING_AB_SHADOW_MAX_JOBS per candidate (default 25;
            # 0 means unlimited). Bounds shadow OpenAI cost.
            shadow_cap = self._shadow_max_jobs()
            shadow_under_cap = (shadow_cap == 0) or (ab_shadow_remaining > 0)
            if (ab_shadow_on and shadow_model and shadow_resume_emb and shadow_under_cap):
                if shadow_cap != 0:
                    ab_shadow_remaining -= 1
                try:
                    shadow_job_emb = self._generate_with_model(
                        job_description, shadow_model, 'embedding_service.shadow'
                    )
                    if shadow_job_emb:
                        shadow_sim = self.compute_similarity(shadow_resume_emb, shadow_job_emb)
                        ab_log_entries.append({
                            'vetting_log_id': vetting_log_id,
                            'bullhorn_candidate_id': candidate_id,
                            'candidate_name': candidate_name,
                            'bullhorn_job_id': job_id,
                            'job_title': job_title,
                            'primary_model': primary_model_label,
                            'shadow_model': shadow_model,
                            'primary_score': round(float(similarity), 6),
                            'shadow_score': round(float(shadow_sim), 6),
                            'threshold_used': float(threshold),
                            'primary_passed': bool(primary_passed),
                            'shadow_would_pass': bool(shadow_sim >= threshold),
                        })
                except Exception as exc:
                    logger.warning(f"Shadow A/B per-job comparison failed (job {job_id}): {exc}")
        
        # Batch-write filtered entries to EmbeddingFilterLog
        filtered_count = len(filtered_entries)
        if filtered_entries:
            self._save_filter_logs(filtered_entries)

        # Batch-write shadow A/B entries (no-op if shadow off or no entries)
        if ab_log_entries:
            self._save_ab_log_batch(ab_log_entries)
            logger.info(
                f"🧪 Shadow A/B flushed {len(ab_log_entries)} comparisons "
                f"for candidate {candidate_id} (primary={primary_model_label}, shadow={shadow_model})"
            )
        elif ab_shadow_on:
            logger.info(
                f"🧪 Shadow A/B was enabled for candidate {candidate_id} but produced 0 comparisons "
                f"(jobs={len(jobs)}, cap_remaining={ab_shadow_remaining})"
            )
        
        logger.info(
            f"🔍 Embedding pre-filter for {candidate_name} (ID: {candidate_id}): "
            f"{len(relevant_jobs)} jobs passed, {filtered_count} filtered "
            f"(threshold={threshold}, total={len(jobs)})"
        )
        
        return relevant_jobs, filtered_count
    
    def _save_filter_logs(self, entries: List[Dict]):
        """
        Batch-save EmbeddingFilterLog entries for audit.
        
        Args:
            entries: List of filter log dictionaries
        """
        try:
            from models import EmbeddingFilterLog
            from app import db
            
            for entry in entries:
                log = EmbeddingFilterLog(
                    bullhorn_candidate_id=entry['bullhorn_candidate_id'],
                    candidate_name=entry['candidate_name'],
                    bullhorn_job_id=entry['bullhorn_job_id'],
                    job_title=entry['job_title'],
                    similarity_score=entry['similarity_score'],
                    threshold_used=entry['threshold_used'],
                    resume_snippet=entry['resume_snippet'],
                    vetting_log_id=entry['vetting_log_id']
                )
                db.session.add(log)
            
            db.session.commit()
            logger.debug(f"📝 Saved {len(entries)} embedding filter log entries")
            
        except Exception as e:
            logger.error(f"Failed to save embedding filter logs: {str(e)}")
            try:
                from app import db
                db.session.rollback()
            except Exception:
                pass
    
    def save_escalation_log(
        self,
        vetting_log_id: int,
        candidate_id: int,
        candidate_name: str,
        job_id: int,
        job_title: str,
        mini_score: float,
        gpt4o_score: float,
        threshold: float
    ):
        """
        Log an escalation event (Layer 2 → Layer 3 re-analysis).
        
        Args:
            vetting_log_id: Parent CandidateVettingLog ID
            candidate_id: Bullhorn candidate ID
            candidate_name: Candidate name for audit
            job_id: Bullhorn job ID
            job_title: Job title for audit
            mini_score: Layer 2 model score
            gpt4o_score: Layer 3 model score
            threshold: Job-specific or global threshold used
        """
        try:
            from models import EscalationLog
            from app import db
            
            score_delta = gpt4o_score - mini_score
            material_change = abs(score_delta) >= 5.0
            
            # Check if recommendation crossed the threshold
            mini_recommended = mini_score >= threshold
            layer3_recommended = gpt4o_score >= threshold
            crossed_threshold = mini_recommended != layer3_recommended
            
            log = EscalationLog(
                vetting_log_id=vetting_log_id,
                bullhorn_candidate_id=candidate_id,
                candidate_name=candidate_name,
                bullhorn_job_id=job_id,
                job_title=job_title,
                mini_score=mini_score,
                gpt4o_score=gpt4o_score,
                score_delta=round(score_delta, 2),
                material_change=material_change,
                threshold_used=threshold,
                crossed_threshold=crossed_threshold
            )
            db.session.add(log)
            db.session.commit()
            
            status = "CROSSED THRESHOLD" if crossed_threshold else ("MATERIAL CHANGE" if material_change else "minor")
            logger.info(
                f"📊 Escalation logged: {candidate_name} × {job_title} — "
                f"mini={mini_score}% → gpt4o={gpt4o_score}% (Δ{score_delta:+.1f}) [{status}]"
            )
            
        except Exception as e:
            logger.error(f"Failed to save escalation log: {str(e)}")
            try:
                from app import db
                db.session.rollback()
            except Exception:
                pass
