"""AI Cost dashboard + Module-Based Forecaster + Embedding A/B Analysis.

Super-admin-only. Three routes:
  /admin/ai-cost                — per-site spend breakdown over a window
  /admin/ai-cost/forecast       — module-based monthly cost projector
  /admin/ai-cost/embedding-ab   — shadow-mode embedding A/B analysis (S3 Phase A)

The first two read from `openai_call_log`. The forecaster additionally
reads/writes `cost_forecast_override` and `cost_forecast_scenario`.
The A/B page reads from `embedding_ab_log`.
"""
import logging
import math
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
        active_page='ai_cost',
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
        active_page='ai_cost',
        window_days=window_days,
        module_rows=module_rows,
        projection=projection,
        flash_message=flash_message,
        saved_scenarios=saved_scenarios,
    )


# ============================================================================
# Embedding Model A/B Analysis (S3 Phase A — May 2026)
# ============================================================================

# Threshold sweep grid for the analysis page.
_THRESHOLD_SWEEP = [0.15, 0.18, 0.20, 0.22, 0.25, 0.28, 0.30, 0.35]

# Verdict thresholds.
_CUTOVER_CONCORDANCE_PASS = 95.0
_CUTOVER_FN_PASS = 2.0


def _pearson(xs, ys):
    """Pearson correlation. Returns 0.0 on degenerate input."""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return num / (dx * dy)


def _build_threshold_sweep(rows):
    """For each threshold in _THRESHOLD_SWEEP, recompute concordance / FN / FP.
    `rows` is a list of dicts with primary_score, shadow_score, primary_passed.
    """
    out = []
    if not rows:
        return out
    n = len(rows)
    best_idx = -1
    best_concordance = -1.0
    for i, t in enumerate(_THRESHOLD_SWEEP):
        agree = fn = fp = shadow_pass = 0
        for r in rows:
            shadow_pass_at_t = r['shadow_score'] >= t
            primary_pass = r['primary_passed']
            if shadow_pass_at_t == primary_pass:
                agree += 1
            elif primary_pass and not shadow_pass_at_t:
                fn += 1
            else:
                fp += 1
            if shadow_pass_at_t:
                shadow_pass += 1
        concordance = (agree / n) * 100.0
        fn_pct = (fn / n) * 100.0
        fp_pct = (fp / n) * 100.0
        # Recommendation policy: ONLY mark recommended when FN ≤ 2%
        # (the cutover safety policy). Among qualifying thresholds, pick
        # the highest concordance. If no threshold meets the FN floor, no
        # row is marked recommended — operators must conclude the small
        # model is too divergent at any threshold in the sweep.
        if fn_pct <= _CUTOVER_FN_PASS and concordance > best_concordance:
            best_concordance = concordance
            best_idx = i
        out.append({
            'threshold': t,
            'concordance_pct': concordance,
            'false_negative_pct': fn_pct,
            'false_positive_pct': fp_pct,
            'shadow_pass_pct': (shadow_pass / n) * 100.0,
            'recommended': False,
        })
    if best_idx >= 0:
        out[best_idx]['recommended'] = True
    return out


