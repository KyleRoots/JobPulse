"""Multi-tenant Bullhorn environment registry (Task #100).

Each row represents one connected Bullhorn instance (a "tenant"). The existing
Myticas connection is modeled as the seeded DEFAULT environment, so that with a
single environment present the application behaves byte-for-byte as before.

Credentials:
    The default environment continues to resolve its Bullhorn credentials from
    GlobalSettings (backward compatible — nothing about the existing Myticas
    connection changes). Additional environments may instead carry their own
    inline credential set on this row. ``resolve_credentials()`` returns the
    inline set only when fully populated, otherwise ``None`` to signal "fall
    back to GlobalSettings".

This module is intentionally minimal — it establishes the registry plus the
``environment_id`` discriminator backbone. Brand-rendering and inbound-routing
configuration (apply templates, logos, owner/source routing) are layered on in
a later increment that wires them into the request / inbound paths.
"""
import json
from datetime import datetime
from sqlalchemy import text
from extensions import db


_CREDENTIAL_KEYS = (
    'bullhorn_client_id',
    'bullhorn_client_secret',
    'bullhorn_username',
    'bullhorn_password',
)


class BullhornEnvironment(db.Model):
    """A connected Bullhorn instance / tenant."""
    __tablename__ = 'bullhorn_environment'

    id = db.Column(db.Integer, primary_key=True)
    # Stable machine key, e.g. 'myticas', 'qualified_staffing'.
    key = db.Column(db.String(50), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(120), nullable=False)
    company_name = db.Column(db.String(120), nullable=True)

    # Exactly one row should carry is_default=True (the Myticas connection).
    is_default = db.Column(db.Boolean, nullable=False, default=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    # Optional inline Bullhorn credentials. When absent (the default
    # environment), credentials resolve from GlobalSettings instead — see
    # ``resolve_credentials``.
    bullhorn_client_id = db.Column(db.String(255), nullable=True)
    bullhorn_client_secret = db.Column(db.String(255), nullable=True)
    bullhorn_username = db.Column(db.String(255), nullable=True)
    bullhorn_password = db.Column(db.String(255), nullable=True)

    # Per-brand screening configuration (Task #101). Both NULL on the default
    # (Myticas) environment, so its screening behaves byte-for-byte as before.
    #   screening_profile         — selects prompt/scoring tuning. NULL/''
    #                               resolves to 'standard' (the historical IT
    #                               behavior). 'light_industrial' tunes the
    #                               prompt + experience-floor for sparse
    #                               commercial / trade / warehouse resumes.
    #   screening_config_overrides — optional JSON object of VettingConfig
    #                               key→value overrides applied on top of the
    #                               global settings for this environment only.
    screening_profile = db.Column(db.String(50), nullable=True)
    screening_config_overrides = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # Enforce at most ONE default environment so the no-argument
        # get_bullhorn_service() path can never resolve to ambiguous default
        # credentials. Partial unique index over the rows where is_default.
        db.Index(
            'uq_bullhorn_environment_single_default',
            'is_default', unique=True,
            postgresql_where=text('is_default'),
            sqlite_where=text('is_default = 1'),
        ),
    )

    def __repr__(self):
        flag = ' default' if self.is_default else ''
        return f'<BullhornEnvironment {self.key}{flag}>'

    def resolve_credentials(self):
        """Return the inline credential dict when fully populated, else None.

        ``None`` signals the caller to fall back to GlobalSettings (the
        historical single-tenant credential source for the Myticas connection).
        A partially-filled credential set is treated as "not configured" and
        also returns ``None`` — we never construct a half-credentialed client.
        """
        creds = {}
        for key in _CREDENTIAL_KEYS:
            value = getattr(self, key, None)
            if value and str(value).strip():
                creds[key] = str(value).strip()
        if len(creds) == len(_CREDENTIAL_KEYS):
            return creds
        return None

    def get_screening_profile(self):
        """Return the screening profile key, defaulting to 'standard'.

        NULL or empty resolves to 'standard' so the default environment keeps
        the historical IT screening behavior with no special-casing.
        """
        profile = (self.screening_profile or '').strip()
        return profile or 'standard'

    def get_screening_overrides(self):
        """Return the per-environment VettingConfig override dict.

        Returns ``{}`` when unset or unparseable (fail-soft to the global
        settings), so a malformed override row can never break screening.
        """
        raw = self.screening_config_overrides
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @classmethod
    def get_default(cls):
        """Return the default environment, or None if not seeded yet.

        Ordered by id for deterministic resolution even in the (DB-prevented)
        event of more than one default row.
        """
        return cls.query.filter_by(is_default=True).order_by(cls.id.asc()).first()

    @classmethod
    def get_active(cls):
        """Return all active environments, default first."""
        return cls.query.filter_by(is_active=True).order_by(
            cls.is_default.desc(), cls.id.asc()
        ).all()
