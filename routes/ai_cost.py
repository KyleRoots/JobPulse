"""AI Cost dashboard + Module-Based Forecaster.

Super-admin-only. Two routes:
  /admin/ai-cost            — per-site spend breakdown over a window
  /admin/ai-cost/forecast   — module-based monthly cost projector

Both read from `openai_call_log`. The forecaster additionally reads/writes
`cost_forecast_override` and `cost_forecast_scenario`.
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, render_template, request, abort, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import text

from extensions import db
from services.cost_forecaster import (
    MODULE_DEFINITIONS,
    derive_unit_costs,
    project_monthly_cost,
)

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


def _load_overrides() -> dict[str, float]:
    """Return {module_key: unit_cost_usd} from cost_forecast_override."""
    out: dict[str, float] = {}
    try:
        rows = db.session.execute(
            text("SELECT module_key, unit_cost_usd FROM cost_forecast_override")
        ).fetchall()
        for r in rows:
            try:
                out[r[0]] = float(r[1])
            except (TypeError, ValueError):
                continue
    except Exception as exc:
        logger.warning(f"forecaster: load_overrides failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass
    return out


def _save_override(module_key: str, unit_cost: float, user: str) -> None:
    """Upsert a single override (Postgres ON CONFLICT)."""
    try:
        db.session.execute(
            text(
                "INSERT INTO cost_forecast_override (module_key, unit_cost_usd, updated_at, updated_by) "
                "VALUES (:k, :c, :t, :u) "
                "ON CONFLICT (module_key) DO UPDATE SET "
                "unit_cost_usd = EXCLUDED.unit_cost_usd, "
                "updated_at = EXCLUDED.updated_at, "
                "updated_by = EXCLUDED.updated_by"
            ),
            {'k': module_key, 'c': Decimal(f'{unit_cost:.6f}'),
             't': datetime.utcnow(), 'u': (user or '')[:80]},
        )
        db.session.commit()
    except Exception as exc:
        logger.warning(f"forecaster: save_override({module_key}) failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass


def _parse_form_state(form) -> tuple[dict[str, int], dict[str, float | None]]:
    """Return (active_volumes, override_inputs) from POST form."""
    active: dict[str, int] = {}
    overrides: dict[str, float | None] = {}
    for m in MODULE_DEFINITIONS:
        if form.get(f'active_{m.key}') == '1':
            try:
                vol = int(form.get(f'volume_{m.key}', '0') or 0)
            except (TypeError, ValueError):
                vol = 0
            active[m.key] = max(0, vol)
        raw_ov = form.get(f'override_{m.key}', '')
        if raw_ov is not None and str(raw_ov).strip() != '':
            try:
                overrides[m.key] = float(raw_ov)
            except (TypeError, ValueError):
                overrides[m.key] = None
        else:
            overrides[m.key] = None
    return active, overrides


@ai_cost_bp.route('/forecast', methods=['GET', 'POST'])
@login_required
def ai_cost_forecast():
    _require_admin()

    # Telemetry window for unit-cost derivation.
    try:
        window_days = int(request.values.get('window', '14'))
    except (TypeError, ValueError):
        window_days = 14
    window_days = max(1, min(window_days, 90))

    unit_costs = derive_unit_costs(window_days=window_days)
    saved_overrides = _load_overrides()

    # GET: prefill volumes from a saved scenario if requested, else 0/off.
    flash_message = None
    projection = None
    user_overrides_form: dict[str, float | None] = {k: None for k in (m.key for m in MODULE_DEFINITIONS)}
    active_state: dict[str, int] = {}

    if request.method == 'POST':
        active_state, user_overrides_form = _parse_form_state(request.form)

        # Save overrides if requested — persist the non-empty entries.
        if request.form.get('save_overrides') == '1':
            user_label = getattr(current_user, 'username', '') or ''
            for k, v in user_overrides_form.items():
                if v is not None and v >= 0:
                    _save_override(k, v, user_label)
            saved_overrides = _load_overrides()
            flash_message = 'Overrides saved.'

        # Save scenario if a name was supplied + "save_scenario" pressed.
        if request.form.get('save_scenario') == '1':
            name = (request.form.get('save_scenario_name') or '').strip()[:120]
            if name and active_state:
                try:
                    user_label = getattr(current_user, 'username', '') or ''
                    db.session.execute(
                        text(
                            "INSERT INTO cost_forecast_scenario "
                            "(name, payload, created_at, created_by) "
                            "VALUES (:n, CAST(:p AS JSON), :t, :u) "
                            "ON CONFLICT (name) DO UPDATE SET "
                            "payload = EXCLUDED.payload, created_at = EXCLUDED.created_at, "
                            "created_by = EXCLUDED.created_by"
                        ),
                        {
                            'n': name,
                            'p': __import__('json').dumps(active_state),
                            't': datetime.utcnow(),
                            'u': user_label[:80],
                        },
                    )
                    db.session.commit()
                    flash_message = (flash_message + ' ' if flash_message else '') + f'Scenario "{name}" saved.'
                except Exception as exc:
                    logger.warning(f"forecaster: save_scenario failed: {exc}")
                    try:
                        db.session.rollback()
                    except Exception:
                        pass

        # Effective overrides = form values where present, else saved.
        effective_overrides = {
            k: (v if v is not None else saved_overrides.get(k))
            for k, v in user_overrides_form.items()
        }
        # Strip Nones for the projection call.
        effective_overrides_clean = {k: v for k, v in effective_overrides.items() if v is not None}
        projection = project_monthly_cost(active_state, unit_costs, effective_overrides_clean)
    else:
        # GET: optionally load a saved scenario.
        scenario_id = request.args.get('scenario')
        if scenario_id:
            try:
                row = db.session.execute(
                    text("SELECT name, payload FROM cost_forecast_scenario WHERE id = :i"),
                    {'i': int(scenario_id)},
                ).fetchone()
                if row and row[1]:
                    payload = row[1] if isinstance(row[1], dict) else __import__('json').loads(row[1])
                    for k, v in payload.items():
                        try:
                            active_state[k] = int(v)
                        except (TypeError, ValueError):
                            continue
                    flash_message = f'Loaded scenario "{row[0]}".'
            except Exception as exc:
                logger.warning(f"forecaster: load scenario failed: {exc}")
                try:
                    db.session.rollback()
                except Exception:
                    pass

    # Build per-module rows for the form.
    module_rows = []
    for m in MODULE_DEFINITIONS:
        derived = unit_costs.get(m.key, {}) or {}
        form_override = user_overrides_form.get(m.key)
        saved_override = saved_overrides.get(m.key)
        # Display priority: form input > saved override > derived
        if form_override is not None:
            display_override = form_override
            effective_source = 'override'
        elif saved_override is not None:
            display_override = saved_override
            effective_source = 'override'
        else:
            display_override = None
            effective_source = derived.get('confidence', 'insufficient_data')
        module_rows.append({
            'key': m.key,
            'label': m.label,
            'unit': m.unit,
            'notes': m.notes,
            'active': bool(active_state.get(m.key, 0) > 0),
            'volume': int(active_state.get(m.key, 0)),
            'derived_unit_cost': float(derived.get('unit_cost_usd') or 0.0),
            'sample_size': int(derived.get('sample_size') or 0),
            'confidence': derived.get('confidence', 'insufficient_data'),
            'override_unit_cost': display_override,
            'effective_source': effective_source,
        })

    # Saved scenarios for the quick-load buttons.
    saved_scenarios = []
    try:
        srows = db.session.execute(
            text("SELECT id, name FROM cost_forecast_scenario ORDER BY created_at DESC LIMIT 12")
        ).fetchall()
        saved_scenarios = [{'id': r[0], 'name': r[1]} for r in srows]
    except Exception as exc:
        logger.warning(f"forecaster: load scenarios failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass

    return render_template(
        'admin_ai_cost_forecast.html',
        active_page='admin_health',
        window_days=window_days,
        module_rows=module_rows,
        projection=projection,
        flash_message=flash_message,
        saved_scenarios=saved_scenarios,
    )
