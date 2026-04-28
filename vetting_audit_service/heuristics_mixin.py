"""Auto-split from vetting_audit_service.py — see vetting_audit_service/__init__.py."""
import json
import logging
import os
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

from .helpers import DOMAIN_KEYWORDS, get_platform_age_ceilings

logger = logging.getLogger(__name__)

class HeuristicsMixin:
    """Heuristic and false-positive rule engines that run before AI confirmation."""

    def _run_heuristic_checks(self, vetting_log, job_match) -> List[Dict]:
        issues = []

        gaps = (job_match.gaps_identified or '').lower()
        match_summary = (job_match.match_summary or '').lower()
        job_title = (job_match.job_title or '').lower()
        platform_age_ceilings = get_platform_age_ceilings()

        recency_phrases = [
            'career trajectory has shifted away',
            'not practiced relevant skills in their last two positions',
            'most recent professional activity is outside the target domain',
        ]
        for phrase in recency_phrases:
            if phrase in gaps:
                candidate_title = ''
                if vetting_log.resume_text:
                    lines = vetting_log.resume_text[:500].split('\n')
                    for line in lines[:10]:
                        line_lower = line.strip().lower()
                        if any(kw in line_lower for kw in DOMAIN_KEYWORDS):
                            candidate_title = line.strip()
                            break

                if candidate_title:
                    issues.append({
                        'check_type': 'recency_misfire',
                        'description': (
                            f"Gaps say '{phrase}' but candidate's resume header "
                            f"indicates current role: '{candidate_title}'. "
                            f"Possible recency gate misfire."
                        )
                    })
                    break

        experience_match = (job_match.experience_match or '')
        _recency_tag_idx = experience_match.find('[Recency:')
        if _recency_tag_idx >= 0:
            _recency_tag = experience_match[_recency_tag_idx:]
            if 'relevant=yes' in _recency_tag:
                _justification_idx = _recency_tag.find('justification:')
                if _justification_idx >= 0:
                    _justification_text = _recency_tag[_justification_idx + len('justification:'):].rstrip(']').strip()
                    _WEAK_PHRASES = [
                        'transferable skills', 'transferable',
                        'general experience', 'general work experience',
                        'work ethic', 'reliable', 'reliability',
                        'communication skills', 'teamwork',
                        'customer-facing', 'customer facing',
                        'soft skills', 'people skills',
                        'has work experience', 'has experience',
                    ]
                    _justification_lower = _justification_text.lower()
                    _is_weak = (
                        len(_justification_text) < 20
                        or any(wp in _justification_lower for wp in _WEAK_PHRASES)
                    )
                    if _is_weak:
                        issues.append({
                            'check_type': 'recency_misfire',
                            'description': (
                                f"AI marked most recent role as relevant but justification "
                                f"is weak or generic: '{_justification_text[:120]}'. "
                                f"Possible inflated recency classification."
                            )
                        })

        years_json_str = job_match.years_analysis_json
        if years_json_str:
            try:
                years_data = json.loads(years_json_str) if isinstance(years_json_str, str) else years_json_str
                if isinstance(years_data, dict):
                    for skill, data in years_data.items():
                        if not isinstance(data, dict):
                            continue
                        required = float(data.get('required_years', 0))
                        if required <= 0:
                            continue
                        skill_lower = skill.lower()
                        for platform_key, ceiling in platform_age_ceilings.items():
                            if platform_key in skill_lower and required > ceiling:
                                issues.append({
                                    'check_type': 'platform_age_violation',
                                    'description': (
                                        f"Job requires {required:.0f}yr of '{skill}' but "
                                        f"platform max is ~{ceiling:.0f}yr. "
                                        f"Impossible requirement may have inflated gap scoring."
                                    )
                                })
                                break
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        positive_indicators = [
            'strong technical skills', 'meets all', 'meets the mandatory',
            'well-aligned', 'strong match', 'closely aligned',
            'extensive experience', 'solid background', 'strong background'
        ]
        if job_match.match_score is not None and job_match.match_score < 40:
            if any(indicator in match_summary for indicator in positive_indicators):
                issues.append({
                    'check_type': 'score_inconsistency',
                    'description': (
                        f"AI summary uses positive language ('{match_summary[:100]}...') "
                        f"but score is only {job_match.match_score}%. "
                        f"Possible post-processing penalty was too aggressive."
                    )
                })

        if 'location mismatch' in gaps:
            tech_score = job_match.technical_score
            final_score = job_match.match_score or 0
            from models import VettingConfig
            threshold = VettingConfig.get_value('match_threshold', 80.0)
            try:
                threshold = float(threshold)
            except (ValueError, TypeError):
                threshold = 80.0

            if tech_score is not None and tech_score >= (threshold - 10) and not job_match.is_qualified:
                non_loc_gaps = [
                    part.strip() for part in gaps.replace(' | ', '|').split('|')
                    if 'location mismatch' not in part.lower() and part.strip()
                ]
                if not non_loc_gaps:
                    issues.append({
                        'check_type': 'location_score_consistency',
                        'description': (
                            f"Candidate has technical_score={tech_score:.0f}% "
                            f"(threshold={threshold:.0f}%) with location as the ONLY gap, "
                            f"but was marked Not Recommended (final={final_score:.0f}%). "
                            f"This may be a strong technical fit that should be Location Barrier instead."
                        )
                    })
            elif tech_score is None and 'location mismatch' in gaps:
                other_gaps = [
                    part.strip() for part in gaps.replace(' | ', '|').split('|')
                    if 'location mismatch' not in part.lower() and part.strip()
                ]
                if not other_gaps and final_score >= (threshold - 20):
                    issues.append({
                        'check_type': 'location_score_consistency',
                        'description': (
                            f"Location is the only gap but no technical_score recorded "
                            f"(pre two-phase scoring). Final score={final_score:.0f}% "
                            f"with threshold={threshold:.0f}%. Consider re-screening to "
                            f"capture separate technical vs. location scoring."
                        )
                    })

        if 'location mismatch: different country' in gaps:
            try:
                from models import JobVettingRequirements
                job_req = JobVettingRequirements.query.filter_by(
                    bullhorn_job_id=job_match.bullhorn_job_id
                ).first()
                if job_req and (job_req.job_work_type or '').strip().lower() == 'remote':
                    raw_location = (job_req.job_location or '').strip()
                    job_country = raw_location.lower()
                    if ',' in job_country:
                        job_country = job_country.split(',')[-1].strip()

                    summary_lower = (job_match.match_summary or '').lower()
                    resume_header = (vetting_log.resume_text or '')[:600].lower()

                    same_country_signals = []

                    positive_location_phrases = [
                        'meeting the location requirement',
                        'meets the location requirement',
                        'satisfies the location requirement',
                        'meets the remote location',
                        'eligible for remote work in',
                        'qualifies for the remote',
                    ]
                    for phrase in positive_location_phrases:
                        if phrase in summary_lower:
                            same_country_signals.append(f"summary says \"{phrase}\"")
                            break

                    if job_country:
                        affirmative_country_patterns = [
                            f"based in {job_country}",
                            f"located in {job_country}",
                            f"residing in {job_country}",
                            f"candidate is in {job_country}",
                            f"candidate is located in {job_country}",
                        ]
                        for pattern in affirmative_country_patterns:
                            if pattern in summary_lower:
                                same_country_signals.append(
                                    f"summary explicitly places candidate in '{job_country}' ({pattern!r})"
                                )
                                break

                        resume_first_line = resume_header.split('\n')[0] if '\n' in resume_header else resume_header[:120]
                        if f", {job_country}" in resume_first_line or resume_first_line.endswith(job_country):
                            same_country_signals.append(
                                f"resume first line contains '{job_country}' in location position"
                            )

                    if same_country_signals:
                        tech_display = f"{job_match.technical_score:.0f}%" if job_match.technical_score is not None else "N/A"
                        final_display = f"{job_match.match_score:.0f}%" if job_match.match_score is not None else "N/A"
                        issues.append({
                            'check_type': 'remote_location_misfire',
                            'description': (
                                f"Job {job_match.bullhorn_job_id} is Remote (location: {raw_location}). "
                                f"Gaps contain 'location mismatch: different country' but evidence suggests "
                                f"candidate is in the same country — {'; '.join(same_country_signals)}. "
                                f"Technical: {tech_display}, Final: {final_display}. "
                                f"Likely a location penalty misfire on a remote role."
                            )
                        })
            except Exception as _loc_err:
                logger.debug(f"remote_location_misfire check error: {_loc_err}")

        years_json_str2 = job_match.years_analysis_json
        if years_json_str2:
            try:
                years_data2 = json.loads(years_json_str2) if isinstance(years_json_str2, str) else years_json_str2
                if isinstance(years_data2, dict):
                    for skill, data in years_data2.items():
                        if not isinstance(data, dict):
                            continue
                        required = float(data.get('required_years', 0))
                        estimated = float(data.get('estimated_years', data.get('actual_years', 0)))
                        meets = data.get('meets_requirement', True)
                        if required > 0 and estimated >= required and meets is False:
                            issues.append({
                                'check_type': 'experience_undercounting',
                                'description': (
                                    f"AI's own years_analysis shows {estimated:.1f}yr estimated vs "
                                    f"{required:.1f}yr required for '{skill}', but meets_requirement=false. "
                                    f"Direct self-contradiction — candidate may have sufficient experience."
                                )
                            })
                            break
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        if 'employment gap' in gaps:
            resume_header = ''
            if vetting_log.resume_text:
                resume_header = vetting_log.resume_text[:2500].lower()
            employment_current_indicators = ['present', 'current', 'ongoing', 'to date', 'till date']
            if resume_header and any(indicator in resume_header for indicator in employment_current_indicators):
                issues.append({
                    'check_type': 'employment_gap_misfire',
                    'description': (
                        f"Gaps mention 'employment gap' but the candidate's resume header "
                        f"contains current-employment indicators (e.g. 'Present'/'Current'). "
                        f"Possible false gap penalty on an actively employed candidate."
                    )
                })

        auth_flag_phrases = [
            'work authorization cannot be inferred',
            'limited us work history',
            'limited u.s. work history',
            'work authorization unconfirmed',
        ]
        auth_flagged = any(phrase in gaps for phrase in auth_flag_phrases)
        auth_inferred = 'scout screening infers strong likelihood' in match_summary
        if auth_flagged and auth_inferred:
            issues.append({
                'check_type': 'authorization_misfire',
                'description': (
                    f"Gaps flag work authorization concern but match_summary contains "
                    f"'Scout Screening infers strong likelihood' of authorization. "
                    f"AI contradicted itself — authorization inference and gap scoring conflict."
                )
            })

        return issues

    def _run_false_positive_checks(self, vetting_log, job_match) -> List[Dict]:
        """Tier-1 heuristics for Qualified false-positive detection.

        Looks for cases where a candidate scored above the threshold but the
        AI's own outputs contain signals suggesting the score is too high
        (e.g., gaps mention 2+ mandatory skills missing, or summary uses
        negative qualifiers, or years_analysis shows experience well below
        what was required).

        Returns a list of suspected issues. An empty list means the candidate
        looks like a genuine Qualified result and the AI confirmation step
        will be skipped.
        """
        issues: List[Dict] = []

        if not job_match:
            return issues

        gaps = (job_match.gaps_identified or '').lower()
        match_summary = (job_match.match_summary or '').lower()
        score = job_match.match_score or 0

        if score < 50:
            return issues

        mandatory_indicator_phrases = [
            'mandatory skill', 'required skill', 'critical requirement',
            'core requirement', 'must have', 'must-have',
            'no experience with', 'no evidence of', 'lacks experience',
            'missing required',
        ]
        mandatory_gap_count = sum(
            1 for phrase in mandatory_indicator_phrases if phrase in gaps
        )
        if mandatory_gap_count >= 2:
            issues.append({
                'check_type': 'false_positive_skill_gap',
                'description': (
                    f"Score is {score}% (Qualified) but gaps_identified flags "
                    f"{mandatory_gap_count} mandatory-skill concerns: "
                    f"'{(job_match.gaps_identified or '')[:200]}'. "
                    f"Possible false positive — recruiter may receive a candidate who "
                    f"is actually missing required skills."
                )
            })

        negative_qualifiers = [
            'limited experience', 'lacks', 'no evidence',
            'minimal experience', 'minimal exposure', 'shallow experience',
            'brief exposure', 'no demonstrated', 'no proof of',
            'has not demonstrated',
        ]
        negative_hits = [phrase for phrase in negative_qualifiers if phrase in match_summary]
        if negative_hits and score >= 70:
            issues.append({
                'check_type': 'false_positive_negative_summary',
                'description': (
                    f"Score is {score}% (Qualified) but match_summary contains "
                    f"negative qualifier(s): {negative_hits}. "
                    f"Summary text: '{(job_match.match_summary or '')[:200]}'. "
                    f"Possible inflated score — summary describes a weaker fit than "
                    f"the score implies."
                )
            })

        years_json_str = job_match.years_analysis_json
        if years_json_str:
            try:
                years_data = json.loads(years_json_str) if isinstance(years_json_str, str) else years_json_str
                if isinstance(years_data, dict):
                    shortfalls = []
                    for skill, data in years_data.items():
                        if not isinstance(data, dict):
                            continue
                        try:
                            required = float(data.get('required_years', 0) or 0)
                            estimated = float(
                                data.get('estimated_years', data.get('actual_years', 0)) or 0
                            )
                        except (ValueError, TypeError):
                            continue
                        meets = data.get('meets_requirement', True)
                        if required >= 3 and meets is True and estimated < (required * 0.5):
                            shortfalls.append(
                                f"{skill}: {estimated:.1f}yr vs {required:.1f}yr required"
                            )
                    if shortfalls:
                        issues.append({
                            'check_type': 'false_positive_experience_short',
                            'description': (
                                f"Score is {score}% (Qualified) but years_analysis shows "
                                f"the candidate has less than half the required experience "
                                f"on at least one mandatory skill while still marked "
                                f"meets_requirement=true: {'; '.join(shortfalls[:3])}. "
                                f"AI may have over-credited transferable experience."
                            )
                        })
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        return issues
