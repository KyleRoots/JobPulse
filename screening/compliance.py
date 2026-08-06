"""
Screening compliance helpers — rules versioning, metadata stamping, and metrics.

Phase A hardening: every completed vetting run records which rules/model/profile
produced the result so audits can answer "what recipe was used?"
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Bump when prompts, post-processing guardrails, or default thresholds change.
SCREENING_RULES_VERSION = '2026.07.29'

SCREENING_PRODUCT_NAME = 'Scout Screening (Scout Genius)'

# Recommended resume retention for compliance posture (enforced by cleanup job later).
SCREENING_RESUME_RETENTION_MONTHS = 24


def get_privacy_contact_for_host(host: str) -> str:
    """Candidate-facing privacy / screening inquiry contact by apply domain."""
    host_l = (host or '').lower()
    if 'stsigroup.com' in host_l:
        return 'apply@stsigroup.com'
    return 'apply@myticas.com'


def get_screening_rules_metadata(service) -> Dict[str, Any]:
    """Build the rules snapshot to stamp on a completed vetting log."""
    profile = 'standard'
    threshold = 80
    routing_mode = 'off'
    layer2_model = 'gpt-5.4'

    try:
        from utils.environment_context import get_current_environment
        env = get_current_environment()
        if env is not None:
            profile = (getattr(env, 'screening_profile', None) or 'standard').strip() or 'standard'
    except Exception:
        pass

    try:
        if service is not None:
            threshold = int(service.get_config_value('match_threshold', '80'))
            routing_mode = str(service.get_config_value('screening_routing_mode', 'off') or 'off')
            layer2_model = str(service.get_config_value('layer2_model', 'gpt-5.4') or 'gpt-5.4')
    except Exception:
        pass

    return {
        'rules_version': SCREENING_RULES_VERSION,
        'product_name': SCREENING_PRODUCT_NAME,
        'prompt_profile': profile,
        'layer2_model': layer2_model,
        'routing_mode': routing_mode,
        'match_threshold': threshold,
        'stamped_at': datetime.utcnow().isoformat() + 'Z',
    }


def stamp_vetting_log_compliance(vetting_log, service) -> None:
    """Attach compliance metadata columns on a CandidateVettingLog."""
    meta = get_screening_rules_metadata(service)
    vetting_log.screening_rules_version = meta['rules_version']
    vetting_log.screening_model_used = meta['layer2_model']
    vetting_log.screening_prompt_profile = meta['prompt_profile']
    vetting_log.screening_rules_json = json.dumps(meta)


def build_compliance_metrics(days: int = 7, environment_id: Optional[int] = None) -> Dict[str, Any]:
    """Weekly bias/quality snapshot for admin review."""
    from sqlalchemy import func
    from models import CandidateVettingLog, CandidateJobMatch

    since = datetime.utcnow() - timedelta(days=max(1, days))
    log_q = CandidateVettingLog.query.filter(
        CandidateVettingLog.analyzed_at >= since,
        CandidateVettingLog.status == 'completed',
    )
    if environment_id is not None:
        log_q = log_q.filter(CandidateVettingLog.environment_id == environment_id)

    logs = log_q.all()
    log_ids = [l.id for l in logs]
    total_screened = len(logs)
    qualified_candidates = sum(1 for l in logs if l.is_qualified)

    match_q = CandidateJobMatch.query.filter(CandidateJobMatch.vetting_log_id.in_(log_ids)) if log_ids else None
    matches = match_q.all() if match_q is not None else []

    qualified_matches = [m for m in matches if m.is_qualified]
    prestige_boosted = [m for m in matches if m.prestige_boost_applied]
    scores = [m.match_score for m in matches if m.match_score is not None]

    def _pct(n: int, d: int) -> float:
        return round((n / d) * 100, 1) if d else 0.0

    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    # Score distribution buckets
    buckets = {'0-49': 0, '50-69': 0, '70-84': 0, '85-100': 0}
    for s in scores:
        if s < 50:
            buckets['0-49'] += 1
        elif s < 70:
            buckets['50-69'] += 1
        elif s < 85:
            buckets['70-84'] += 1
        else:
            buckets['85-100'] += 1

    rules_versions = {}
    for l in logs:
        v = l.screening_rules_version or 'unknown'
        rules_versions[v] = rules_versions.get(v, 0) + 1

    return {
        'period_days': days,
        'since': since.isoformat() + 'Z',
        'current_rules_version': SCREENING_RULES_VERSION,
        'totals': {
            'candidates_screened': total_screened,
            'candidates_qualified': qualified_candidates,
            'candidate_qualification_rate_pct': _pct(qualified_candidates, total_screened),
            'job_matches_evaluated': len(matches),
            'job_matches_qualified': len(qualified_matches),
            'match_qualification_rate_pct': _pct(len(qualified_matches), len(matches)),
            'prestige_boost_applied_count': len(prestige_boosted),
            'avg_match_score': avg_score,
        },
        'score_distribution': buckets,
        'rules_versions_seen': rules_versions,
        'notes': [
            'Scout Screening is decision-support; recruiters make final decisions.',
            'Prestige boost is per-job opt-in and logged on each match.',
            'Compare qualification rates over time before broad reactivation.',
        ],
    }
