import json
import logging
from datetime import datetime

from flask import Blueprint, render_template, jsonify, request, redirect, url_for, flash
from flask_login import login_required, current_user
from extensions import db
from routes import register_module_guard

knowledge_hub_bp = Blueprint('knowledge_hub', __name__)
register_module_guard(knowledge_hub_bp, 'scout_support')
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {'pdf', 'docx', 'doc', 'txt'}

def _get_onedrive_status():
    try:
        from onedrive_service import OneDriveService
        svc = OneDriveService()
        connected = svc.is_connected()
        user_info = svc.get_user_info() if connected else None
        return {"connected": connected, "user": user_info}
    except Exception:
        return {"connected": False, "user": None}


def _allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@knowledge_hub_bp.route('/scout-support/knowledge')
@login_required
def knowledge_hub():
    if not current_user.is_admin:
        return redirect(url_for('scout_support.scout_support_dashboard'))

    from models import KnowledgeDocument
    from scout_support.knowledge import KnowledgeService, KNOWLEDGE_CATEGORIES

    doc_type_filter = request.args.get('type', 'all')
    category_filter = request.args.get('category', 'all')

    query = KnowledgeDocument.query.filter(KnowledgeDocument.status != 'deleted')

    if doc_type_filter != 'all':
        query = query.filter_by(doc_type=doc_type_filter)
    if category_filter != 'all':
        query = query.filter_by(category=category_filter)

    documents = query.order_by(KnowledgeDocument.created_at.desc()).all()

    ks = KnowledgeService()
    stats = ks.get_stats()

    from models import OneDriveSyncFolder
    onedrive_status = _get_onedrive_status()
    sync_folders = OneDriveSyncFolder.query.order_by(OneDriveSyncFolder.created_at.desc()).all()
    onedrive_doc_count = KnowledgeDocument.query.filter_by(doc_type='onedrive_sync', status='active').count()

    return render_template('knowledge_hub.html',
                           documents=documents,
                           stats=stats,
                           categories=KNOWLEDGE_CATEGORIES,
                           doc_type_filter=doc_type_filter,
                           category_filter=category_filter,
                           onedrive_status=onedrive_status,
                           sync_folders=sync_folders,
                           onedrive_doc_count=onedrive_doc_count,
                           active_page='scout_support_knowledge')


@knowledge_hub_bp.route('/scout-support/knowledge/upload', methods=['POST'])
@login_required
def upload_document():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from scout_support.knowledge import KnowledgeService

    if 'file' not in request.files:
        return jsonify({'error': 'No file selected'}), 400

    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'error': 'No file selected'}), 400

    if not _allowed_file(file.filename):
        return jsonify({'error': f'File type not supported. Allowed: {", ".join(ALLOWED_EXTENSIONS)}'}), 400

    title = request.form.get('title', '').strip()
    if not title:
        title = file.filename.rsplit('.', 1)[0]

    category = request.form.get('category', 'other')
    description = request.form.get('description', '').strip()

    ks = KnowledgeService()
    doc = ks.process_uploaded_document(
        title=title,
        file_storage=file,
        category=category,
        description=description,
        uploaded_by=current_user.email,
    )

    if doc:
        entry_count = doc.entries.count()
        return jsonify({
            'success': True,
            'message': f'Document "{doc.title}" processed successfully ({entry_count} knowledge chunks created)',
            'document_id': doc.id,
        })
    else:
        return jsonify({'error': 'Failed to process document. The file may be empty or unreadable.'}), 400


@knowledge_hub_bp.route('/scout-support/knowledge/<int:doc_id>')
@login_required
def document_detail(doc_id):
    if not current_user.is_admin:
        return redirect(url_for('scout_support.scout_support_dashboard'))

    from models import KnowledgeDocument
    from scout_support.knowledge import KNOWLEDGE_CATEGORIES

    doc = KnowledgeDocument.query.get_or_404(doc_id)
    entries = doc.entries.order_by(db.text('chunk_index ASC')).all()

    return render_template('knowledge_document.html',
                           document=doc,
                           entries=entries,
                           categories=KNOWLEDGE_CATEGORIES,
                           active_page='scout_support_knowledge')


