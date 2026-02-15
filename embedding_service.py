"""
Embedding Service for JobPulse Cost Optimization

Provides Layer 1 (embedding pre-filter) functionality:
- Generates text embeddings using OpenAI text-embedding-3-small
- Caches job description embeddings with hash-based change detection
- Computes cosine similarity between candidate resumes and job descriptions
- Filters irrelevant job-candidate pairs before expensive GPT analysis

Architecture:
  Layer 1: Embedding pre-filter (this module) → cheap, blocks irrelevant pairs
  Layer 2: GPT-4o-mini analysis → main vetting (candidate_vetting_service.py)
  Layer 3: GPT-4o escalation → borderline candidates re-analyzed
"""

import hashlib
import json
import logging
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
    logging.warning("tiktoken not available; using conservative fallback estimation")


# Default configuration constants
DEFAULT_SIMILARITY_THRESHOLD = 0.25
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 1536
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
    
    def _init_openai(self):
        """Initialize OpenAI client for embedding generation"""
        api_key = os.environ.get('OPENAI_API_KEY')
        if api_key:
            self.openai_client = OpenAI(api_key=api_key)
        else:
            logging.warning("OPENAI_API_KEY not found - embedding service will not work")
    
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
                logging.warning(f"tiktoken encoding failed: {e}, using fallback")
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
                
                logging.warning(
                    f"📏 Text truncated for embedding: {original_tokens} tokens → "
                    f"{max_tokens} tokens (head={head_budget}, tail={tail_budget}). "
                    f"Original length: {len(text)} chars."
                )
                
                return truncated, True, original_tokens
            except Exception as e:
                logging.warning(f"tiktoken truncation failed: {e}, using char fallback")
        
        # Fallback: character-based truncation (conservative 3 chars/token)
        max_chars = max_tokens * 3
        head_chars = int(max_chars * 0.75)
        tail_chars = max_chars - head_chars
        
        head = text[:head_chars]
        tail = text[-tail_chars:]
        
        logging.warning(
            f"📏 Text truncated for embedding (char fallback): ~{original_tokens} est. tokens "
            f"→ ~{max_tokens} budget. Kept first {head_chars} + last {tail_chars} chars."
        )
        
        return head + "\n...[truncated]...\n" + tail, True, original_tokens
    
    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate an embedding vector for the given text.
        
        Automatically truncates text exceeding the model's token limit (8192 for
        text-embedding-3-small). If truncation is applied, logs a WARNING. If
        generation fails entirely, returns None (caller should handle gracefully
        to allow candidate through to Layer 2).
        
        Args:
            text: Input text to embed (intelligently truncated to stay under token limit)
            
        Returns:
            List of floats (embedding vector) or None if generation fails
        """
        if not self.openai_client:
            logging.error("OpenAI client not initialized - cannot generate embedding")
            return None
        
        if not text or not text.strip():
            logging.warning("Empty text provided for embedding generation")
            return None
        
        try:
            # Intelligently truncate to avoid token limits
            # (text-embedding-3-small supports max 8192 tokens, budget 8000)
            truncated_text, was_truncated, original_tokens = self._truncate_for_embedding(text)
            
            if was_truncated:
                logging.warning(
                    f"Embedding truncation applied: {original_tokens} tokens → "
                    f"{MAX_EMBEDDING_TOKENS} token budget. "
                    f"Resume length: {len(text)} chars."
                )
            
            response = self.openai_client.embeddings.create(
                input=truncated_text,
                model=self.embedding_model
            )
            
            return response.data[0].embedding
            
        except Exception as e:
            logging.error(f"Failed to generate embedding: {str(e)}")
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
            logging.warning(f"Vector dimension mismatch: {len(vec_a)} vs {len(vec_b)}")
            return 0.0
        
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
                logging.info(f"🔄 Updated embedding cache for job {job_id} (description changed)")
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
                logging.info(f"📦 Cached new embedding for job {job_id}: {job_title}")
            
            db.session.commit()
            return embedding
            
        except Exception as e:
            logging.error(f"Error in get_job_embedding for job {job_id}: {str(e)}")
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
            logging.warning(f"Error reading embedding threshold config: {e}")
        
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
            logging.warning(f"Error reading embedding filter config: {e}")
        
        # Default: enabled
        return True
    
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
            logging.info("🔍 Embedding filter is DISABLED — all jobs passed through")
            return jobs, 0
        
        if not resume_text or not jobs:
            return jobs, 0
        
        # Generate resume embedding (one call per candidate)
        resume_embedding = self.generate_embedding(resume_text)
        if not resume_embedding:
            logging.warning("⚠️ Failed to generate resume embedding — bypassing filter, all jobs passed through")
            return jobs, 0
        
        threshold = self.get_similarity_threshold()
        candidate_id = candidate_info.get('id', 0)
        candidate_name = candidate_info.get('name', 'Unknown')
        resume_snippet = resume_text[:500] if resume_text else ''
        
        relevant_jobs = []
        filtered_entries = []
        
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
            
            if similarity >= threshold:
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
        
        # Batch-write filtered entries to EmbeddingFilterLog
        filtered_count = len(filtered_entries)
        if filtered_entries:
            self._save_filter_logs(filtered_entries)
        
        logging.info(
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
            logging.debug(f"📝 Saved {len(entries)} embedding filter log entries")
            
        except Exception as e:
            logging.error(f"Failed to save embedding filter logs: {str(e)}")
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
            mini_score: GPT-4o-mini score (Layer 2)
            gpt4o_score: GPT-4o score (Layer 3)
            threshold: Job-specific or global threshold used
        """
        try:
            from models import EscalationLog
            from app import db
            
            score_delta = gpt4o_score - mini_score
            material_change = abs(score_delta) >= 5.0
            
            # Check if recommendation crossed the threshold
            mini_recommended = mini_score >= threshold
            gpt4o_recommended = gpt4o_score >= threshold
            crossed_threshold = mini_recommended != gpt4o_recommended
            
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
            logging.info(
                f"📊 Escalation logged: {candidate_name} × {job_title} — "
                f"mini={mini_score}% → gpt4o={gpt4o_score}% (Δ{score_delta:+.1f}) [{status}]"
            )
            
        except Exception as e:
            logging.error(f"Failed to save escalation log: {str(e)}")
            try:
                from app import db
                db.session.rollback()
            except Exception:
                pass
