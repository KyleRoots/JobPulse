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
