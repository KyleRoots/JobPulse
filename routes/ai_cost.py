"""AI Cost dashboard — per-site OpenAI spend breakdown.

Super-admin-only. Reads from `openai_call_log`. Surfaces the call-site
ranking, model tier in use, and aggregate spend over the selected
window so we can validate Phase 1 downgrades and target the next phase.
"""
import logging
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, abort
from flask_login import login_required, current_user
from sqlalchemy import text

from extensions import db

logger = logging.getLogger(__name__)

ai_cost_bp = Blueprint('ai_cost', __name__, url_prefix='/admin/ai-cost')


def _require_admin():
    if not current_user.is_authenticated or not current_user.is_admin:
        abort(403)


@ai_cost_bp.route('/', methods=['GET'])
@login_required
def ai_cost_dashboard():
    _require_admin()

    try:
        hours = int(request.args.get('hours', '24'))
    except (TypeError, ValueError):
        hours = 24
    hours = max(1, min(hours, 24 * 30))

    since = datetime.utcnow() - timedelta(hours=hours)

    try:
        rows = db.session.execute(
            text(
                "SELECT call_site_id, model, "
                "       COUNT(*) AS calls, "
                "       COALESCE(SUM(input_tokens), 0) AS in_tok, "
                "       COALESCE(SUM(output_tokens), 0) AS out_tok, "
                "       COALESCE(SUM(estimated_cost_usd), 0) AS cost "
                "FROM openai_call_log "
                "WHERE created_at >= :since "
                "GROUP BY call_site_id, model "
                "ORDER BY cost DESC"
            ),
            {'since': since},
        ).fetchall()
    except Exception as exc:
        logger.warning(f"AI cost query failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass
        rows = []

    breakdown = []
    total_cost = 0.0
    total_calls = 0
    total_tokens = 0
    for r in rows:
        cost = float(r[5] or 0)
        calls = int(r[2] or 0)
        in_tok = int(r[3] or 0)
        out_tok = int(r[4] or 0)
        breakdown.append({
            'call_site_id': r[0],
            'model': r[1],
            'calls': calls,
            'input_tokens': in_tok,
            'output_tokens': out_tok,
            'tokens': in_tok + out_tok,
            'cost': cost,
            'avg_cost': (cost / calls) if calls else 0.0,
        })
        total_cost += cost
        total_calls += calls
        total_tokens += in_tok + out_tok

    # 7-day daily trend (always rendered, regardless of selected window)
    trend_since = datetime.utcnow() - timedelta(days=7)
    trend = []
    try:
        trend_rows = db.session.execute(
            text(
                "SELECT DATE(created_at) AS day, "
                "       COUNT(*) AS calls, "
                "       COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens, "
                "       COALESCE(SUM(estimated_cost_usd), 0) AS cost, "
                "       COALESCE(SUM(CASE WHEN success = FALSE THEN 1 ELSE 0 END), 0) AS failures "
                "FROM openai_call_log "
                "WHERE created_at >= :since "
                "GROUP BY DATE(created_at) "
                "ORDER BY day DESC"
            ),
            {'since': trend_since},
        ).fetchall()
        for r in trend_rows:
            trend.append({
                'day': r[0].isoformat() if r[0] else '',
                'calls': int(r[1] or 0),
                'tokens': int(r[2] or 0),
                'cost': float(r[3] or 0),
                'failures': int(r[4] or 0),
            })
    except Exception as exc:
        logger.warning(f"AI cost trend query failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass

    return render_template(
        'admin_ai_cost.html',
        active_page='admin_health',
        hours=hours,
        since_iso=since.isoformat() + 'Z',
        breakdown=breakdown,
        total_cost=total_cost,
        total_calls=total_calls,
        total_tokens=total_tokens,
        trend=trend,
    )
