"""Per-job match thresholds vs the global 80% baseline.

Custom JobVettingRequirements.vetting_threshold values are the qualify bar
for that job, whether they are below or above the global default.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


def coerce_job_id(value: Any) -> Optional[int]:
    """Bullhorn job ids arrive as int or str; cache keys must be one type."""
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def threshold_map_from_requirements(rows: Iterable[Any]) -> Dict[int, float]:
    mapping: Dict[int, float] = {}
    for req in rows or []:
        job_id = coerce_job_id(getattr(req, 'bullhorn_job_id', None))
        raw = getattr(req, 'vetting_threshold', None)
        if job_id is None or raw is None:
            continue
        try:
            mapping[job_id] = float(raw)
        except (TypeError, ValueError):
            continue
    return mapping


def resolve_job_threshold(
    threshold_map: Optional[Dict[Any, float]],
    job_id: Any,
    global_threshold: float,
) -> float:
    mapped = coerce_job_id(job_id)
    if mapped is None or not threshold_map:
        return float(global_threshold)
    if mapped in threshold_map:
        return float(threshold_map[mapped])
    for key, value in threshold_map.items():
        if coerce_job_id(key) == mapped:
            return float(value)
    return float(global_threshold)


def score_clears_job_threshold(score: Any, job_threshold: Any) -> bool:
    try:
        return float(score) >= float(job_threshold)
    except (TypeError, ValueError):
        return False


def custom_job_threshold(
    threshold_map: Optional[Dict[Any, float]],
    job_id: Any,
) -> Optional[float]:
    """Per-job override, or None when the job uses the global baseline."""
    mapped = coerce_job_id(job_id)
    if mapped is None or not threshold_map:
        return None
    if mapped in threshold_map:
        return float(threshold_map[mapped])
    for key, value in threshold_map.items():
        if coerce_job_id(key) == mapped:
            return float(value)
    return None
