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

    return render_template('knowledge_hub.html',
                           documents=documents,
                           stats=stats,
                           categories=KNOWLEDGE_CATEGORIES,
                           doc_type_filter=doc_type_filter,
                           category_filter=category_filter,
                           active_page='knowledge_hub')


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
                           active_page='knowledge_hub')


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

    completed_tickets = SupportTicket.query.filter(
        SupportTicket.status.in_(['completed']),
        ~SupportTicket.id.in_(
            db.session.query(KnowledgeDocument.source_ticket_id).filter(
                KnowledgeDocument.source_ticket_id.isnot(None)
            )
        )
    ).all()

    ks = KnowledgeService()
    learned = 0
    for ticket in completed_tickets:
        doc = ks.learn_from_ticket(ticket.id)
        if doc:
            learned += 1

    return jsonify({
        'success': True,
        'learned': learned,
        'message': f'Learned from {learned} resolved ticket(s)',
    })
