import re
import logging
from screening.prestige import detect_prestige_employer

logger = logging.getLogger(__name__)

PLATFORM_AGE_CEILINGS = {
    'databricks': 8.0,
    'delta lake': 7.0,
    'azure synapse': 6.0,
    'azure synapse analytics': 6.0,
    'microsoft fabric': 3.0,
    'snowflake': 10.0,
    'dbt': 8.0,
    'data build tool': 8.0,
    'apache flink': 10.0,
    'kubernetes': 10.0,
    'apache kafka': 14.0,
}


def normalize_response_fields(result, job_id):
    for field in ['gaps_identified', 'match_summary', 'skills_match', 'experience_match', 'key_requirements']:
        if isinstance(result.get(field), list):
            result[field] = ". ".join(str(item) for item in result[field])
            logger.warning(f"Normalized {field} from array to string for job {job_id}")


def coerce_scores(result, job_id):
    raw_score = result.get('match_score')
    logger.info(f"📊 Raw GPT score for job {job_id}: {raw_score} (type: {type(raw_score).__name__})")
    result['match_score'] = int(result.get('match_score', 0))
    raw_tech = result.get('technical_score')
    if raw_tech is not None:
        result['technical_score'] = int(raw_tech)
    else:
        result['technical_score'] = result['match_score']


def enforce_remote_location(result, job_id, work_type):
    if work_type != 'Remote':
        return

    _gaps_text = (result.get('gaps_identified') or '').lower()
    _summary_text = (result.get('match_summary') or '').lower()

    if 'location mismatch: different country' not in _gaps_text and 'different country' not in _gaps_text:
        return

    _negation_words = ['not ', "n't ", 'no ', 'does not ', 'doesn\'t ', 'cannot ', 'outside ']
    _same_country_evidence = [
        'matches the remote job',
        'matches the location requirement',
        'meets the location requirement',
        'meets the remote location',
        'matching the remote job',
        "matches the job's country",
        "matches the remote job's country",
        "matches the job's remote location",
        "matching the job location requirement",
        "matching the job's country requirement",
        "same country as the job",
        "matches the country requirement",
        "located in the same country",
        "resides in the same country",
        "based in the same country",
    ]

    def _has_affirmative_evidence(_text, _phrases, _negations):
        for phrase in _phrases:
            idx = _text.find(phrase)
            if idx >= 0:
                _context_start = max(0, idx - 20)
                _preceding = _text[_context_start:idx]
                if not any(neg in _preceding for neg in _negations):
                    return True
        return False

    if _has_affirmative_evidence(_summary_text, _same_country_evidence, _negation_words):
        _original_gaps = result.get('gaps_identified', '')
        _cleaned_gaps = re.sub(
            r'Location mismatch: different country\.?\s*Technical fit:\s*\d+%\.?\s*Location penalty:\s*-?\d+\s*pts\.?\s*',
            '', _original_gaps, flags=re.IGNORECASE
        ).strip()
        _cleaned_gaps = re.sub(
            r'Location mismatch: different country\.?\s*',
            '', _cleaned_gaps, flags=re.IGNORECASE
        ).strip()
        _cleaned_gaps = re.sub(r'^[\s|.]+|[\s|.]+$', '', _cleaned_gaps).strip()
        result['gaps_identified'] = _cleaned_gaps
        _restored_score = min(
            result['match_score'] + 25,
            result.get('technical_score', result['match_score'] + 25)
        )
        logger.warning(
            f"🛡️ REMOTE LOCATION ENFORCER: Fixed misfire for job {job_id}. "
            f"AI said same-country in summary but applied different-country penalty in gaps. "
            f"Score restored: {result['match_score']}→{_restored_score}. "
            f"Gaps cleaned: '{_original_gaps[:100]}' → '{_cleaned_gaps[:100]}'"
        )
        result['match_score'] = _restored_score


def _apply_platform_ceiling(skill, required, job_id):
    skill_lower = skill.lower()
    for platform_key, platform_ceiling in PLATFORM_AGE_CEILINGS.items():
        if platform_key in skill_lower:
            if required > platform_ceiling:
                logger.info(
                    f"📐 Platform age ceiling applied for job {job_id}: "
                    f"'{skill}' requires {required:.0f}yr but platform max is ~{platform_ceiling:.0f}yr. "
                    f"Effective requirement adjusted to {platform_ceiling:.0f}yr."
                )
                return platform_ceiling
            break
    return required


def _compute_shortfalls(years_analysis, job_id):
    max_shortfall = 0.0
    shortfall_details = []
    for skill, data in years_analysis.items():
        if not isinstance(data, dict):
            continue
        meets = data.get('meets_requirement', True)
        if not meets:
            required = float(data.get('required_years', 0))
            if required <= 0:
                continue
            estimated = float(data.get('estimated_years', 0))
            required = _apply_platform_ceiling(skill, required, job_id)
            data['required_years'] = required
            shortfall = required - estimated
            if shortfall > max_shortfall:
                max_shortfall = shortfall
            shortfall_details.append(
                f"CRITICAL: {skill} requires {required:.0f}yr, candidate has ~{estimated:.1f}yr"
            )
    return max_shortfall, shortfall_details


