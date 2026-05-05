"""Candidate-side models: parsed-resume cache, profile embedding, merge log, fuzzy queue."""
import json
from datetime import datetime
from extensions import db


class ParsedResumeCache(db.Model):
    """Cache for parsed resume results to reduce OpenAI API costs.

    Uses content-based hashing (SHA-256) to identify duplicate resumes.
    Same resume content = cache hit = skip AI call.
    """
    id = db.Column(db.Integer, primary_key=True)
    content_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)  # SHA-256 hash
    candidate_id = db.Column(db.Integer, nullable=True, index=True)  # Optional Bullhorn candidate ID

    # Parsed results
    parsed_data_json = db.Column(db.Text, nullable=False)  # JSON: {first_name, last_name, email, phone}
    raw_text = db.Column(db.Text, nullable=True)  # Extracted plain text
    formatted_html = db.Column(db.Text, nullable=True)  # AI-formatted HTML

    # Cache metadata
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_accessed = db.Column(db.DateTime, default=datetime.utcnow)  # For LRU tracking
    access_count = db.Column(db.Integer, default=1)  # Track cache hits

    # TTL: 90 days default, entries older than this are considered stale
    CACHE_TTL_DAYS = 90

    def __repr__(self):
        return f'<ParsedResumeCache {self.content_hash[:16]}... accessed {self.access_count}x>'

    @classmethod
    def get_cached(cls, content_hash):
        """Retrieve cached result by content hash, update access stats if found."""
        cached = cls.query.filter_by(content_hash=content_hash).first()
        if cached:
            # Check TTL
            age_days = (datetime.utcnow() - cached.created_at).days
            if age_days > cls.CACHE_TTL_DAYS:
                # Cache entry expired, delete it
                db.session.delete(cached)
                db.session.commit()
                return None
            # Update access stats
            cached.last_accessed = datetime.utcnow()
            cached.access_count += 1
            db.session.commit()
        return cached

    @classmethod
    def store(cls, content_hash, parsed_data, raw_text, formatted_html, candidate_id=None):
        """Store parsed result in cache."""
        cached = cls(
            content_hash=content_hash,
            candidate_id=candidate_id,
            parsed_data_json=json.dumps(parsed_data),
            raw_text=raw_text,
            formatted_html=formatted_html
        )
        db.session.add(cached)
        db.session.commit()
        return cached


class CandidateMergeLog(db.Model):
    __tablename__ = 'candidate_merge_log'
    id = db.Column(db.Integer, primary_key=True)
    primary_candidate_id = db.Column(db.Integer, nullable=False)
    duplicate_candidate_id = db.Column(db.Integer, nullable=False)
    primary_name = db.Column(db.String(200), nullable=True)
    duplicate_name = db.Column(db.String(200), nullable=True)
    confidence_score = db.Column(db.Float, nullable=False)
    match_field = db.Column(db.String(50), nullable=True)
    # 'exact' = email/phone/name exact match (legacy default)
    # 'ai_fuzzy' = embedding pre-filter + GPT confirmation (Task #57)
    match_type = db.Column(db.String(20), nullable=False, default='exact')
    merge_type = db.Column(db.String(20), nullable=False, default='scheduled')
    items_transferred = db.Column(db.Text, nullable=True)
    merged_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    merged_by = db.Column(db.String(100), nullable=True, default='system')
    skipped = db.Column(db.Boolean, nullable=False, default=False)
    skip_reason = db.Column(db.String(500), nullable=True)

    __table_args__ = (
        db.Index('idx_merge_log_primary', 'primary_candidate_id'),
        db.Index('idx_merge_log_duplicate', 'duplicate_candidate_id'),
        db.Index('idx_merge_log_merged_at', 'merged_at'),
        db.Index('idx_merge_log_match_type', 'match_type'),
    )

    def __repr__(self):
        action = 'SKIPPED' if self.skipped else 'MERGED'
        return f'<CandidateMergeLog {self.id}: {action} ({self.match_type}) {self.duplicate_candidate_id} -> {self.primary_candidate_id}>'


class CandidateProfileEmbedding(db.Model):
    """Cached candidate-profile embeddings for the AI fuzzy duplicate matcher (Task #57).

    Stores a vector representation of each candidate built from their name +
    work history + skills + location + education. Used by
    `fuzzy_duplicate_matcher.FuzzyDuplicateMatcher` to detect duplicates
    where both the email AND the phone changed (the largest accuracy gap in
    the exact-match dedup pipeline).

    The `profile_hash` lets us detect when a candidate's underlying profile
    has changed since the last embedding so we can refresh just those rows.
    """
    __tablename__ = 'candidate_profile_embedding'

    id = db.Column(db.Integer, primary_key=True)
    bullhorn_candidate_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    candidate_name = db.Column(db.String(200), nullable=True)
    profile_hash = db.Column(db.String(64), nullable=False)
    embedding_vector = db.Column(db.Text, nullable=False)
    embedding_model = db.Column(db.String(50), nullable=False, default='text-embedding-3-large')
    profile_text_snippet = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_cand_profile_emb_updated', 'updated_at'),
    )

    def __repr__(self):
        return f'<CandidateProfileEmbedding candidate_id={self.bullhorn_candidate_id}>'


class FuzzyEvaluationQueue(db.Model):
    """Persistent overflow queue for the AI fuzzy duplicate matcher (Task #57).

    The fuzzy pass is bounded by ``FUZZY_MAX_CANDIDATES_PER_CYCLE`` per
    scheduled run so we don't blow the hourly job window. Without a
    durable queue, candidates that overflow that cap would simply be
    re-discovered next cycle by the recent-window scan — but only if
    they're still inside ``RECENT_WINDOW_HOURS``. Under sustained load
    they would age out and never be evaluated.

    This table records each deferred candidate so the next cycle drains
    them BEFORE looking at fresh recent candidates, with retry/backoff
    semantics so a single broken record can't permanently block the queue.
    """
    __tablename__ = 'fuzzy_evaluation_queue'

    id = db.Column(db.Integer, primary_key=True)
    bullhorn_candidate_id = db.Column(
        db.Integer, unique=True, nullable=False, index=True
    )
    enqueued_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    last_attempted_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)

    __table_args__ = (
        db.Index('idx_fuzzy_queue_enqueued_at', 'enqueued_at'),
    )

    def __repr__(self):
        return (
            f'<FuzzyEvaluationQueue candidate_id={self.bullhorn_candidate_id} '
            f'attempts={self.attempts}>'
        )
