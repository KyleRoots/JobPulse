"""AI Cost dashboard + Module-Based Forecaster + A/B Analyses.

Super-admin-only. Four routes:
  /admin/ai-cost                — per-site spend breakdown over a window
  /admin/ai-cost/forecast       — module-based monthly cost projector
  /admin/ai-cost/embedding-ab   — shadow-mode embedding A/B analysis (S3 Phase A)
  /admin/ai-cost/screening-ab   — shadow-mode screening A/B analysis (S2 Phase A)

The first two read from `openai_call_log`. The forecaster additionally
reads/writes `cost_forecast_override` and `cost_forecast_scenario`.
The embedding A/B page reads from `embedding_ab_log`. The screening
A/B page reads from `screening_ab_log`.
"""
import logging
import math
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, render_template, request, abort, redirect, url_for, flash
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


# --- Quality Auditor Dashboard (Task 1 — Auditor Visibility) ----------------

_AUDIT_DEFAULT_THRESHOLD = 80.0


def _resolve_audit_threshold() -> float:
    """Pull the global match threshold so flip-rate math uses the live value."""
    try:
        from models import VettingConfig
        raw = VettingConfig.get_value('match_threshold')
        return float(raw) if raw else _AUDIT_DEFAULT_THRESHOLD
    except Exception:
        return _AUDIT_DEFAULT_THRESHOLD