def enforce_years_hard_gate(result, job_id, job_title, resume_text, recheck_fn):
    years_analysis = result.get('years_analysis', {})
    if not isinstance(years_analysis, dict) or not years_analysis:
        return

    original_score = result['match_score']
    max_shortfall, shortfall_details = _compute_shortfalls(years_analysis, job_id)

    if max_shortfall >= 2.0:
        recheck_result = recheck_fn(resume_text, years_analysis, job_id, job_title)
        if recheck_result:
            years_analysis = recheck_result
            result['years_analysis'] = recheck_result
            max_shortfall, shortfall_details = _compute_shortfalls(years_analysis, job_id)

        if max_shortfall >= 2.0:
            if result['match_score'] > 60:
                result['match_score'] = 60
                logger.info(
                    f"📉 Years hard gate: capped score {original_score}→60 for job {job_id} "
                    f"(shortfall: {max_shortfall:.1f}yr, confirmed by re-check)"
                )
        elif max_shortfall >= 1.0:
            result['match_score'] = max(0, result['match_score'] - 15)
            if result['match_score'] != original_score:
                logger.info(
                    f"📉 Years penalty: reduced score {original_score}→{result['match_score']} for job {job_id} "
                    f"(shortfall: {max_shortfall:.1f}yr, adjusted after re-check)"
                )
        else:
            logger.info(
                f"✅ Years re-check OVERTURNED shortfall for job {job_id}: "
                f"now meets requirements (max remaining shortfall: {max_shortfall:.1f}yr). "
                f"Score {original_score} preserved."
            )
    elif max_shortfall >= 1.0:
        result['match_score'] = max(0, result['match_score'] - 15)
        if result['match_score'] != original_score:
            logger.info(
                f"📉 Years penalty: reduced score {original_score}→{result['match_score']} for job {job_id} "
                f"(shortfall: {max_shortfall:.1f}yr)"
            )

    if shortfall_details:
        existing_gaps = result.get('gaps_identified', '') or ''
        gap_suffix = ' | '.join(shortfall_details)
        if existing_gaps:
            result['gaps_identified'] = f"{existing_gaps} | {gap_suffix}"
        else:
            result['gaps_identified'] = gap_suffix


def enforce_recency_hard_gate(result, job_id):
    recency_analysis = result.get('recency_analysis', {})
    if not isinstance(recency_analysis, dict) or not recency_analysis:
        return

    recency_original_score = result['match_score']
    most_recent_relevant = recency_analysis.get('most_recent_role_relevant', True)
    second_recent_relevant = recency_analysis.get('second_recent_role_relevant', True)
    months_since = recency_analysis.get('months_since_relevant_work', 0)
    ai_penalty = recency_analysis.get('penalty_applied', 0)

    if most_recent_relevant:
        _justification = str(recency_analysis.get('relevance_justification', '') or '').strip()
        _WEAK_INDICATORS = [
            'transferable skills', 'transferable',
            'general experience', 'general work experience',
            'work ethic', 'reliable', 'reliability',
            'communication skills', 'teamwork',
            'soft skills', 'people skills',
            'has work experience', 'has experience',
        ]
        _justification_lower = _justification.lower()
        _is_invalid = (
            not _justification
            or _justification == 'N/A'
            or len(_justification) < 15
            or any(wp in _justification_lower for wp in _WEAK_INDICATORS)
        )
        if _is_invalid:
            logger.warning(
                f"🔍 JUSTIFICATION ENFORCER: AI marked most_recent_role_relevant=True "
                f"for job {job_id} but justification is missing/weak: '{_justification[:100]}'. "
                f"Overriding to relevant=False."
            )
            most_recent_relevant = False
            recency_analysis['most_recent_role_relevant'] = False

    if not most_recent_relevant and months_since > 24:
        most_recent_role_str = str(recency_analysis.get('most_recent_role', '')).lower()
        recency_domain_keywords = [
            'data engineer', 'azure data', 'databricks', 'spark', 'etl', 'pipeline',
            'data lake', 'synapse', 'snowflake', 'cloud engineer', 'big data',
            'data warehouse', 'kafka', 'airflow', 'dbt', 'analytics engineer',
            'machine learning', 'ml engineer', 'ai engineer', 'software engineer',
            'devops', 'platform engineer', 'backend engineer', 'data architect',
        ]
        if any(kw in most_recent_role_str for kw in recency_domain_keywords):
            logger.warning(
                f"⚠️ Recency gate misfire detected for job {job_id}: "
                f"AI reported most_recent_role_relevant=False with months_since={months_since} "
                f"but most_recent_role='{recency_analysis.get('most_recent_role', '')}' "
                f"contains domain-relevant keywords. Overriding to relevant=True."
            )
            most_recent_relevant = True
            recency_analysis['most_recent_role_relevant'] = True
            recency_analysis['months_since_relevant_work'] = 0
            months_since = 0
            ai_penalty = 0

    if not most_recent_relevant and not second_recent_relevant:
        target_penalty = 20
        recency_note = (
            "Candidate has not practiced relevant skills in their last two positions; "
            "career trajectory has shifted away from this domain."
        )
    elif not most_recent_relevant and months_since >= 12:
        target_penalty = 12
        recency_note = (
            "Candidate's most recent professional activity is outside the target domain; "
            "relevant experience is not current."
        )
    else:
        target_penalty = 0
        recency_note = None

    if target_penalty > 0:
        effective_penalty = max(target_penalty, ai_penalty)
        new_score = max(0, recency_original_score - effective_penalty)

        if new_score < result['match_score']:
            result['match_score'] = new_score
            logger.info(
                f"📉 Recency hard gate: reduced score {recency_original_score}→{new_score} "
                f"for job {job_id} (penalty: {effective_penalty}pts, "
                f"months_since_relevant: {months_since})"
            )

        if recency_note:
            existing_gaps = result.get('gaps_identified', '') or ''
            if existing_gaps:
                result['gaps_identified'] = f"{existing_gaps} | {recency_note}"
            else:
                result['gaps_identified'] = recency_note


