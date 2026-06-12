"""Customer Onboarding Wizard (Task #102) — super-admin tenant provisioning.

A guided, single-page wizard that provisions a brand-new tenant:
environment credentials (LIVE-validated), brand + apply domain, screening
profile, company admin, and module access. Provisioning is atomic and never
touches the existing default (Myticas) tenant.
"""
import logging

from flask import Blueprint, render_template, request, flash, jsonify
from flask_login import login_required

from routes import admin_required, register_admin_guard

logger = logging.getLogger(__name__)

onboarding_bp = Blueprint('onboarding', __name__, url_prefix='/admin/onboarding')
register_admin_guard(onboarding_bp)

_ALL_MODULES = ['scout_inbound', 'scout_screening', 'scout_vetting', 'scout_support', 'scout_prospector']
_SCREENING_PROFILES = ['standard', 'light_industrial']


@onboarding_bp.route('/', methods=['GET'])
@login_required
@admin_required
def wizard():
    """Render the onboarding wizard."""
    return render_template(
        'onboarding_wizard.html',
        all_modules=_ALL_MODULES,
        screening_profiles=_SCREENING_PROFILES,
        active_page='onboarding',
        form={},
    )


@onboarding_bp.route('/validate-credentials', methods=['POST'])
@login_required
@admin_required
def validate_credentials():
    """LIVE-validate a Bullhorn credential set without writing anything."""
    from utils.tenant_provisioning import validate_bullhorn_credentials

    data = request.get_json(silent=True) or request.form
    ok, message = validate_bullhorn_credentials(
        data.get('bullhorn_client_id'),
        data.get('bullhorn_client_secret'),
        data.get('bullhorn_username'),
        data.get('bullhorn_password'),
    )
    return jsonify({'ok': ok, 'message': message}), (200 if ok else 400)


@onboarding_bp.route('/provision', methods=['POST'])
@login_required
@admin_required
def provision():
    """Provision the tenant from the submitted wizard form."""
    from utils.tenant_provisioning import provision_tenant, ProvisioningError

    f = request.form
    payload = dict(
        display_name=f.get('env_display_name', ''),
        company_name=f.get('company_name', ''),
        credentials={
            'bullhorn_client_id': f.get('bullhorn_client_id', ''),
            'bullhorn_client_secret': f.get('bullhorn_client_secret', ''),
            'bullhorn_username': f.get('bullhorn_username', ''),
            'bullhorn_password': f.get('bullhorn_password', ''),
        },
        brand={
            'display_name': f.get('brand_display_name', ''),
            'apply_domain': f.get('apply_domain', ''),
            'company_name': f.get('brand_company_name', ''),
            'from_email': f.get('from_email', ''),
            'to_email': f.get('to_email', ''),
            'logo_path': f.get('logo_path', ''),
            'logo_filename': f.get('logo_filename', ''),
            'logo_cid': f.get('logo_cid', ''),
            'logo_alt_text': f.get('logo_alt_text', ''),
            'apply_template': f.get('apply_template', '') or 'apply.html',
        },
        screening_profile=f.get('screening_profile', ''),
        admin={
            'username': f.get('admin_username', ''),
            'email': f.get('admin_email', ''),
            'password': f.get('admin_password', ''),
        },
        modules=f.getlist('modules'),
    )

    try:
        result = provision_tenant(**payload)
    except ProvisioningError as e:
        flash(str(e), 'danger')
        return render_template(
            'onboarding_wizard.html',
            all_modules=_ALL_MODULES,
            screening_profiles=_SCREENING_PROFILES,
            active_page='onboarding',
            form=f,
        ), 400

    flash(
        f"Tenant '{result['environment'].display_name}' provisioned successfully.",
        'success',
    )
    return render_template(
        'onboarding_complete.html',
        result=result,
        active_page='onboarding',
    )
