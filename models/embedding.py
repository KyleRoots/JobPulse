"""Job-side embedding cache + filter audit log."""
from datetime import datetime
from extensions import db


class JobEmbedding(db.Model):
    """Cached job description embeddings for the embedding pre-filter (Layer 1)"""
    __tablename__ = 'job_embedding'

    id = db.Column(db.Integer, primary_key=True)
    bullhorn_job_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    job_title = db.Column(db.String(500), nullable=True)
    description_hash = db.Column(db.String(64), nullable=False)  # SHA-256 of description text
    embedding_vector = db.Column(db.Text, nullable=False)  # JSON-serialized float array (1536 dims)
    embedding_model = db.Column(db.String(50), nullable=False, default='text-embedding-3-large')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<JobEmbedding job_id={self.bullhorn_job_id}>'


class EmbeddingFilterLog(db.Model):
    """Audit trail of candidate-job pairs filtered by the embedding pre-filter (Layer 1)"""
    __tablename__ = 'embedding_filter_log'

    id = db.Column(db.Integer, primary_key=True)
    bullhorn_candidate_id = db.Column(db.Integer, nullable=False, index=True)
    candidate_name = db.Column(db.String(255), nullable=True)
    bullhorn_job_id = db.Column(db.Integer, nullable=False, index=True)
    job_title = db.Column(db.String(500), nullable=True)
    similarity_score = db.Column(db.Float, nullable=False)  # Cosine similarity (0.0-1.0)
    threshold_used = db.Column(db.Float, nullable=False)  # Threshold at time of filtering
    resume_snippet = db.Column(db.Text, nullable=True)  # First 500 chars of resume
    filtered_at = db.Column(db.DateTime, default=datetime.utcnow)
    vetting_log_id = db.Column(db.Integer, db.ForeignKey('candidate_vetting_log.id'), nullable=True, index=True)

    __table_args__ = (
        db.Index('idx_filter_log_filtered_at', 'filtered_at'),
        db.Index('idx_filter_log_similarity', 'similarity_score'),
    )

    def __repr__(self):
        return f'<EmbeddingFilterLog candidate={self.bullhorn_candidate_id} job={self.bullhorn_job_id} sim={self.similarity_score}>'


class EmbeddingABLog(db.Model):
    """Shadow-mode A/B telemetry for the text-embedding-3-large vs -3-small
    pre-filter swap (May 2026 cost-savings batch S3, Phase A).

    Each row records one (candidate × job) similarity comparison: the model
    currently used for the production gate decision (primary), and the
    candidate replacement model (shadow). The shadow score never affects
    real production behavior — it's logged only for offline analysis at
    /admin/ai-cost/embedding-ab.

    Used to compute: concordance rate, false-negative rate (qualified
    candidates we'd lose if we cut over), false-positive rate (extra GPT
    cost we'd incur), score correlation, and a threshold sweep table to
    find the optimal threshold for the smaller model.
    """
    __tablename__ = 'embedding_ab_log'

    id = db.Column(db.Integer, primary_key=True)
    vetting_log_id = db.Column(db.Integer, nullable=True, index=True)
    bullhorn_candidate_id = db.Column(db.Integer, nullable=False, index=True)
    candidate_name = db.Column(db.String(255), nullable=True)
    bullhorn_job_id = db.Column(db.Integer, nullable=False, index=True)
    job_title = db.Column(db.String(500), nullable=True)
    primary_model = db.Column(db.String(60), nullable=False)
    shadow_model = db.Column(db.String(60), nullable=False)
    primary_score = db.Column(db.Float, nullable=False)
    shadow_score = db.Column(db.Float, nullable=False)
    threshold_used = db.Column(db.Float, nullable=False)
    primary_passed = db.Column(db.Boolean, nullable=False)
    shadow_would_pass = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.Index('ix_embedding_ab_log_created_at', 'created_at'),
        db.Index('ix_embedding_ab_log_concordance', 'primary_passed', 'shadow_would_pass'),
    )

    def __repr__(self):
        return (f'<EmbeddingABLog candidate={self.bullhorn_candidate_id} '
                f'job={self.bullhorn_job_id} primary={self.primary_score:.3f} '
                f'shadow={self.shadow_score:.3f}>')


class ScreeningABLog(db.Model):
    """Shadow-mode A/B telemetry for the gpt-5.4 vs gpt-4.1-mini screening
    scoring swap (May 2026 cost-savings batch S2).

    Each row records one (candidate × job) scoring comparison: the production
    score returned by gpt-5.4 (prod_score) and the shadow score returned by
    gpt-4.1-mini (shadow_score). The shadow score never affects production
    behavior — it is logged only for offline analysis at
    /admin/ai-cost/screening-ab.

    Acceptance bar for proposing an actual two-stage gate cutover:
      - 0 hard-rejected qualifications (shadow <40 while prod ≥80)
      - ≤3pt avg drift in 40-89 borderline band
      - ≤8pt max single-row drift in 70-89 risk band
      - ≥6,000 paired scores collected

    Schema mirrors EmbeddingABLog conventions for consistent dashboards.
    """
    __tablename__ = 'screening_ab_log'

    id = db.Column(db.Integer, primary_key=True)
    vetting_log_id = db.Column(db.Integer, nullable=True, index=True)
    candidate_job_match_id = db.Column(db.Integer, nullable=True, index=True)
    bullhorn_candidate_id = db.Column(db.Integer, nullable=True, index=True)
    bullhorn_job_id = db.Column(db.Integer, nullable=True, index=True)
    job_title = db.Column(db.String(500), nullable=True)
    prod_model = db.Column(db.String(60), nullable=False)
    shadow_model = db.Column(db.String(60), nullable=False)
    prod_score = db.Column(db.Float, nullable=False)
    shadow_score = db.Column(db.Float, nullable=True)  # nullable: shadow may have failed
    score_delta = db.Column(db.Float, nullable=True)  # shadow - prod, computed on insert
    prod_qualified = db.Column(db.Boolean, nullable=True)
    shadow_qualified_inferred = db.Column(db.Boolean, nullable=True)
    shadow_input_tokens = db.Column(db.Integer, nullable=True)
    shadow_output_tokens = db.Column(db.Integer, nullable=True)
    shadow_estimated_cost_usd = db.Column(db.Numeric(12, 6), nullable=True)
    shadow_duration_ms = db.Column(db.Integer, nullable=True)
    shadow_error = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.Index('ix_screening_ab_log_created_at', 'created_at'),
        db.Index('ix_screening_ab_log_scores', 'prod_score', 'shadow_score'),
    )

    def __repr__(self):
        return (f'<ScreeningABLog candidate={self.bullhorn_candidate_id} '
                f'job={self.bullhorn_job_id} prod={self.prod_score} '
                f'shadow={self.shadow_score}>')
