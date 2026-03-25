import json
import logging
from flask import Blueprint, render_template, redirect, url_for, jsonify, request
from flask_login import login_required, current_user
from extensions import db

platform_support_bp = Blueprint('platform_support', __name__)
logger = logging.getLogger(__name__)


@platform_support_bp.route('/my-tickets')
@login_required
def my_tickets():
    from models import SupportTicket
    from scout_support_service import CATEGORY_LABELS, PLATFORM_CATEGORIES

    all_user_tickets = SupportTicket.query.filter_by(
        submitter_email=current_user.email
    ).order_by(SupportTicket.created_at.desc()).all()

    has_support_module = current_user.is_admin or current_user.has_module('scout_support')

    if has_support_module:
        tickets = all_user_tickets
    else:
        tickets = [t for t in all_user_tickets if t.category in PLATFORM_CATEGORIES]

    platform_tickets = [t for t in tickets if t.category in PLATFORM_CATEGORIES]
    ats_tickets = [t for t in tickets if t.category not in PLATFORM_CATEGORIES]

    stats = {
        'total': len(tickets),
        'platform': len(platform_tickets),
        'ats': len(ats_tickets),
        'open': sum(1 for t in tickets if t.status not in ('completed', 'closed')),
    }

    return render_template('my_tickets.html',
                           tickets=tickets,
                           stats=stats,
                           category_labels=CATEGORY_LABELS,
                           platform_categories=PLATFORM_CATEGORIES,
                           has_support_module=has_support_module,
                           active_page='my_tickets')


@platform_support_bp.route('/my-tickets/<ticket_number>')
@login_required
def my_ticket_detail(ticket_number):
    from models import SupportTicket
    from scout_support_service import PLATFORM_CATEGORIES

    ticket = SupportTicket.query.filter_by(ticket_number=ticket_number).first_or_404()

    if ticket.submitter_email != current_user.email and not current_user.is_admin:
        return redirect(url_for('platform_support.my_tickets'))

    has_support_module = current_user.is_admin or current_user.has_module('scout_support')
    if not has_support_module and ticket.category not in PLATFORM_CATEGORIES:
        return redirect(url_for('platform_support.my_tickets'))

    conversations = ticket.conversations.order_by(db.text('created_at ASC')).all()

    ai_understanding = None
    if ticket.ai_understanding:
        try:
            ai_understanding = json.loads(ticket.ai_understanding)
        except (json.JSONDecodeError, TypeError):
            ai_understanding = {'understanding': ticket.ai_understanding}

    return render_template('my_ticket_detail.html',
                           ticket=ticket,
                           conversations=conversations,
                           ai_understanding=ai_understanding,
                           active_page='my_tickets')