def enforce_midcareer_gap(result, job_id):
    _gap_analysis = result.get('employment_gap_analysis', {})
    if not isinstance(_gap_analysis, dict) or not _gap_analysis:
        return

    _midcareer_gap_months = 0
    try:
        _midcareer_gap_months = int(_gap_analysis.get('largest_midcareer_gap_months', 0))
    except (ValueError, TypeError):
        pass
    _ai_midcareer_penalty = 0
    try:
        _ai_midcareer_penalty = abs(int(_gap_analysis.get('midcareer_gap_penalty_applied', 0)))
    except (ValueError, TypeError):
        pass

    if _midcareer_gap_months >= 24:
        _target_midcareer_penalty = 7
    elif _midcareer_gap_months >= 12:
        _target_midcareer_penalty = 4
    else:
        _target_midcareer_penalty = 0

    if _target_midcareer_penalty > 0 and _ai_midcareer_penalty < _target_midcareer_penalty:
        _delta = _target_midcareer_penalty - _ai_midcareer_penalty
        _tech_before = result.get('technical_score', result['match_score'])
        result['technical_score'] = max(0, _tech_before - _delta)
        _match_before = result['match_score']
        result['match_score'] = max(0, _match_before - _delta)
        _gap_between = _gap_analysis.get('midcareer_gap_between', 'unknown')
        logger.info(
            f"📉 Mid-career gap enforcer: AI applied {_ai_midcareer_penalty}pts but "
            f"target is {_target_midcareer_penalty}pts for {_midcareer_gap_months}-month gap. "
            f"Added delta {_delta}pts for job {job_id}. "
            f"technical_score: {_tech_before}→{result['technical_score']}, "
            f"match_score: {_match_before}→{result['match_score']} "
            f"(between: {_gap_between})"
        )

        _midcareer_note = (
            f"Mid-career employment gap: {_midcareer_gap_months} months between roles "
            f"({_gap_between})."
        )
        existing_gaps = result.get('gaps_identified', '') or ''
        if 'mid-career' not in existing_gaps.lower():
            if existing_gaps:
                result['gaps_identified'] = f"{existing_gaps} | {_midcareer_note}"
            else:
                result['gaps_identified'] = _midcareer_note