@knowledge_hub_bp.route('/scout-support/knowledge/<int:doc_id>/delete', methods=['POST'])
@login_required
def delete_document(doc_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from scout_support.knowledge import KnowledgeService

    ks = KnowledgeService()
    success = ks.delete_document(doc_id)

    if success:
        return jsonify({'success': True, 'message': 'Document deleted'})
    else:
        return jsonify({'error': 'Document not found'}), 404


@knowledge_hub_bp.route('/scout-support/knowledge/search')
@login_required
def search_knowledge():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    query = request.args.get('q', '').strip()
    if not query or len(query) < 3:
        return jsonify({'results': [], 'message': 'Query must be at least 3 characters'})

    from scout_support.knowledge import KnowledgeService

    ks = KnowledgeService()
    results = ks.retrieve_relevant_knowledge(query, top_k=10, threshold=0.20)

    return jsonify({'results': results, 'query': query})


@knowledge_hub_bp.route('/scout-support/knowledge/learn-from-tickets', methods=['POST'])
@login_required
def learn_from_tickets():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from models import SupportTicket, KnowledgeDocument
    from scout_support.knowledge import KnowledgeService

    already_learned_ids = db.session.query(KnowledgeDocument.source_ticket_id).filter(
        KnowledgeDocument.source_ticket_id.isnot(None),
        KnowledgeDocument.doc_type == 'ticket_resolution'
    ).subquery()
    completed_tickets = SupportTicket.query.filter(
        SupportTicket.status.in_(['completed']),
        ~SupportTicket.id.in_(already_learned_ids)
    ).all()

    already_escalation_ids = db.session.query(KnowledgeDocument.source_ticket_id).filter(
        KnowledgeDocument.source_ticket_id.isnot(None),
        KnowledgeDocument.doc_type == 'ticket_escalation'
    ).subquery()
    escalated_tickets = SupportTicket.query.filter(
        SupportTicket.status.in_(['escalated', 'execution_failed', 'admin_handling', 'closed']),
        ~SupportTicket.id.in_(already_escalation_ids)
    ).all()

    ks = KnowledgeService()
    learned_success = 0
    learned_failure = 0

    for ticket in completed_tickets:
        doc = ks.learn_from_ticket(ticket.id)
        if doc:
            learned_success += 1

    for ticket in escalated_tickets:
        doc = ks.learn_from_escalation(ticket.id)
        if doc:
            learned_failure += 1

    return jsonify({
        'success': True,
        'learned': learned_success + learned_failure,
        'learned_success': learned_success,
        'learned_failure': learned_failure,
        'message': f'Learned from {learned_success} resolved and {learned_failure} escalated ticket(s)',
    })


@knowledge_hub_bp.route('/scout-support/knowledge/onedrive/browse')
@login_required
def onedrive_browse():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    folder_id = request.args.get('folder_id', '')

    try:
        from onedrive_service import OneDriveService
        svc = OneDriveService()

        if folder_id:
            items = svc.list_folder_items(folder_id)
            folder_path = svc.get_folder_path(folder_id)
        else:
            items = svc.list_root_items()
            folder_path = "/"

        folders = [i for i in items if i['is_folder']]
        files = [i for i in items if not i['is_folder']]

        return jsonify({
            'success': True,
            'folder_id': folder_id or 'root',
            'folder_path': folder_path,
            'folders': folders,
            'files': files,
        })
    except ConnectionError as e:
        return jsonify({'error': str(e)}), 503
    except Exception as e:
        logger.error(f"OneDrive browse error: {e}")
        return jsonify({'error': 'Failed to browse OneDrive'}), 500


@knowledge_hub_bp.route('/scout-support/knowledge/onedrive/add-folder', methods=['POST'])
@login_required
def onedrive_add_folder():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json()
    folder_id = data.get('folder_id', '').strip()
    folder_name = data.get('folder_name', '').strip()

    if not folder_id or not folder_name:
        return jsonify({'error': 'Folder ID and name are required'}), 400

    from models import OneDriveSyncFolder
    existing = OneDriveSyncFolder.query.filter_by(onedrive_folder_id=folder_id).first()
    if existing:
        if not existing.sync_enabled:
            existing.sync_enabled = True
            db.session.commit()
            return jsonify({'success': True, 'message': f'Re-enabled sync for "{folder_name}"'})
        return jsonify({'error': f'Folder "{folder_name}" is already being synced'}), 409

    try:
        from onedrive_service import OneDriveService
        svc = OneDriveService()
        folder_path = svc.get_folder_path(folder_id)
    except Exception:
        folder_path = f"/{folder_name}"

    sync_folder = OneDriveSyncFolder(
        onedrive_folder_id=folder_id,
        folder_name=folder_name,
        folder_path=folder_path,
        sync_enabled=True,
        added_by=current_user.email,
    )
    db.session.add(sync_folder)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'Added "{folder_name}" for syncing. Click "Sync Now" to import documents.',
        'folder': {
            'id': sync_folder.id,
            'folder_name': folder_name,
            'folder_path': folder_path,
        },
    })


