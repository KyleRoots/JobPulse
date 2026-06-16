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
import re
from datetime import datetime
from sqlalchemy import text
from extensions import db


def _extract_email_addr(value):
    """Pull a bare email address out of a possibly display-name-wrapped string.

    e.g. 'Apply Inbox <apply@myticas.com>' -> 'apply@myticas.com'. Returns ''
    when no address is found.
    """
    if not value:
        return ''
    match = re.search(r'[\w.+-]+@[\w.-]+', str(value))
    return match.group(0).lower() if match else ''


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

    # Per-environment Sales Rep display-name sync config (June 2026). All NULL
    # on the default (Myticas) environment, so it keeps the historical
    # customText3 → customText6 mapping and stays enabled. New tenants are OFF
    # until their fields are configured (their Bullhorn custom fields very
    # likely differ), so the sync can never write to the wrong field.
    #   salesrep_sync_enabled  — tri-state: NULL→enabled only for the default
    #                            env (disabled for new tenants); explicit
    #                            True/False overrides.
    #   salesrep_source_field  — custom field holding the numeric CorporateUser
    #                            ID. NULL → 'customText3'.
    #   salesrep_display_field — custom field that receives the resolved name
    #                            (shown in the company header). NULL →
    #                            'customText6'.
    salesrep_sync_enabled = db.Column(db.Boolean, nullable=True)
    salesrep_source_field = db.Column(db.String(50), nullable=True)
    salesrep_display_field = db.Column(db.String(50), nullable=True)

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

    def salesrep_sync_active(self):
        """Whether the Sales Rep display-name sync runs for this environment.

        Tri-state ``salesrep_sync_enabled``:
          * NULL  → enabled ONLY for the default (Myticas) environment, so it
            keeps running exactly as before; new tenants are OFF until someone
            explicitly configures and enables them (prevents writing to the
            wrong custom field on a tenant that uses customText3/6 differently).
          * True/False → explicit override.
        """
        if self.salesrep_sync_enabled is None:
            return bool(self.is_default)
        return bool(self.salesrep_sync_enabled)

    def get_salesrep_source_field(self):
        """Per-env source field (numeric rep ID), or None to use the default.

        Returns ``None`` when unset/blank so the caller falls back to the
        service default (``customText3``) — keeping a single source of truth.
        """
        value = (self.salesrep_source_field or '').strip()
        return value or None

    def get_salesrep_display_field(self):
        """Per-env display field (resolved name), or None to use the default.

        Returns ``None`` when unset/blank so the caller falls back to the
        service default (``customText6``).
        """
        value = (self.salesrep_display_field or '').strip()
        return value or None

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


def default_environment_id():
    """Resolve the default environment id for stamping new ATS-scoped rows.

    Used as the SQLAlchemy column ``default`` on the multi-tenant
    ``environment_id`` discriminator of tables that carry a per-environment
    unique constraint. Returning the default (Myticas) environment's id means
    every new row is correctly attributed to the single live environment, so
    the per-environment unique constraint behaves exactly like the historical
    single-column unique. Returns ``None`` (fail-soft) when no environment is
    seeded yet — the boot-time backfill stamps any such rows on the next seed.
    """
    try:
        env = BullhornEnvironment.get_default()
        return env.id if env else None
    except Exception:
        return None


