"""Customer onboarding / tenant provisioning service (Task #102).

Provisions a brand-new tenant from the super-admin onboarding wizard:
  * a ``BullhornEnvironment`` (with inline, LIVE-validated credentials),
  * its primary ``Brand`` (apply-form identity + domain),
  * a company-admin ``User`` scoped to that environment, with module access.

Hard invariant: a new tenant is NEVER the default. ``is_default`` stays False on
both the new environment and brand, so the existing Myticas tenant remains the
sole default and all current behavior is unchanged. The single-default partial
unique indexes enforce this at the DB level as a backstop.

Credential validation is LIVE: we authenticate against Bullhorn and perform a
small test fetch before writing anything. Invalid credentials abort the whole
provision with a clear error and zero DB writes.
"""
import logging
import re

logger = logging.getLogger(__name__)


class ProvisioningError(Exception):
    """Raised when provisioning cannot proceed (validation or write failure)."""


def validate_bullhorn_credentials(client_id, client_secret, username, password):
    """LIVE-validate a Bullhorn credential set (auth + test fetch).

    Returns (ok: bool, message: str). Never raises — any exception is converted
    to a clear, user-facing failure message so the wizard can block cleanly.
    """
    missing = [
        name for name, val in (
            ('Client ID', client_id),
            ('Client Secret', client_secret),
            ('Username', username),
            ('Password', password),
        ) if not (val and str(val).strip())
    ]
    if missing:
        return False, f"Missing required credential(s): {', '.join(missing)}."

    try:
        from bullhorn_service import BullhornService
        svc = BullhornService(
            client_id=str(client_id).strip(),
            client_secret=str(client_secret).strip(),
            username=str(username).strip(),
            password=str(password).strip(),
        )
        if not svc.authenticate():
            return False, (
                "Bullhorn authentication failed. Double-check the Client ID, "
                "Client Secret, Username and Password."
            )
        # Test fetch — confirms the token actually works against the REST API.
        try:
            svc.get_tearsheets()
        except Exception as fetch_err:
            logger.warning(f"Credential test-fetch failed: {fetch_err}")
            return False, (
                "Authenticated, but a test data fetch failed. The account may "
                "lack API access or tearsheet permissions."
            )
        return True, "Credentials validated successfully."
    except Exception as e:
        logger.warning(f"Bullhorn credential validation error: {e}")
        return False, f"Could not validate credentials: {e}"


def _slugify(value):
    slug = re.sub(r'[^a-z0-9]+', '_', (value or '').strip().lower()).strip('_')
    return slug or 'tenant'


def _unique_key(model, base):
    """Return a key unique within ``model.key`` derived from ``base``."""
    key = base
    suffix = 2
    while model.query.filter_by(key=key).first() is not None:
        key = f"{base}_{suffix}"
        suffix += 1
    return key