@knowledge_hub_bp.route('/scout-support/knowledge/onedrive/remove-folder', methods=['POST'])
@login_required
def onedrive_remove_folder():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    data = request.get_json()
    folder_id = data.get('folder_id', '').strip()

    if not folder_id:
        return jsonify({'error': 'Folder ID is required'}), 400

    from models import OneDriveSyncFolder
    sync_folder = OneDriveSyncFolder.query.filter_by(onedrive_folder_id=folder_id).first()
    if not sync_folder:
        return jsonify({'error': 'Sync folder not found'}), 404

    sync_folder.sync_enabled = False
    db.session.commit()

    return jsonify({'success': True, 'message': f'Stopped syncing "{sync_folder.folder_name}"'})


_onedrive_sync_status = {"running": False, "last_result": None, "started_at": None}

@knowledge_hub_bp.route('/scout-support/knowledge/onedrive/sync', methods=['POST'])
@login_required
def onedrive_sync():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    if _onedrive_sync_status["running"]:
        return jsonify({'success': True, 'message': 'Sync is already in progress...', 'status': 'running'})

    data = request.get_json() or {}
    folder_id = data.get('folder_id', '').strip()
    force_resync = data.get('force_resync', False)

    try:
        from onedrive_service import OneDriveService
        svc = OneDriveService()
        if not svc.is_connected():
            return jsonify({'error': 'OneDrive is not connected'}), 503
    except ConnectionError as e:
        return jsonify({'error': str(e)}), 503
    except Exception as e:
        logger.error(f"OneDrive sync connection check error: {e}")
        return jsonify({'error': f'Connection check failed: {str(e)}'}), 500

    if force_resync:
        try:
            from extensions import db
            from models import KnowledgeDocument
            cleared = KnowledgeDocument.query.filter(
                KnowledgeDocument.doc_type == 'onedrive_sync',
                KnowledgeDocument.status == 'active',
                KnowledgeDocument.onedrive_etag.isnot(None),
            ).update({'onedrive_etag': None})
            db.session.commit()
            logger.info(f"Force re-sync: cleared etags on {cleared} OneDrive documents")
        except Exception as e:
            logger.error(f"Force re-sync etag clear failed: {e}")

    import threading
    from flask import current_app

    app = current_app._get_current_object()

    def run_sync():
        _onedrive_sync_status["running"] = True
        _onedrive_sync_status["started_at"] = datetime.utcnow().isoformat()
        try:
            with app.app_context():
                from onedrive_service import OneDriveService
                sync_svc = OneDriveService()
                if folder_id:
                    result = sync_svc.sync_folder_to_knowledge(folder_id)
                else:
                    result = sync_svc.sync_all_folders()
                _onedrive_sync_status["last_result"] = result
                logger.info(f"OneDrive background sync complete: {result}")
        except Exception as e:
            logger.error(f"OneDrive background sync error: {e}")
            _onedrive_sync_status["last_result"] = {"error": str(e)}
        finally:
            _onedrive_sync_status["running"] = False

    thread = threading.Thread(target=run_sync, daemon=True)
    thread.start()

    return jsonify({'success': True, 'message': 'Sync started in the background. Refresh the page in a moment to see results.', 'status': 'started'})


@knowledge_hub_bp.route('/scout-support/knowledge/onedrive/sync-status')
@login_required
def onedrive_sync_status():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    return jsonify({
        'running': _onedrive_sync_status['running'],
        'started_at': _onedrive_sync_status['started_at'],
        'last_result': _onedrive_sync_status['last_result'],
    })


@knowledge_hub_bp.route('/scout-support/knowledge/onedrive/status')
@login_required
def onedrive_status():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    status = _get_onedrive_status()
    from models import OneDriveSyncFolder
    folders = OneDriveSyncFolder.query.filter_by(sync_enabled=True).all()

    return jsonify({
        'connected': status['connected'],
        'user': status['user'],
        'sync_folders': [{
            'id': f.id,
            'folder_name': f.folder_name,
            'folder_path': f.folder_path,
            'last_synced_at': f.last_synced_at.isoformat() if f.last_synced_at else None,
            'last_sync_files': f.last_sync_files or 0,
        } for f in folders],
    })