@ai_cost_bp.route('/embedding-ab', methods=['GET'])
@login_required
def embedding_ab_analysis():
    _require_admin()

    try:
        hours = int(request.args.get('hours', '48'))
    except (TypeError, ValueError):
        hours = 48
    hours = max(1, min(hours, 24 * 30))
    since = datetime.utcnow() - timedelta(hours=hours)

    # Pull raw rows in the window.
    rows = []
    primary_model = ''
    shadow_model = ''
    try:
        result = db.session.execute(
            text(
                "SELECT primary_model, shadow_model, primary_score, shadow_score, "
                "       threshold_used, primary_passed, shadow_would_pass, "
                "       bullhorn_candidate_id, candidate_name, "
                "       bullhorn_job_id, job_title, created_at "
                "FROM embedding_ab_log "
                "WHERE created_at >= :since "
                "ORDER BY created_at DESC"
            ),
            {'since': since},
        ).fetchall()
        for r in result:
            rows.append({
                'primary_model': r[0],
                'shadow_model': r[1],
                'primary_score': float(r[2] or 0.0),
                'shadow_score': float(r[3] or 0.0),
                'threshold_used': float(r[4] or 0.0),
                'primary_passed': bool(r[5]),
                'shadow_would_pass': bool(r[6]),
                'bullhorn_candidate_id': r[7],
                'candidate_name': r[8],
                'bullhorn_job_id': r[9],
                'job_title': r[10],
                'created_at': r[11],
            })
        if rows:
            primary_model = rows[0]['primary_model']
            shadow_model = rows[0]['shadow_model']
    except Exception as exc:
        logger.warning(f"embedding_ab query failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass

    total_pairs = len(rows)
    both_pass = sum(1 for r in rows if r['primary_passed'] and r['shadow_would_pass'])
    both_block = sum(1 for r in rows if not r['primary_passed'] and not r['shadow_would_pass'])
    false_negatives = sum(1 for r in rows if r['primary_passed'] and not r['shadow_would_pass'])
    false_positives = sum(1 for r in rows if not r['primary_passed'] and r['shadow_would_pass'])

    if total_pairs > 0:
        concordance_pct = ((both_pass + both_block) / total_pairs) * 100.0
        false_negative_pct = (false_negatives / total_pairs) * 100.0
        false_positive_pct = (false_positives / total_pairs) * 100.0
        avg_primary = sum(r['primary_score'] for r in rows) / total_pairs
        avg_shadow = sum(r['shadow_score'] for r in rows) / total_pairs
        avg_delta = avg_shadow - avg_primary
        pearson_r = _pearson(
            [r['primary_score'] for r in rows],
            [r['shadow_score'] for r in rows],
        )
    else:
        concordance_pct = false_negative_pct = false_positive_pct = 0.0
        avg_primary = avg_shadow = avg_delta = pearson_r = 0.0

    # Verdict
    if total_pairs == 0:
        verdict_class, verdict_icon, verdict_text = 'secondary', 'info-circle', 'No data yet.'
    elif concordance_pct >= _CUTOVER_CONCORDANCE_PASS and false_negative_pct <= _CUTOVER_FN_PASS:
        verdict_class, verdict_icon = 'success', 'check-circle'
        verdict_text = (f"Cut over recommended. Concordance {concordance_pct:.1f}% "
                        f"(target ≥ {_CUTOVER_CONCORDANCE_PASS}%) and false-negatives "
                        f"{false_negative_pct:.1f}% (target ≤ {_CUTOVER_FN_PASS}%) both pass.")
    elif concordance_pct >= 90.0:
        verdict_class, verdict_icon = 'warning', 'exclamation-triangle'
        verdict_text = (f"Threshold tuning recommended. Concordance is {concordance_pct:.1f}% — "
                        f"check the sweep table below for a better shadow threshold before deciding.")
    else:
        verdict_class, verdict_icon = 'danger', 'times-circle'
        verdict_text = (f"Cutover NOT recommended at this time. Concordance {concordance_pct:.1f}% "
                        f"is below the 90% safety floor; the small model is too divergent.")

    # Top false-negative pairs (limit 25 by descending primary score)
    flagged_pairs = sorted(
        [r for r in rows if r['primary_passed'] and not r['shadow_would_pass']],
        key=lambda r: r['primary_score'],
        reverse=True,
    )[:25]

    threshold_sweep = _build_threshold_sweep(rows)

    return render_template(
        'admin_ai_cost_embedding_ab.html',
        active_page='ai_cost',
        hours=hours,
        since_iso=since.isoformat() + 'Z',
        primary_model=primary_model,
        shadow_model=shadow_model,
        total_pairs=total_pairs,
        both_pass=both_pass,
        both_block=both_block,
        false_negatives=false_negatives,
        false_positives=false_positives,
        concordance_pct=concordance_pct,
        false_negative_pct=false_negative_pct,
        false_positive_pct=false_positive_pct,
        avg_primary=avg_primary,
        avg_shadow=avg_shadow,
        avg_delta=avg_delta,
        pearson_r=pearson_r,
        verdict_class=verdict_class,
        verdict_icon=verdict_icon,
        verdict_text=verdict_text,
        flagged_pairs=flagged_pairs,
        threshold_sweep=threshold_sweep,
    )