class Brand(db.Model):
    """An apply-form identity (domain + branding) belonging to one environment.

    A single Bullhorn environment may serve MULTIPLE brands distinguished only
    by the apply-form host. The Myticas environment, for example, serves both
    the Myticas and STSI brands from one Bullhorn instance (same data, same
    credentials), historically selected by a hardcoded ``'stsigroup' in host``
    check. Brand rows make that host -> template/logo/company/from/to mapping
    data-driven. With the seeded Myticas + STSI brands the apply pipeline
    renders byte-for-byte as before; a new customer adds a new Brand (usually
    one) pointing at its own environment.
    """
    __tablename__ = 'environment_brand'

    id = db.Column(db.Integer, primary_key=True)
    environment_id = db.Column(
        db.Integer, db.ForeignKey('bullhorn_environment.id'),
        nullable=False, index=True,
    )
    # Stable machine key, e.g. 'myticas', 'stsi'.
    key = db.Column(db.String(50), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(120), nullable=False)

    # JSON array of case-insensitive host substrings that select this brand
    # (e.g. ["stsigroup"]). The default brand matches as a fallback regardless.
    domains = db.Column(db.Text, nullable=True)

    # Apply-form rendering.
    apply_template = db.Column(db.String(120), nullable=False, default='apply.html')
    logo_path = db.Column(db.String(255), nullable=True)
    logo_filename = db.Column(db.String(120), nullable=True)
    logo_cid = db.Column(db.String(120), nullable=True)
    company_name = db.Column(db.String(160), nullable=True)
    logo_alt_text = db.Column(db.String(160), nullable=True)

    # Application-forward email envelope.
    from_email = db.Column(db.String(255), nullable=True)
    to_email = db.Column(db.String(255), nullable=True)

    # Exactly one brand should carry is_default=True (the global fallback,
    # historically Myticas).
    is_default = db.Column(db.Boolean, nullable=False, default=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    environment = db.relationship(
        'BullhornEnvironment', backref=db.backref('brands', lazy='dynamic')
    )

    __table_args__ = (
        # Enforce at most ONE default brand so apply-host resolution can never
        # fall back to an ambiguous default. Partial unique index.
        db.Index(
            'uq_environment_brand_single_default',
            'is_default', unique=True,
            postgresql_where=text('is_default'),
            sqlite_where=text('is_default = 1'),
        ),
    )

    def __repr__(self):
        flag = ' default' if self.is_default else ''
        return f'<Brand {self.key}{flag}>'

    def get_domains(self):
        """Return the list of lower-cased host substrings for this brand."""
        raw = self.domains
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except Exception:
            return []
        if isinstance(data, list):
            return [str(d).strip().lower() for d in data if str(d).strip()]
        return []

    def set_domains(self, domains):
        """Persist a list of host substrings as JSON (lower-cased, de-blanked)."""
        cleaned = [str(d).strip().lower() for d in (domains or []) if str(d).strip()]
        self.domains = json.dumps(cleaned)

    def matches_host(self, host):
        """True when any of this brand's domain tokens is a substring of host."""
        if not host:
            return False
        h = host.lower()
        return any(token in h for token in self.get_domains())

    @classmethod
    def resolve_for_host(cls, host):
        """Resolve the Brand for an apply-form request host.

        Non-default brands whose domain token is a substring of the host win
        first (mirroring the historical ``'stsigroup' in host`` check). When
        none match, the global default brand is returned (historically
        Myticas), so any host that is not an STSI host renders the Myticas
        brand exactly as before. Returns ``None`` only when no brands exist.
        """
        h = (host or '').lower()
        candidates = cls.query.filter_by(is_active=True).order_by(
            cls.is_default.asc(), cls.id.asc()
        ).all()
        for brand in candidates:
            if not brand.is_default and brand.matches_host(h):
                return brand
        for brand in candidates:
            if brand.is_default:
                return brand
        return candidates[0] if candidates else None

    @classmethod
    def get_default(cls):
        """Return the global default (fallback) brand, or None if unseeded."""
        return cls.query.filter_by(is_default=True).order_by(cls.id.asc()).first()

    @classmethod
    def resolve_environment_id_for_recipient(cls, recipient):
        """Resolve the owning environment id for an inbound applicant email.

        Matches the email's recipient (To) address against active brands'
        ``to_email``. Falls back to the default environment so single-tenant
        inbound is attributed to the live (Myticas) environment exactly as
        before. Returns ``None`` only when no environment is seeded — callers
        treat that as "leave NULL, backfill stamps it later".
        """
        addr = _extract_email_addr(recipient)
        if addr:
            for brand in cls.query.filter_by(is_active=True).all():
                brand_addr = (brand.to_email or '').strip().lower()
                if brand_addr and brand_addr == addr:
                    return brand.environment_id
        env = BullhornEnvironment.get_default()
        return env.id if env else None
