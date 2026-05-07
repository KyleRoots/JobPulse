"""Module-Based AI Cost Forecaster.

Derives per-module unit costs from `openai_call_log` and projects monthly
cost for arbitrary combinations of active modules at arbitrary volumes.

Each MODULE_DEFINITIONS entry maps a logical module (the user-facing
feature toggle, e.g. "Inbound", "Screening") to:
    - the list of OpenAI call_site_ids that fire when that module runs
    - a `primary_site` whose call count = "one unit of work" for that
      module (one parsed candidate, one screened candidate, one vetting
      session, etc.)
    - a human-readable `unit` label used in the UI

Unit cost derivation:
    unit_cost_usd = SUM(cost over all module sites) / COUNT(primary site)

This handles the common case where one logical unit triggers several
OpenAI calls (e.g. screening one candidate calls requirements_extract +
scoring + maybe years_recheck + zero_recheck).

Modules with too few primary-site calls in the window are flagged
"insufficient_data" so the UI can prompt for a manual override rather
than silently report $0.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text

from extensions import db

logger = logging.getLogger(__name__)


MIN_SAMPLE_FOR_CONFIDENT = 30


@dataclass(frozen=True)
class ModuleDef:
    key: str
    label: str
    unit: str
    primary_site: str
    sites: tuple[str, ...]
    notes: str = ''


MODULE_DEFINITIONS: list[ModuleDef] = [
    ModuleDef(
        key='inbound',
        label='Inbound',
        unit='candidate parsed',
        primary_site='email_inbound.resume_parse',
        sites=(
            'email_inbound.resume_parse',
            'email_inbound.dedup_validate',
            'email_inbound.identity_extract',
        ),
        notes='Email-forward → candidate intake (parse + dedup + identity).',
    ),
    ModuleDef(
        key='screening',
        label='Screening',
        unit='candidate screened',
        primary_site='screening.scoring',
        sites=(
            'screening.requirements_extract',
            'screening.scoring',
            'screening.years_recheck',
            'screening.zero_recheck',
            'scout_screening.optimize_reqs',
        ),
        notes='AI scoring of candidate vs. job requirements.',
    ),
    ModuleDef(
        key='vetting',
        label='Vetting',
        unit='vetting session',
        primary_site='scout_vetting.questions',
        sites=(
            'scout_vetting.questions',
            'scout_vetting.reply_intent',
            'scout_vetting.outcome',
            'scout_vetting.followup_email',
            'vetting.ocr_doc',
            'vetting.ocr_pages',
        ),
        notes='Conversational candidate vetting + OCR of vetting docs.',
    ),
    ModuleDef(
        key='scout_support',
        label='Scout Support',
        unit='support ticket',
        primary_site='scout_support.understanding',
        sites=(
            'scout_support.understanding',
            'scout_support.clarification',
            'scout_support.platform_intake',
            'scout_support.platform_reply',
            'scout_support.classify_reply',
            'scout_support.admin_handling_intent',
            'scout_support.admin_question',
            'scout_support.admin_refine',
            'scout_support.draft_generation',
            'scout_support.retry_analysis',
            'scout_support.reopen_analysis',
            'scout_support.reopen-analysis',
            'scout_support.failure_analysis',
            'scout_support.vision',
            'scout_support.knowledge_embed',
        ),
        notes='Internal ATS support ticket lifecycle.',
    ),
    ModuleDef(
        key='prospector',
        label='Search / Recruit (Prospector)',
        unit='prospect search',
        primary_site='scout_prospector.web_search',
        sites=(
            'scout_prospector.web_search',
            'scout_prospector.refine',
        ),
        notes='Web-search-driven candidate prospecting + AI refinement.',
    ),
    ModuleDef(
        key='automation',
        label='Job Automation',
        unit='job processed',
        primary_site='automation.title_extract',
        sites=(
            'automation.title_extract',
            'job_classification',
        ),
        notes='XML-feed job ingestion (title extract + classification).',
    ),
    ModuleDef(
        key='resume_parsing',
        label='Resume Parsing (general)',
        unit='resume parsed',
        primary_site='resume_parser.format_html',
        sites=(
            'resume_parser.format_html',
        ),
        notes='Generic resume → HTML formatting (used by Apply forms).',
    ),
    ModuleDef(
        key='duplicate_detection',
        label='Fuzzy Duplicate Detection',
        unit='dedup check',
        primary_site='fuzzy_duplicate_matcher',
        sites=(
            'fuzzy_duplicate_matcher',
        ),
        notes='AI fuzzy-match second-pass on suspected duplicate candidates.',
    ),
    ModuleDef(
        key='embeddings',
        label='Embeddings',
        unit='embedding generated',
        primary_site='embedding_service.candidate',
        sites=(
            'embedding_service.candidate',
        ),
        notes='Candidate profile vectorization for similarity matching.',
    ),
]


MODULE_BY_KEY: dict[str, ModuleDef] = {m.key: m for m in MODULE_DEFINITIONS}


def _safe_query(sql: str, params: dict) -> list:
    try:
        return db.session.execute(text(sql), params).fetchall()
    except Exception as exc:
        logger.warning(f"cost_forecaster query failed: {exc}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return []


def derive_unit_costs(window_days: int = 14) -> dict[str, dict[str, Any]]:
    """For each module, compute unit_cost_usd = total_cost / primary_count
    over the most recent `window_days`.

    Returns a dict keyed by module.key. Each entry has:
        unit_cost_usd: float (0.0 if insufficient_data)
        sample_size: int   (count of primary_site calls in window)
        total_cost_usd: float
        confidence: 'confident' | 'low' | 'insufficient_data'
        window_days: int
    """
    since = datetime.utcnow() - timedelta(days=window_days)

    # One pass aggregating both module-wide cost and primary-site count.
    site_to_module: dict[str, ModuleDef] = {}
    for m in MODULE_DEFINITIONS:
        for s in m.sites:
            site_to_module[s] = m

    rows = _safe_query(
        "SELECT call_site_id, "
        "       COUNT(*) AS calls, "
        "       COALESCE(SUM(estimated_cost_usd), 0) AS cost "
        "FROM openai_call_log "
        "WHERE created_at >= :since "
        "GROUP BY call_site_id",
        {'since': since},
    )

    per_module_cost: dict[str, float] = {m.key: 0.0 for m in MODULE_DEFINITIONS}
    per_module_primary_calls: dict[str, int] = {m.key: 0 for m in MODULE_DEFINITIONS}
    for r in rows:
        site = r[0]
        calls = int(r[1] or 0)
        cost = float(r[2] or 0)
        m = site_to_module.get(site)
        if not m:
            continue
        per_module_cost[m.key] += cost
        if site == m.primary_site:
            per_module_primary_calls[m.key] += calls

    out: dict[str, dict[str, Any]] = {}
    for m in MODULE_DEFINITIONS:
        primary_calls = per_module_primary_calls[m.key]
        total_cost = per_module_cost[m.key]
        if primary_calls <= 0:
            confidence = 'insufficient_data'
            unit_cost = 0.0
        else:
            unit_cost = total_cost / primary_calls
            confidence = 'confident' if primary_calls >= MIN_SAMPLE_FOR_CONFIDENT else 'low'
        out[m.key] = {
            'unit_cost_usd': round(unit_cost, 6),
            'sample_size': primary_calls,
            'total_cost_usd': round(total_cost, 4),
            'confidence': confidence,
            'window_days': window_days,
        }
    return out


def project_monthly_cost(
    active_modules: dict[str, int],
    unit_costs: dict[str, dict[str, Any]],
    overrides: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Given {module_key: monthly_volume} and a unit-cost table (with
    optional manual overrides), return per-module + total projected cost.

    overrides: {module_key: unit_cost_usd} — when present, supersedes the
    derived unit cost. Used both for "insufficient_data" modules AND for
    user-driven scenario tuning (e.g. modeling a contract bump).
    """
    overrides = overrides or {}
    breakdown = []
    grand_total = 0.0
    for mod_key, volume in active_modules.items():
        m = MODULE_BY_KEY.get(mod_key)
        if not m or volume <= 0:
            continue
        derived = unit_costs.get(mod_key, {}) or {}
        override = overrides.get(mod_key)
        if (override is not None and isinstance(override, (int, float))
                and not math.isnan(float(override))
                and not math.isinf(float(override))
                and override >= 0):
            unit_cost = float(override)
            source = 'override'
        else:
            unit_cost = float(derived.get('unit_cost_usd') or 0.0)
            source = derived.get('confidence', 'insufficient_data')
        monthly = unit_cost * volume
        grand_total += monthly
        breakdown.append({
            'module_key': mod_key,
            'module_label': m.label,
            'unit': m.unit,
            'monthly_volume': int(volume),
            'unit_cost_usd': round(unit_cost, 6),
            'monthly_cost_usd': round(monthly, 2),
            'source': source,
            'sample_size': int(derived.get('sample_size') or 0),
        })
    return {
        'breakdown': breakdown,
        'monthly_cost_usd': round(grand_total, 2),
        'annual_cost_usd': round(grand_total * 12, 2),
    }