def enforce_experience_floor(result, job_id, custom_requirements, job_description):
    exp_class = result.get('experience_level_classification', {})
    if not isinstance(exp_class, dict) or not exp_class:
        return

    classification = exp_class.get('classification', '').upper()
    highest_role = exp_class.get('highest_role_type', '').upper()
    professional_years = 3.0
    try:
        professional_years = float(exp_class.get('total_professional_years', 3.0))
    except (ValueError, TypeError):
        pass

    requirements_text_combined = ' '.join(filter(None, [
        custom_requirements or '',
        result.get('key_requirements', ''),
        job_description or ''
    ]))
    years_match = re.search(
        r'(?:minimum\s+)?(\d+)\+?\s*years?\s*(?:of\s+)?(?:experience|professional)',
        requirements_text_combined, re.IGNORECASE
    )
    required_min_years = int(years_match.group(1)) if years_match else 0

    exp_floor_original_score = result['match_score']

    if classification in ('FRESH_GRAD', 'ENTRY') and required_min_years >= 3:
        if result['match_score'] > 55:
            result['match_score'] = 55
            logger.info(
                f"📉 Experience floor: capped {exp_floor_original_score}→55 "
                f"for job {job_id} (classification={classification}, "
                f"professional_years={professional_years:.1f}, "
                f"required={required_min_years}yr)"
            )
            floor_gap = (
                f"Experience floor: candidate classified as {classification} "
                f"({professional_years:.1f}yr professional) vs {required_min_years}yr required."
            )
            existing_gaps = result.get('gaps_identified', '') or ''
            if existing_gaps:
                result['gaps_identified'] = f"{existing_gaps} | {floor_gap}"
            else:
                result['gaps_identified'] = floor_gap

    if (highest_role in ('INTERNSHIP_ONLY', 'ACADEMIC_ONLY') or professional_years < 1.0):
        years_analysis = result.get('years_analysis', {})
        if isinstance(years_analysis, dict):
            overridden = False
            for skill, data in years_analysis.items():
                if not isinstance(data, dict):
                    continue
                required_yrs = float(data.get('required_years', 0))
                if data.get('meets_requirement') and required_yrs >= 3:
                    data['meets_requirement'] = False
                    data['estimated_years'] = min(
                        professional_years,
                        float(data.get('estimated_years', 0))
                    )
                    overridden = True
                    logger.warning(
                        f"⚠️ Experience floor override: {skill} "
                        f"meets_requirement forced to false for job {job_id} "
                        f"(intern-only profile, {professional_years:.1f}yr professional)"
                    )

            if overridden:
                result['years_analysis'] = years_analysis
                max_shortfall_recheck = 0.0
                shortfall_details_recheck = []
                for skill, data in years_analysis.items():
                    if not isinstance(data, dict):
                        continue
                    if not data.get('meets_requirement', True):
                        req_yrs = float(data.get('required_years', 0))
                        if req_yrs <= 0:
                            continue
                        est_yrs = float(data.get('estimated_years', 0))
                        shortfall = req_yrs - est_yrs
                        if shortfall > max_shortfall_recheck:
                            max_shortfall_recheck = shortfall
                        shortfall_details_recheck.append(
                            f"CRITICAL: {skill} requires {req_yrs:.0f}yr, "
                            f"candidate has ~{est_yrs:.1f}yr"
                        )

                if max_shortfall_recheck >= 2.0 and result['match_score'] > 60:
                    result['match_score'] = min(result['match_score'], 60)
                    logger.info(
                        f"📉 Experience floor re-check: capped at 60 for job {job_id} "
                        f"(shortfall: {max_shortfall_recheck:.1f}yr after override)"
                    )
                elif max_shortfall_recheck >= 1.0:
                    new_score = max(0, result['match_score'] - 15)
                    if new_score < result['match_score']:
                        result['match_score'] = new_score

                if shortfall_details_recheck:
                    existing_gaps = result.get('gaps_identified', '') or ''
                    for detail in shortfall_details_recheck:
                        if detail not in existing_gaps:
                            if existing_gaps:
                                existing_gaps = f"{existing_gaps} | {detail}"
                            else:
                                existing_gaps = detail
                    result['gaps_identified'] = existing_gaps

    if (highest_role in ('INTERNSHIP_ONLY', 'ACADEMIC_ONLY') and
            professional_years < 1.0 and result['match_score'] > 65):
        gate3_original = result['match_score']
        result['match_score'] = 65
        logger.info(
            f"📉 Experience floor (catch-all): capped {gate3_original}→65 "
            f"for job {job_id} (highest_role={highest_role}, "
            f"professional_years={professional_years:.1f})"
        )
        floor_gap = (
            f"Experience floor: candidate has only {highest_role.lower().replace('_', ' ')} "
            f"roles ({professional_years:.1f}yr professional)."
        )
        existing_gaps = result.get('gaps_identified', '') or ''
        if 'experience floor' not in existing_gaps.lower():
            if existing_gaps:
                result['gaps_identified'] = f"{existing_gaps} | {floor_gap}"
            else:
                result['gaps_identified'] = floor_gap


def apply_prestige_detection(result, job_id, resume_text):
    _prestige_firm = detect_prestige_employer(resume_text)
    if _prestige_firm:
        result['_prestige_employer'] = _prestige_firm
        logger.info(f"🏢 Prestige employer detected for job {job_id}: {_prestige_firm}")


def apply_location_barrier(result, job_id, work_type):
    _gaps_text = result.get('gaps_identified', '') or ''
    if work_type in ('On-site', 'Hybrid') and 'location mismatch' in _gaps_text.lower():
        result['is_location_barrier'] = True
        logger.info(
            f"📍 Location barrier detected for job {job_id}: "
            f"work_type={work_type}, score={result['match_score']}"
        )