def _audit_phase_split(since, threshold):
    """Per-phase counts derived from the audit row's IMMUTABLE original_score.

    Phase 1 = not-qualified sweep (original_score < threshold at audit time).
    Phase 2 = qualified sample (original_score >= threshold at audit time).

    We deliberately bucket off `val.original_score` rather than joining to
    `candidate_vetting_log.is_qualified`: that field is mutated by later
    re-vetting runs, so a Phase 1 false-negative rescue could be silently
    re-bucketed as Phase 2 after the fact. `val.original_score` is the
    score the auditor saw at audit time and never changes.

    Rows with NULL original_score (rare; defensive) are placed in
    `phase_unknown` rather than silently dropped.
    """
    sql = """
        SELECT
            CASE
                WHEN val.original_score IS NULL THEN 'phase_unknown'
                WHEN val.original_score >= :thr THEN 'phase_2'
                ELSE 'phase_1'
            END AS phase,
            COUNT(*) AS audits,
            SUM(CASE WHEN val.finding_type IS NOT NULL AND val.finding_type <> 'no_issue' THEN 1 ELSE 0 END) AS findings,
            SUM(CASE WHEN val.action_taken = 'revet_triggered' THEN 1 ELSE 0 END) AS revets,
            SUM(CASE WHEN val.action_taken = 'revet_skipped_capped' THEN 1 ELSE 0 END) AS revets_capped,
            SUM(CASE WHEN val.action_taken = 'revet_skipped_stable' THEN 1 ELSE 0 END) AS revets_stable
        FROM vetting_audit_log val
        WHERE val.created_at >= :since
        GROUP BY 1
    """
    try:
        rows = db.session.execute(text(sql), {'since': since, 'thr': threshold}).fetchall()
    except Exception as exc:
        logger.warning(f"audit phase split query failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return {'phase_1': None, 'phase_2': None, 'phase_unknown': None}

    out = {'phase_1': None, 'phase_2': None, 'phase_unknown': None}
    for r in rows:
        bucket = r[0] or 'phase_unknown'
        out[bucket] = {
            'audits': int(r[1] or 0),
            'findings': int(r[2] or 0),
            'revets': int(r[3] or 0),
            'revets_capped': int(r[4] or 0),
            'revets_stable': int(r[5] or 0),
        }
    return out


def _audit_funnel(since, threshold):
    """Aggregate audit funnel + flip counts for one time window."""
    sql = """
        SELECT
            COUNT(*) AS audits,
            SUM(CASE WHEN val.finding_type IS NOT NULL AND val.finding_type <> 'no_issue' THEN 1 ELSE 0 END) AS findings,
            SUM(CASE WHEN val.confidence = 'high' AND val.finding_type <> 'no_issue' THEN 1 ELSE 0 END) AS high_conf_findings,
            SUM(CASE WHEN val.action_taken = 'revet_triggered' THEN 1 ELSE 0 END) AS revets,
            SUM(CASE
                    WHEN val.action_taken = 'revet_triggered'
                     AND val.original_score IS NOT NULL
                     AND val.revet_new_score IS NOT NULL
                     AND (
                          (val.original_score <  :thr AND val.revet_new_score >= :thr) OR
                          (val.original_score >= :thr AND val.revet_new_score <  :thr)
                     )
                THEN 1 ELSE 0
            END) AS flips,
            SUM(CASE
                    WHEN val.action_taken = 'revet_triggered'
                     AND val.original_score IS NOT NULL
                     AND val.revet_new_score IS NOT NULL
                     AND val.original_score <  :thr
                     AND val.revet_new_score >= :thr
                THEN 1 ELSE 0
            END) AS false_neg_rescues,
            SUM(CASE
                    WHEN val.action_taken = 'revet_triggered'
                     AND val.original_score IS NOT NULL
                     AND val.revet_new_score IS NOT NULL
                     AND val.original_score >= :thr
                     AND val.revet_new_score <  :thr
                THEN 1 ELSE 0
            END) AS false_pos_catches,
            SUM(CASE WHEN val.action_taken = 'revet_triggered' AND val.revet_new_score IS NULL THEN 1 ELSE 0 END) AS revets_pending
        FROM vetting_audit_log val
        WHERE val.created_at >= :since
    """
    try:
        row = db.session.execute(text(sql), {'since': since, 'thr': threshold}).fetchone()
    except Exception as exc:
        logger.warning(f"audit funnel query failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return None
    if not row:
        return None
    audits = int(row[0] or 0)
    revets = int(row[3] or 0)
    flips = int(row[4] or 0)
    return {
        'audits': audits,
        'findings': int(row[1] or 0),
        'high_conf_findings': int(row[2] or 0),
        'revets': revets,
        'flips': flips,
        'false_neg_rescues': int(row[5] or 0),
        'false_pos_catches': int(row[6] or 0),
        'revets_pending': int(row[7] or 0),
        'finding_rate_pct': (int(row[1] or 0) / audits * 100.0) if audits else 0.0,
        'revet_rate_pct': (revets / audits * 100.0) if audits else 0.0,
        'flip_rate_pct': (flips / revets * 100.0) if revets else 0.0,
    }


def _audit_finding_breakdown(since):
    sql = """
        SELECT finding_type, COUNT(*) AS n
        FROM vetting_audit_log
        WHERE created_at >= :since
        GROUP BY finding_type
        ORDER BY n DESC
    """
    try:
        rows = db.session.execute(text(sql), {'since': since}).fetchall()
    except Exception as exc:
        logger.warning(f"audit finding breakdown query failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return []
    return [{'finding_type': r[0] or 'unknown', 'count': int(r[1] or 0)} for r in rows]


def _audit_cost(since):
    sql = """
        SELECT
            COUNT(*) AS calls,
            COALESCE(SUM(input_tokens), 0) AS in_tok,
            COALESCE(SUM(output_tokens), 0) AS out_tok,
            COALESCE(SUM(estimated_cost_usd), 0) AS cost,
            COALESCE(AVG(duration_ms), 0) AS avg_ms
        FROM openai_call_log
        WHERE call_site_id = 'vetting_audit' AND created_at >= :since
    """
    try:
        row = db.session.execute(text(sql), {'since': since}).fetchone()
    except Exception as exc:
        logger.warning(f"audit cost query failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return {'calls': 0, 'tokens': 0, 'cost': 0.0, 'avg_cost': 0.0, 'avg_ms': 0.0}
    if not row:
        return {'calls': 0, 'tokens': 0, 'cost': 0.0, 'avg_cost': 0.0, 'avg_ms': 0.0}
    calls = int(row[0] or 0)
    cost = float(row[3] or 0)
    return {
        'calls': calls,
        'tokens': int(row[1] or 0) + int(row[2] or 0),
        'cost': cost,
        'avg_cost': (cost / calls) if calls else 0.0,
        'avg_ms': float(row[4] or 0),
    }


def _audit_backlog():
    """Completed candidate vetting logs in the last 7 days that have NOT been audited."""
    sql = """
        SELECT
            COUNT(*) AS unaudited,
            MIN(cvl.analyzed_at) AS oldest_at
        FROM candidate_vetting_log cvl
        LEFT JOIN vetting_audit_log val ON val.candidate_vetting_log_id = cvl.id
        WHERE cvl.status = 'completed'
          AND COALESCE(cvl.is_sandbox, FALSE) = FALSE
          AND cvl.analyzed_at >= NOW() - INTERVAL '7 days'
          AND val.id IS NULL
    """
    try:
        row = db.session.execute(text(sql)).fetchone()
    except Exception as exc:
        logger.warning(f"audit backlog query failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return {'unaudited': 0, 'oldest_at': None, 'oldest_age_hours': 0.0}
    if not row:
        return {'unaudited': 0, 'oldest_at': None, 'oldest_age_hours': 0.0}
    oldest_at = row[1]
    age_hours = 0.0
    if oldest_at:
        try:
            age_hours = (datetime.utcnow() - oldest_at).total_seconds() / 3600.0
        except Exception:
            age_hours = 0.0
    return {
        'unaudited': int(row[0] or 0),
        'oldest_at': oldest_at,
        'oldest_age_hours': age_hours,
    }


def _audit_recent_actions(since, limit=25):
    sql = """
        SELECT id, created_at, bullhorn_candidate_id, candidate_name,
               job_id, job_title, original_score, revet_new_score,
               finding_type, confidence, action_taken
        FROM vetting_audit_log
        WHERE created_at >= :since
        ORDER BY created_at DESC
        LIMIT :lim
    """
    try:
        rows = db.session.execute(text(sql), {'since': since, 'lim': limit}).fetchall()
    except Exception as exc:
        logger.warning(f"audit recent actions query failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return []
    out = []
    for r in rows:
        out.append({
            'id': r[0],
            'created_at': r[1],
            'candidate_id': r[2],
            'candidate_name': r[3],
            'job_id': r[4],
            'job_title': r[5],
            'original_score': float(r[6]) if r[6] is not None else None,
            'revet_new_score': float(r[7]) if r[7] is not None else None,
            'finding_type': r[8] or '',
            'confidence': r[9] or '',
            'action_taken': r[10] or '',
        })
    return out


def _read_audit_settings():
    """Pull the live tunable knobs so the UI shows them in context."""
    try:
        from models import VettingConfig
    except Exception:
        return {}
    keys = [
        'qualified_audit_sample_rate',
        'quality_auditor_model',
        'auditor_revet_cap_per_24h',
        'auditor_cooldown_hours',
        'auditor_revet_score_tolerance',
    ]
    out = {}
    for k in keys:
        try:
            out[k] = VettingConfig.get_value(k)
        except Exception:
            out[k] = None
    return out


@ai_cost_bp.route('/auditor', methods=['GET'])
@login_required
def auditor_dashboard():
    """Scout Quality Auditor visibility dashboard.

    Shows the audits → revets → flips funnel, phase split (false-negative
    vs false-positive sweeps), heuristic breakdown, cost, backlog, and the
    live tunable settings. Read-only — no writes.
    """
    _require_admin()

    threshold = _resolve_audit_threshold()
    now = datetime.utcnow()
    windows = {
        '24h': now - timedelta(hours=24),
        '7d':  now - timedelta(days=7),
        '30d': now - timedelta(days=30),
    }

    funnels = {label: _audit_funnel(since, threshold) for label, since in windows.items()}
    phase_splits = {label: _audit_phase_split(since, threshold) for label, since in windows.items()}
    costs = {label: _audit_cost(since) for label, since in windows.items()}

    # Choose the "primary" window for headline numbers from query string.
    primary = request.args.get('window', '7d')
    if primary not in windows:
        primary = '7d'
    since_primary = windows[primary]

    finding_breakdown = _audit_finding_breakdown(since_primary)
    backlog = _audit_backlog()
    recent_actions = _audit_recent_actions(since_primary, limit=25)
    settings = _read_audit_settings()

    return render_template(
        'admin_ai_cost_auditor.html',
        active_page='ai_cost',
        threshold=threshold,
        primary_window=primary,
        windows=list(windows.keys()),
        funnels=funnels,
        phase_splits=phase_splits,
        costs=costs,
        finding_breakdown=finding_breakdown,
        backlog=backlog,
        recent_actions=recent_actions,
        settings=settings,
        since_iso=since_primary.isoformat() + 'Z',
    )


# --- One-shot remediation: backfill stuck revet_triggered audit rows -------
#
# Mirrors `scripts/backfill_stuck_revets.py` but runs in-process inside the
# super-admin dashboard so it works on Reserved-VM deployments that don't
# expose a shell. Idempotent — only updates rows still matching the stuck
# criteria. Two-step UX: first POST without `confirm=yes` returns a preview
# (dry-run); second POST with `confirm=yes` commits.
#
@ai_cost_bp.route('/auditor/backfill-stuck-revets', methods=['POST'])
@login_required
def auditor_backfill_stuck_revets():
    _require_admin()

    from scripts.backfill_stuck_revets import find_stuck_revets, reclassify
    from screening.candidate_data import _resolve_vetting_cutoff

    confirm = request.form.get('confirm') == 'yes'

    try:
        age_hours = max(1, min(720, int(request.form.get('age_hours') or 24)))
    except (TypeError, ValueError):
        age_hours = 24

    raw_limit = (request.form.get('limit') or '').strip()
    try:
        limit = int(raw_limit) if raw_limit else None
        if limit is not None and limit <= 0:
            limit = None
    except (TypeError, ValueError):
        limit = None

    cutoff = _resolve_vetting_cutoff()
    if cutoff is None:
        flash(
            'vetting_cutoff_date is unset — backfill cannot run. '
            "Set the cutoff first via /vetting/settings.",
            'danger',
        )
        return redirect(url_for('ai_cost.auditor_dashboard'))

    age_threshold = datetime.utcnow() - timedelta(hours=age_hours)

    try:
        rows = find_stuck_revets(cutoff, age_threshold, limit)
    except Exception as exc:
        logger.exception(f"Auditor backfill: find_stuck_revets failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass
        flash(f'Backfill query failed: {exc!r}', 'danger')
        return redirect(url_for('ai_cost.auditor_dashboard'))

    if not rows:
        flash(
            f'No stuck rows found (cutoff={cutoff.isoformat()}, '
            f'age_threshold={age_hours}h). Nothing to do.',
            'info',
        )
        return redirect(url_for('ai_cost.auditor_dashboard'))

    if not confirm:
        # Keep total flash payload < 2KB to stay well under the 4KB
        # session-cookie ceiling (browsers drop the cookie above ~4KB,
        # which silently logs the user out and loses the message).
        preview_cap = 10
        preview_rows = []
        for row, latest_pe_at in rows[:preview_cap]:
            pe_repr = (
                latest_pe_at.strftime('%Y-%m-%d %H:%M')
                if latest_pe_at else 'none'
            )
            preview_rows.append(
                f"#{row.id} cand=#{row.bullhorn_candidate_id} "
                f"job=#{row.job_id} audit={row.created_at.strftime('%Y-%m-%d %H:%M') if row.created_at else '?'} "
                f"latest_pe={pe_repr}"
            )
        more = (
            f"\n…and {len(rows) - preview_cap} more (full list omitted to keep session cookie small)"
            if len(rows) > preview_cap else ''
        )
        flash(
            f"PREVIEW — {len(rows)} stuck row(s) eligible for backfill "
            f"(cutoff={cutoff.isoformat()}, age={age_hours}h). "
            f"Sample (first {min(preview_cap, len(rows))}):\n"
            + '\n'.join(preview_rows)
            + more
            + "\n\nIf the count looks right, click 'Confirm & Commit Backfill'.",
            'warning',
        )
        return redirect(url_for('ai_cost.auditor_dashboard'))

    # Commit path
    try:
        for row, latest_pe_at in rows:
            reclassify(row, latest_pe_at, cutoff)
        db.session.commit()
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.exception(f"Auditor backfill: commit failed: {exc}")
        flash(f'Backfill commit failed — rolled back, no rows updated: {exc!r}', 'danger')
        return redirect(url_for('ai_cost.auditor_dashboard'))

    actor = getattr(current_user, 'username', None) or 'unknown'
    logger.info(
        f"event=auditor_backfill_committed actor={actor} "
        f"rows={len(rows)} cutoff={cutoff.isoformat()} age_threshold_hours={age_hours}"
    )
    flash(
        f'✅ Backfill complete: {len(rows)} row(s) reclassified to '
        f"'revet_skipped_pre_cutoff'. Funnel will refresh on next page load.",
        'success',
    )
    return redirect(url_for('ai_cost.auditor_dashboard'))


# --- S2 Screening Shadow A/B Analysis ---------------------------------------

_SCREENING_THRESHOLD_SWEEP = [60, 65, 70, 75, 80, 85, 90]


def _screening_band(score):
    if score < 40:
        return '<40'
    if score < 70:
        return '40-69'
    if score < 90:
        return '70-89'
    return '≥90'


@ai_cost_bp.route('/screening-ab', methods=['GET'])
@login_required
def screening_ab_analysis():
    """Shadow-mode screening A/B dashboard (S2). Reads `screening_ab_log`."""
    _require_admin()

    try:
        hours = int(request.args.get('hours', '168'))
    except (TypeError, ValueError):
        hours = 168
    hours = max(1, min(hours, 24 * 30))
    since = datetime.utcnow() - timedelta(hours=hours)

    rows = []
    prod_model = ''
    shadow_model = ''
    try:
        result = db.session.execute(
            text(
                "SELECT prod_model, shadow_model, prod_score, shadow_score, "
                "       score_delta, prod_qualified, shadow_qualified_inferred, "
                "       bullhorn_candidate_id, bullhorn_job_id, job_title, "
                "       shadow_input_tokens, shadow_output_tokens, "
                "       shadow_estimated_cost_usd, shadow_duration_ms, "
                "       shadow_error, created_at "
                "FROM screening_ab_log "
                "WHERE created_at >= :since "
                # Exclude prompt-cache-audit rows (tagged '{model}|{layout}').
                # Those live alongside model-A/B rows in the same table but
                # measure prompt-induced score drift, not model differences,
                # and would pollute the model-A/B headline metrics.
                "  AND COALESCE(prod_model,'')   NOT LIKE '%|%' "
                "  AND COALESCE(shadow_model,'') NOT LIKE '%|%' "
                "ORDER BY created_at DESC"
            ),
            {'since': since},
        ).fetchall()
        for r in result:
            rows.append({
                'prod_model': r[0],
                'shadow_model': r[1],
                'prod_score': float(r[2] or 0.0),
                'shadow_score': float(r[3]) if r[3] is not None else None,
                'score_delta': float(r[4]) if r[4] is not None else None,
                'prod_qualified': bool(r[5]) if r[5] is not None else None,
                'shadow_qualified_inferred': bool(r[6]) if r[6] is not None else None,
                'bullhorn_candidate_id': r[7],
                'bullhorn_job_id': r[8],
                'job_title': r[9],
                'shadow_input_tokens': r[10],
                'shadow_output_tokens': r[11],
                'shadow_estimated_cost_usd': float(r[12]) if r[12] is not None else 0.0,
                'shadow_duration_ms': r[13],
                'shadow_error': r[14],
                'created_at': r[15],
            })
        if rows:
            prod_model = rows[0]['prod_model']
            shadow_model = rows[0]['shadow_model']
    except Exception as exc:
        logger.warning(f"screening_ab query failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass

    # Filter to rows with successful shadow scores for paired metrics
    paired = [r for r in rows if r['shadow_score'] is not None]
    total_pairs = len(paired)
    total_attempts = len(rows)
    shadow_errors = sum(1 for r in rows if r['shadow_error'])
    shadow_total_cost = sum(r['shadow_estimated_cost_usd'] for r in rows)

    # Headline metrics
    if total_pairs > 0:
        avg_prod = sum(r['prod_score'] for r in paired) / total_pairs
        avg_shadow = sum(r['shadow_score'] for r in paired) / total_pairs
        avg_delta = avg_shadow - avg_prod
        pearson_r = _pearson(
            [r['prod_score'] for r in paired],
            [r['shadow_score'] for r in paired],
        )
        # Acceptance-bar metrics
        deltas_borderline = [abs(r['shadow_score'] - r['prod_score'])
                             for r in paired if 40 <= r['prod_score'] < 90]
        avg_drift_borderline = (sum(deltas_borderline) / len(deltas_borderline)
                                if deltas_borderline else 0.0)
        deltas_risk = [abs(r['shadow_score'] - r['prod_score'])
                       for r in paired if 70 <= r['prod_score'] < 90]
        max_drift_risk = max(deltas_risk) if deltas_risk else 0.0
        # Hard-reject false negatives: shadow <40 AND prod >=80
        hard_reject_fns = [r for r in paired
                           if r['shadow_score'] < 40 and r['prod_score'] >= 80]
        hard_reject_fn_count = len(hard_reject_fns)
    else:
        avg_prod = avg_shadow = avg_delta = pearson_r = 0.0
        avg_drift_borderline = max_drift_risk = 0.0
        hard_reject_fns = []
        hard_reject_fn_count = 0

    # Confusion matrices at multiple thresholds
    confusion_by_threshold = []
    for t in [40, 80, 90]:
        if total_pairs == 0:
            confusion_by_threshold.append({
                'threshold': t,
                'both_pass': 0, 'both_block': 0, 'fn': 0, 'fp': 0,
                'concordance_pct': 0.0, 'fn_pct': 0.0,
            })
            continue
        bp = bb = fn = fp = 0
        for r in paired:
            prod_pass = r['prod_score'] >= t
            shadow_pass = r['shadow_score'] >= t
            if prod_pass and shadow_pass:
                bp += 1
            elif (not prod_pass) and (not shadow_pass):
                bb += 1
            elif prod_pass and not shadow_pass:
                fn += 1
            else:
                fp += 1
        confusion_by_threshold.append({
            'threshold': t,
            'both_pass': bp, 'both_block': bb, 'fn': fn, 'fp': fp,
            'concordance_pct': ((bp + bb) / total_pairs) * 100.0,
            'fn_pct': (fn / total_pairs) * 100.0,
        })

    # Threshold sweep — find a shadow gate that meets the acceptance bar
    threshold_sweep = []
    best_idx = -1
    best_concordance = -1.0
    if total_pairs > 0:
        for i, t in enumerate(_SCREENING_THRESHOLD_SWEEP):
            agree = fn = fp = shadow_pass = 0
            for r in paired:
                # Compare against prod's qualification at the conventional 80 floor
                prod_pass = r['prod_score'] >= 80
                sh_pass = r['shadow_score'] >= t
                if sh_pass == prod_pass:
                    agree += 1
                elif prod_pass and not sh_pass:
                    fn += 1
                else:
                    fp += 1
                if sh_pass:
                    shadow_pass += 1
            concordance = (agree / total_pairs) * 100.0
            fn_pct = (fn / total_pairs) * 100.0
            fp_pct = (fp / total_pairs) * 100.0
            # Recommend ONLY when FN ≤ 2% (acceptance bar policy)
            if fn_pct <= 2.0 and concordance > best_concordance:
                best_concordance = concordance
                best_idx = i
            threshold_sweep.append({
                'threshold': t,
                'concordance_pct': concordance,
                'fn_pct': fn_pct,
                'fp_pct': fp_pct,
                'shadow_pass_pct': (shadow_pass / total_pairs) * 100.0,
                'recommended': False,
            })
        if best_idx >= 0:
            threshold_sweep[best_idx]['recommended'] = True

    # Critical disagreements: 70-89 prod band where |delta| >= 10
    critical_disagreements = sorted(
        [r for r in paired
         if 70 <= r['prod_score'] < 90 and abs(r['shadow_score'] - r['prod_score']) >= 10],
        key=lambda r: abs(r['shadow_score'] - r['prod_score']),
        reverse=True,
    )[:25]

    # Top-25 hard-reject false negatives
    top_hard_reject_fns = sorted(
        hard_reject_fns,
        key=lambda r: r['prod_score'],
        reverse=True,
    )[:25]

    # Score-band breakdown
    band_stats = {}
    for r in paired:
        band = _screening_band(r['prod_score'])
        if band not in band_stats:
            band_stats[band] = {'count': 0, 'sum_delta': 0.0, 'max_abs_delta': 0.0}
        band_stats[band]['count'] += 1
        delta = r['shadow_score'] - r['prod_score']
        band_stats[band]['sum_delta'] += delta
        band_stats[band]['max_abs_delta'] = max(band_stats[band]['max_abs_delta'], abs(delta))
    band_breakdown = []
    for band in ['<40', '40-69', '70-89', '≥90']:
        s = band_stats.get(band, {'count': 0, 'sum_delta': 0.0, 'max_abs_delta': 0.0})
        avg_d = (s['sum_delta'] / s['count']) if s['count'] else 0.0
        band_breakdown.append({
            'band': band,
            'count': s['count'],
            'pct': (s['count'] / total_pairs * 100.0) if total_pairs else 0.0,
            'avg_delta': avg_d,
            'max_abs_delta': s['max_abs_delta'],
        })

    # Acceptance-bar verdict
    acceptance_target_pairs = 6000
    bar_pairs_ok = total_pairs >= acceptance_target_pairs
    bar_hard_reject_ok = hard_reject_fn_count == 0
    bar_avg_drift_ok = avg_drift_borderline <= 3.0
    bar_max_drift_ok = max_drift_risk <= 8.0
    all_bars_pass = bar_pairs_ok and bar_hard_reject_ok and bar_avg_drift_ok and bar_max_drift_ok

    if total_pairs == 0:
        verdict_class, verdict_icon, verdict_text = 'secondary', 'info-circle', 'No data yet — flip SCREENING_AB_SHADOW_ENABLED=true and wait for screening cycles.'
    elif all_bars_pass:
        verdict_class, verdict_icon = 'success', 'check-circle'
        verdict_text = ('All acceptance-bar gates PASSED. Two-stage cutover is safe to '
                        'propose at the recommended shadow threshold below.')
    elif not bar_pairs_ok:
        verdict_class, verdict_icon = 'info', 'hourglass-half'
        verdict_text = (f'Need more data: {total_pairs:,} / {acceptance_target_pairs:,} paired scores collected. '
                        f'Continue running the shadow.')
    elif not bar_hard_reject_ok:
        verdict_class, verdict_icon = 'danger', 'times-circle'
        verdict_text = (f'BLOCKER: {hard_reject_fn_count} hard-rejected qualifications '
                        f'(shadow <40 while prod ≥80). The mini model is missing real qualified candidates.')
    else:
        verdict_class, verdict_icon = 'warning', 'exclamation-triangle'
        details = []
        if not bar_avg_drift_ok:
            details.append(f'avg drift {avg_drift_borderline:.1f}pt > 3pt floor')
        if not bar_max_drift_ok:
            details.append(f'max risk-band drift {max_drift_risk:.1f}pt > 8pt floor')
        verdict_text = ('Acceptance bar NOT met: ' + '; '.join(details) +
                        '. Investigate before proposing cutover.')

    return render_template(
        'admin_ai_cost_screening_ab.html',
        active_page='ai_cost',
        hours=hours,
        since_iso=since.isoformat() + 'Z',
        prod_model=prod_model,
        shadow_model=shadow_model,
        total_pairs=total_pairs,
        total_attempts=total_attempts,
        shadow_errors=shadow_errors,
        shadow_total_cost=shadow_total_cost,
        avg_prod=avg_prod,
        avg_shadow=avg_shadow,
        avg_delta=avg_delta,
        pearson_r=pearson_r,
        avg_drift_borderline=avg_drift_borderline,
        max_drift_risk=max_drift_risk,
        hard_reject_fn_count=hard_reject_fn_count,
        confusion_by_threshold=confusion_by_threshold,
        threshold_sweep=threshold_sweep,
        critical_disagreements=critical_disagreements,
        top_hard_reject_fns=top_hard_reject_fns,
        band_breakdown=band_breakdown,
        bar_pairs_ok=bar_pairs_ok,
        bar_hard_reject_ok=bar_hard_reject_ok,
        bar_avg_drift_ok=bar_avg_drift_ok,
        bar_max_drift_ok=bar_max_drift_ok,
        acceptance_target_pairs=acceptance_target_pairs,
        verdict_class=verdict_class,
        verdict_icon=verdict_icon,
        verdict_text=verdict_text,
    )
