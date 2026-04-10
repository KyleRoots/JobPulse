import json
import logging
from datetime import datetime

from flask import Blueprint, render_template, jsonify, request, redirect, url_for, flash, Response
from flask_login import login_required, current_user
from extensions import db
from routes import register_module_guard

scout_prospector_bp = Blueprint('scout_prospector', __name__)
register_module_guard(scout_prospector_bp, 'scout_prospector')
logger = logging.getLogger(__name__)


def _get_service():
    from scout_prospector_service import ProspectorService
    return ProspectorService()


@scout_prospector_bp.route('/scout-prospector')
@login_required
def prospector_dashboard():
    service = _get_service()
    profiles = service.get_user_profiles(current_user)
    stats = service.get_prospect_stats(current_user)

    status_filter = request.args.get('status', 'all')
    profile_filter = request.args.get('profile', '')
    search_query = request.args.get('search', '')
    sort_by = request.args.get('sort', 'qualification_score')
    sort_dir = request.args.get('dir', 'desc')

    try:
        profile_id = int(profile_filter) if profile_filter else None
    except (ValueError, TypeError):
        profile_id = None
        profile_filter = ''
    prospects = service.get_user_prospects(
        current_user,
        status=status_filter,
        profile_id=profile_id,
        search=search_query,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )

    return render_template(
        'scout_prospector.html',
        active_page='scout_prospector',
        profiles=profiles,
        prospects=prospects,
        stats=stats,
        status_filter=status_filter,
        profile_filter=profile_filter,
        search_query=search_query,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@scout_prospector_bp.route('/scout-prospector/profiles/create', methods=['GET', 'POST'])
@login_required
def create_profile():
    if request.method == 'POST':
        service = _get_service()
        name = request.form.get('name', '').strip()
        if not name:
            flash('Profile name is required.', 'danger')
            return redirect(url_for('scout_prospector.create_profile'))

        industries = request.form.getlist('industries')
        company_sizes = request.form.getlist('company_sizes')
        geographies = [g.strip() for g in request.form.get('geographies', '').split(',') if g.strip()]
        job_types = [j.strip() for j in request.form.get('job_types', '').split(',') if j.strip()]
        hiring_signals = request.form.getlist('hiring_signals')

        service.create_profile(
            user=current_user,
            name=name,
            description=request.form.get('description', '').strip() or None,
            industries=industries,
            company_sizes=company_sizes,
            geographies=geographies,
            job_types=job_types,
            hiring_signals=hiring_signals,
            additional_criteria=request.form.get('additional_criteria', '').strip() or None,
        )
        flash(f'Profile "{name}" created successfully.', 'success')
        return redirect(url_for('scout_prospector.prospector_dashboard'))

    return render_template(
        'scout_prospector_profile_form.html',
        active_page='scout_prospector',
        profile=None,
        edit_mode=False,
    )


@scout_prospector_bp.route('/scout-prospector/profiles/<int:profile_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_profile(profile_id):
    service = _get_service()
    profile = service.get_profile(profile_id, current_user)
    if not profile:
        flash('Profile not found.', 'danger')
        return redirect(url_for('scout_prospector.prospector_dashboard'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Profile name is required.', 'danger')
            return redirect(url_for('scout_prospector.edit_profile', profile_id=profile_id))

        industries = request.form.getlist('industries')
        company_sizes = request.form.getlist('company_sizes')
        geographies = [g.strip() for g in request.form.get('geographies', '').split(',') if g.strip()]
        job_types = [j.strip() for j in request.form.get('job_types', '').split(',') if j.strip()]
        hiring_signals = request.form.getlist('hiring_signals')

        service.update_profile(
            profile_id=profile_id,
            user=current_user,
            name=name,
            description=request.form.get('description', '').strip() or None,
            industries=industries,
            company_sizes=company_sizes,
            geographies=geographies,
            job_types=job_types,
            hiring_signals=hiring_signals,
            additional_criteria=request.form.get('additional_criteria', '').strip() or None,
        )
        flash(f'Profile "{name}" updated.', 'success')
        return redirect(url_for('scout_prospector.prospector_dashboard'))

    return render_template(
        'scout_prospector_profile_form.html',
        active_page='scout_prospector',
        profile=profile,
        edit_mode=True,
    )


@scout_prospector_bp.route('/scout-prospector/profiles/<int:profile_id>/delete', methods=['POST'])
@login_required
def delete_profile(profile_id):
    service = _get_service()
    if service.delete_profile(profile_id, current_user):
        flash('Profile deleted.', 'success')
    else:
        flash('Profile not found.', 'danger')
    return redirect(url_for('scout_prospector.prospector_dashboard'))


@scout_prospector_bp.route('/scout-prospector/profiles/<int:profile_id>/research', methods=['POST'])
@login_required
def run_research(profile_id):
    service = _get_service()
    profile = service.get_profile(profile_id, current_user)
    if not profile:
        flash('Profile not found.', 'danger')
        return redirect(url_for('scout_prospector.prospector_dashboard'))

    run = service.run_research(profile, current_user)
    if run.status == 'completed':
        flash(f'Research complete! Found {run.prospects_found} prospects.', 'success')
    else:
        logger.error(f"Research run {run.id} failed: {run.error_message}")
        flash('Research failed. Please try again or adjust your ICP profile.', 'danger')
    return redirect(url_for('scout_prospector.prospector_dashboard'))


@scout_prospector_bp.route('/scout-prospector/prospects/<int:prospect_id>')
@login_required
def prospect_detail(prospect_id):
    service = _get_service()
    prospect = service.get_prospect(prospect_id, current_user)
    if not prospect:
        flash('Prospect not found.', 'danger')
        return redirect(url_for('scout_prospector.prospector_dashboard'))

    return render_template(
        'scout_prospector_detail.html',
        active_page='scout_prospector',
        prospect=prospect,
    )


@scout_prospector_bp.route('/scout-prospector/prospects/<int:prospect_id>/update', methods=['POST'])
@login_required
def update_prospect(prospect_id):
    service = _get_service()
    status = request.form.get('status')
    notes = request.form.get('notes')
    prospect = service.update_prospect(prospect_id, current_user, status=status, notes=notes)
    if prospect:
        flash('Prospect updated.', 'success')
    else:
        flash('Prospect not found.', 'danger')

    if request.form.get('return_to_detail'):
        return redirect(url_for('scout_prospector.prospect_detail', prospect_id=prospect_id))
    return redirect(url_for('scout_prospector.prospector_dashboard'))


@scout_prospector_bp.route('/scout-prospector/prospects/<int:prospect_id>/delete', methods=['POST'])
@login_required
def delete_prospect(prospect_id):
    service = _get_service()
    if service.delete_prospect(prospect_id, current_user):
        flash('Prospect removed.', 'success')
    else:
        flash('Prospect not found.', 'danger')
    return redirect(url_for('scout_prospector.prospector_dashboard'))


@scout_prospector_bp.route('/scout-prospector/export')
@login_required
def export_csv():
    service = _get_service()
    profile_filter = request.args.get('profile', '')
    status_filter = request.args.get('status', '')
    try:
        profile_id = int(profile_filter) if profile_filter else None
    except (ValueError, TypeError):
        profile_id = None

    csv_data = service.export_prospects_csv(
        current_user,
        profile_id=profile_id,
        status=status_filter if status_filter and status_filter != 'all' else None,
    )

    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    filename = f'scout_prospector_export_{timestamp}.csv'

    return Response(
        csv_data,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )


@scout_prospector_bp.route('/api/scout-prospector/prospects/<int:prospect_id>/status', methods=['POST'])
@login_required
def api_update_status(prospect_id):
    service = _get_service()
    data = request.get_json()
    if not data or 'status' not in data:
        return jsonify({'error': 'Status required'}), 400

    prospect = service.update_prospect(prospect_id, current_user, status=data['status'])
    if prospect:
        return jsonify({'success': True, 'status': prospect.status})
    return jsonify({'error': 'Prospect not found'}), 404


@scout_prospector_bp.route('/api/scout-prospector/prospects/<int:prospect_id>/notes', methods=['POST'])
@login_required
def api_update_notes(prospect_id):
    service = _get_service()
    data = request.get_json()
    if data is None:
        return jsonify({'error': 'Invalid request'}), 400

    prospect = service.update_prospect(prospect_id, current_user, notes=data.get('notes', ''))
    if prospect:
        return jsonify({'success': True})
    return jsonify({'error': 'Prospect not found'}), 404