def provision_tenant(
    *,
    display_name,
    company_name,
    credentials,
    brand,
    screening_profile=None,
    admin,
    modules,
    validate=True,
):
    """Provision a new tenant atomically.

    Args:
        display_name: Human label for the environment (e.g. "Acme Staffing").
        company_name: Company name for the environment + admin user.
        credentials: dict with bullhorn_client_id/secret/username/password.
        brand: dict with display_name, apply_domain, company_name, from_email,
            to_email, logo_* (optional), apply_template (optional).
        screening_profile: optional screening profile key for the environment.
        admin: dict with username, email, password.
        modules: list of module keys to grant the admin user.
        validate: when True (default) LIVE-validate credentials first.

    Returns:
        dict summary: {environment, brand, user, readiness}.

    Raises:
        ProvisioningError on any validation or write failure (no partial writes).
    """
    from app import db
    from models import BullhornEnvironment, Brand, User, AVAILABLE_MODULES

    creds = credentials or {}
    cid = (creds.get('bullhorn_client_id') or '').strip()
    csecret = (creds.get('bullhorn_client_secret') or '').strip()
    cuser = (creds.get('bullhorn_username') or '').strip()
    cpass = (creds.get('bullhorn_password') or '').strip()

    # ── 1. Required-field validation ─────────────────────────────────────────
    if not (display_name or '').strip():
        raise ProvisioningError("Environment display name is required.")
    admin = admin or {}
    a_user = (admin.get('username') or '').strip()
    a_email = (admin.get('email') or '').strip().lower()
    a_pass = admin.get('password') or ''
    if not a_user or not a_email or not a_pass:
        raise ProvisioningError("Admin username, email and password are all required.")

    # Uniqueness pre-checks (clear error instead of a raw IntegrityError).
    if User.query.filter_by(username=a_user).first() is not None:
        raise ProvisioningError(f"A user named '{a_user}' already exists.")
    if User.query.filter_by(email=a_email).first() is not None:
        raise ProvisioningError(f"A user with email '{a_email}' already exists.")

    # ── 2. LIVE credential validation ────────────────────────────────────────
    if validate:
        ok, message = validate_bullhorn_credentials(cid, csecret, cuser, cpass)
        if not ok:
            raise ProvisioningError(message)

    # ── 3. Atomic provision ──────────────────────────────────────────────────
    try:
        env = BullhornEnvironment(
            key=_unique_key(BullhornEnvironment, _slugify(display_name)),
            display_name=display_name.strip(),
            company_name=(company_name or '').strip() or None,
            is_default=False,          # never the default — invariant
            is_active=True,
            bullhorn_client_id=cid or None,
            bullhorn_client_secret=csecret or None,
            bullhorn_username=cuser or None,
            bullhorn_password=cpass or None,
            screening_profile=(screening_profile or '').strip() or None,
        )
        db.session.add(env)
        db.session.flush()  # assign env.id

        b = brand or {}
        b_name = (b.get('display_name') or display_name).strip()
        apply_domain = (b.get('apply_domain') or '').strip().lower()
        new_to_email = (b.get('to_email') or '').strip().lower() or None

        # Inbound routing keys on Brand.to_email; a duplicate active to_email
        # makes recipient -> environment resolution ambiguous (could misroute
        # one tenant's applicants into another). Reject up front rather than
        # silently letting "first active match wins" pick the wrong tenant.
        if new_to_email is not None:
            clash = (
                Brand.query
                .filter(Brand.is_active.is_(True))
                .filter(db.func.lower(Brand.to_email) == new_to_email)
                .first()
            )
            if clash is not None:
                raise ProvisioningError(
                    f"The apply inbox '{new_to_email}' is already in use by "
                    f"another active brand; inbound routing requires a unique "
                    f"apply inbox per tenant."
                )

        brand_row = Brand(
            environment_id=env.id,
            key=_unique_key(Brand, _slugify(b_name)),
            display_name=b_name,
            apply_template=(b.get('apply_template') or 'apply.html').strip(),
            logo_path=(b.get('logo_path') or '').strip() or None,
            logo_filename=(b.get('logo_filename') or '').strip() or None,
            logo_cid=(b.get('logo_cid') or '').strip() or None,
            company_name=(b.get('company_name') or company_name or '').strip() or None,
            logo_alt_text=(b.get('logo_alt_text') or b_name).strip() or None,
            from_email=(b.get('from_email') or '').strip() or None,
            to_email=(b.get('to_email') or '').strip() or None,
            is_default=False,          # never the default — invariant
            is_active=True,
        )
        brand_row.set_domains([apply_domain] if apply_domain else [])
        db.session.add(brand_row)

        valid_modules = [m for m in (modules or []) if m in AVAILABLE_MODULES]
        admin_user = User(
            username=a_user,
            email=a_email,
            is_admin=False,
            is_company_admin=True,
            role='company_admin',
            company=(company_name or display_name).strip(),
            environment_id=env.id,
        )
        admin_user.set_password(a_pass)
        admin_user.set_modules(valid_modules)
        db.session.add(admin_user)

        db.session.commit()
    except ProvisioningError:
        db.session.rollback()
        raise
    except Exception as e:
        db.session.rollback()
        logger.error(f"Tenant provisioning failed during write: {e}", exc_info=True)
        raise ProvisioningError(f"Provisioning failed while saving: {e}")

    readiness = run_readiness_check(env.id)
    return {
        'environment': env,
        'brand': brand_row,
        'user': admin_user,
        'readiness': readiness,
    }


def run_readiness_check(environment_id):
    """Post-provision readiness check for a tenant.

    Returns a list of {check, ok, detail} dicts so the wizard can render a
    green/red checklist. Read-only and fail-soft.
    """
    from models import BullhornEnvironment, Brand, User

    checks = []
    env = BullhornEnvironment.query.get(environment_id)
    checks.append({
        'check': 'Environment created',
        'ok': env is not None,
        'detail': env.display_name if env else 'missing',
    })
    if env is not None:
        creds_ok = env.resolve_credentials() is not None
        checks.append({
            'check': 'Inline Bullhorn credentials present',
            'ok': creds_ok,
            'detail': 'configured' if creds_ok else 'incomplete',
        })
        brand_count = Brand.query.filter_by(environment_id=env.id, is_active=True).count()
        checks.append({
            'check': 'At least one active brand',
            'ok': brand_count >= 1,
            'detail': f'{brand_count} brand(s)',
        })
        admin_count = User.query.filter_by(environment_id=env.id).count()
        checks.append({
            'check': 'Company admin user',
            'ok': admin_count >= 1,
            'detail': f'{admin_count} user(s)',
        })
        checks.append({
            'check': 'Not the default environment (isolation)',
            'ok': env.is_default is False,
            'detail': 'tenant is isolated' if env.is_default is False else 'WARNING: is default',
        })
    return checks
