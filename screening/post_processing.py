import re
import logging
from screening.prestige import detect_prestige_employer

logger = logging.getLogger(__name__)


def _safe_float(value, default=0.0):
    """Coerce an AI-provided value to float, tolerating prose/None.

    gpt-4.1-mini occasionally returns years fields as prose
    (e.g. 'Not explicitly stated but strong hands-on required') or null
    where gpt-5.4 returned a number. Returns `default` (which may be None)
    when the value cannot be parsed, so callers can decide whether to skip
    a gate rather than crash the whole analysis.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return default


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
    # _safe_float tolerates explicit JSON null and prose strings (mini sometimes
    # emits these where gpt-5.4 always sent a number); int(None)/int("prose")
    # would otherwise crash here and zero the score for the whole job.
    result['match_score'] = int(_safe_float(result.get('match_score', 0), default=0.0))
    raw_tech = result.get('technical_score')
    if raw_tech is not None:
        result['technical_score'] = int(_safe_float(raw_tech, default=result['match_score']))
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
            required = _safe_float(data.get('required_years', 0), default=0.0)
            if required <= 0:
                continue
            estimated = _safe_float(data.get('estimated_years'), default=None)
            if estimated is None:
                logger.warning(
                    f"⚠️ Years gate: skipping '{skill}' for job {job_id} — "
                    f"non-numeric estimated_years ({data.get('estimated_years')!r})"
                )
                continue
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
    if most_recent_relevant is None:
        most_recent_relevant = True
    second_recent_relevant = recency_analysis.get('second_recent_role_relevant', True)
    if second_recent_relevant is None:
        second_recent_relevant = True
    # .get(key, 0) returns None when the AI emits an explicit JSON null (the
    # default only applies to a MISSING key), so coerce: mini sometimes nulls
    # these where gpt-5.4 always sent numbers, which crashed `months_since > 24`.
    months_since = int(_safe_float(recency_analysis.get('months_since_relevant_work', 0), default=0.0))
    ai_penalty = int(_safe_float(recency_analysis.get('penalty_applied', 0), default=0.0))

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


_CONTINUITY_GAP_ENFORCER_COUNTER = 0


def enforce_employment_continuity_gap(result, job_id):
    """
    Safety-net enforcer for the EMPLOYMENT CONTINUITY rule (system_prompt.py rule 15).

    The prompt instructs the AI to compute months_since_last_role and apply a
    tiered technical_score penalty (-8 / -12 / -15 for 12-23 / 24-35 / 36+ month
    gaps since the candidate's most recent role ended). When the AI silently
    skips this rule — typically anchored on a strong technical match — this
    enforcer catches the miss using the AI's own employment_gap_analysis.gap_months
    field and floors the penalty to the correct tier.

    Pairs with enforce_midcareer_gap (gaps BETWEEN roles) and
    enforce_recency_hard_gate (domain drift). This one covers gaps SINCE the
    most recent role ended — a previously uncovered failure mode.

    Fail-soft: if employment_gap_analysis is missing or malformed, no-op.
    """
    global _CONTINUITY_GAP_ENFORCER_COUNTER

    gap_analysis = result.get('employment_gap_analysis', {})
    if not isinstance(gap_analysis, dict) or not gap_analysis:
        return

    try:
        gap_months = int(gap_analysis.get('gap_months', 0))
    except (ValueError, TypeError):
        return

    raw_penalty = gap_analysis.get('penalty_applied', None)
    if raw_penalty is None or raw_penalty == '':
        return
    try:
        ai_penalty = abs(int(raw_penalty))
    except (ValueError, TypeError):
        return

    if gap_months >= 36:
        target_penalty = 15
    elif gap_months >= 24:
        target_penalty = 12
    elif gap_months >= 12:
        target_penalty = 8
    else:
        return

    if ai_penalty >= target_penalty:
        return

    delta = target_penalty - ai_penalty
    tech_before = result.get('technical_score', result['match_score'])
    result['technical_score'] = max(0, tech_before - delta)
    match_before = result['match_score']
    result['match_score'] = max(0, match_before - delta)

    last_end = gap_analysis.get('last_role_end_date', 'unknown')
    logger.info(
        f"📉 Continuity gap enforcer: AI applied {ai_penalty}pts but target is "
        f"{target_penalty}pts for {gap_months}-month gap (last role ended {last_end}). "
        f"Added delta {delta}pts for job {job_id}. "
        f"technical_score: {tech_before}→{result['technical_score']}, "
        f"match_score: {match_before}→{result['match_score']} "
        f"event=continuity_gap_enforced counter={_CONTINUITY_GAP_ENFORCER_COUNTER + 1}"
    )

    _CONTINUITY_GAP_ENFORCER_COUNTER += 1

    continuity_note = (
        f"Employment gap: candidate last employed {last_end} "
        f"({gap_months} months ago) — penalty -{target_penalty}pts."
    )
    existing_gaps = result.get('gaps_identified', '') or ''
    continuity_signature = 'candidate last employed'
    if continuity_signature not in existing_gaps.lower():
        if existing_gaps:
            result['gaps_identified'] = f"{existing_gaps} | {continuity_note}"
        else:
            result['gaps_identified'] = continuity_note


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
    professional_years = _safe_float(
        exp_class.get('total_professional_years', 3.0), default=3.0
    )

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
                required_yrs = _safe_float(data.get('required_years', 0), default=0.0)
                if data.get('meets_requirement') and required_yrs >= 3:
                    data['meets_requirement'] = False
                    data['estimated_years'] = min(
                        professional_years,
                        _safe_float(data.get('estimated_years'), default=professional_years)
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
                        req_yrs = _safe_float(data.get('required_years', 0), default=0.0)
                        if req_yrs <= 0:
                            continue
                        est_yrs = _safe_float(data.get('estimated_years'), default=None)
                        if est_yrs is None:
                            continue
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


# ── Security-clearance / work-authorization documentation enforcer ──
# Trigger phrases that indicate a job requires a security clearance (US or
# Canadian). Kept deliberately specific to avoid false positives on unrelated
# uses of the word "secret"/"reliability".
_CLEARANCE_TRIGGER_PHRASES = (
    'security clearance',
    'security clearances',
    'secret clearance',
    'secret-level',
    'active secret',
    'top secret',
    'ts/sci',
    'sci clearance',
    'sci eligibility',
    'reliability status',
    'enhanced reliability',
    'controlled goods',
    'pwgsc',
    'clearance eligible',
    'clearance-eligible',
    'clearance required',
    'dod secret',
    'nato secret',
    'government clearance',
    'security-clearance',
)


def _detect_clearance_level(requirements_text):
    """Best-effort clearance level name for the generic fallback message."""
    t = requirements_text.lower()
    if 'top secret' in t or 'ts/sci' in t:
        return 'Top Secret clearance'
    if 'secret' in t:
        return 'Secret clearance'
    if 'enhanced reliability' in t:
        return 'Enhanced Reliability clearance'
    if 'reliability' in t:
        return 'Reliability Status clearance'
    return 'a security clearance'


# Topic tokens that mark a field as *about* a security clearance. Includes
# Canadian clearance names so a verdict that omits the literal word "clearance"
# (e.g. "eligible for Reliability Status") is still recognized as documented.
_CLEARANCE_TOPIC_TOKENS = (
    'clearance',
    'reliability status',
    'enhanced reliability',
    'security clearance',
    'ts/sci',
    'top secret',
)

# Candidate-status cues that *might* signal a verdict about the candidate
# (holds / eligible / not evidenced / recruiter verification, etc.). On their own
# these are insufficient — some (e.g. "eligible", "authorized to work") also
# appear inside requirement-echo language ("candidate must be clearance
# eligible"). The requirement-echo guard below filters those out.
_CLEARANCE_VERDICT_CUES = (
    'holds', 'held', 'maintains', 'possess', 'does not hold',
    'no active', 'no clearance', 'without a clearance', 'lacks', 'lack of',
    'not evidenced', 'evidenced', 'no evidence', 'eligible', 'eligibility',
    'inferable', 'clearable', 'interim', 'obtainable', 'appears', 'inferred',
    'infers', 'likely', 'uncertain', 'unconfirmed', 'could not', 'cannot',
    'unable', 'recruiter verification', 'verification recommended',
    'self-identifies', 'self-reports', 'demonstrates', 'confirmed',
)

_WORK_AUTH_VERDICT_CUES = (
    'authorized to work', 'work-authorized', 'us-authorized', 'u.s.-authorized',
    'is authorized', 'not authorized', 'eligible to work', 'eligibility to work',
    'eligible', 'green card holder', 'permanent resident',
    'requires sponsorship', 'needs sponsorship', 'requires no sponsorship',
    'does not require sponsorship', 'no sponsorship needed',
    'does not need sponsorship', 'appears', 'inferred', 'infers', 'likely',
    'uncertain', 'unconfirmed', 'evidenced', 'could not', 'cannot', 'unable',
    'recruiter verification', 'verification recommended', 'self-identifies',
    'self-reports', 'confirmed', 'is a us citizen', 'is a u.s. citizen',
    'authorization status', 'work-authorization status',
)

# Requirement-echo markers: phrasing that states what the JOB demands rather than
# what the candidate IS. (Uses 'requires'/'required' but NOT bare 'require', so
# candidate verdicts like "does not require sponsorship" are not misflagged.)
_REQUIREMENT_MARKERS = (
    'requires', 'required', 'requirement', 'role requires',
    'must be', 'must have', 'must hold', 'must possess', 'must obtain',
    'needs to', 'need to', 'mandatory', 'should have', 'should be', 'should hold',
)

# Strong candidate-evidence markers: unambiguously about the candidate's actual
# status. When present they OVERRIDE the requirement-echo guard (a sentence may
# legitimately contain both the requirement and the candidate's status).
_STRONG_EVIDENCE_MARKERS = (
    'holds', 'held', 'currently hold', 'maintains', 'possesses',
    'does not hold', "doesn't hold", 'no active', 'not evidenced', 'evidenced',
    'no evidence', 'appears', 'inferred', 'infers', 'inferable',
    'self-identifies', 'self-reports', 'per resume', 'per the resume',
    'on the résumé', 'on the resume', 'on resume', 'demonstrates',
    'candidate is', 'candidate has', 'candidate holds', 'candidate appears',
    'recruiter verification', 'verification recommended', 'could not',
    'cannot', 'unable', 'not confirmed', 'unconfirmed', 'likely',
    'is authorized', 'is not authorized', 'not authorized',
    'is a us citizen', 'is a u.s. citizen', 'green card holder',
    'permanent resident', 'lacks', 'no clearance', 'without a clearance',
    'requires sponsorship', 'requires no sponsorship', 'needs sponsorship',
    'no sponsorship needed', 'does not require sponsorship',
    'does not need sponsorship',
)


def _is_verdict_text(text_lower, topic_tokens, verdict_cues):
    """True only if ``text_lower`` states a candidate *verdict* about the topic.

    A verdict requires a topic token AND a candidate-status cue. Requirement-echo
    text ("this role requires Secret clearance"; "candidate must be clearance
    eligible") carries the topic word and sometimes a cue word, but states what
    the job demands — not the candidate's status — so it is rejected UNLESS a
    strong candidate-evidence marker is also present.
    """
    if not any(t in text_lower for t in topic_tokens):
        return False
    if not any(c in text_lower for c in verdict_cues):
        return False
    if (any(m in text_lower for m in _REQUIREMENT_MARKERS)
            and not any(e in text_lower for e in _STRONG_EVIDENCE_MARKERS)):
        return False  # requirement echo, not a candidate verdict
    return True


def _field_documents_verdict(field, topic_tokens, verdict_cues):
    """True if ``field`` already states a candidate verdict about the topic."""
    if not field:
        return False
    return _is_verdict_text(field.lower(), topic_tokens, verdict_cues)


def _extract_verdict_sentence(text, topic_tokens, verdict_cues):
    """Pull the first sentence that states a candidate verdict.

    Applies the same per-sentence verdict test, so requirement-echo sentences are
    never copied across surfaces.
    """
    if not text:
        return None
    fragments = re.split(r'(?<=[.!?])\s+|\s*\|\s*', text)
    for frag in fragments:
        if _is_verdict_text(frag.lower(), topic_tokens, verdict_cues):
            frag = frag.strip()
            if len(frag) > 320:
                frag = frag[:317].rsplit(' ', 1)[0] + '…'
            return frag
    return None


def enforce_clearance_documentation(result, job_id, custom_requirements, job_description):
    """Guarantee a security-clearance verdict is documented in BOTH surfaces.

    The recruiter email renders only ``match_summary`` while the Bullhorn note
    renders both ``match_summary`` and ``gaps_identified``. The scoring prompt
    instructs the model to state the clearance/work-authorization verdict in
    both fields, but model compliance varies (observed in production: a
    clearance gap surfaced in gaps with no eligibility verdict, and nothing in
    the summary). This safety net ensures that for any clearance-triggered job,
    an explicit clearance statement is present in both fields.

    Documentation-only: NEVER changes ``match_score`` / ``technical_score`` /
    banding. Fully fail-soft.
    """
    try:
        # Trigger detection uses the authoritative requirement sources only
        # (recruiter-entered custom requirements + the JD). The model-generated
        # key_requirements is intentionally EXCLUDED so a hallucinated clearance
        # mention cannot make the enforcer inject a clearance line on a job that
        # does not actually require one. This also matches the prompt rules,
        # which name the job description as the primary clearance trigger.
        requirements_text = ' '.join(filter(None, [
            custom_requirements or '',
            job_description or '',
        ])).lower()
        if not requirements_text:
            return
        if not any(p in requirements_text for p in _CLEARANCE_TRIGGER_PHRASES):
            return

        summary = result.get('match_summary', '') or ''
        gaps = result.get('gaps_identified', '') or ''
        topic = _CLEARANCE_TOPIC_TOKENS
        in_summary = _field_documents_verdict(summary, topic, _CLEARANCE_VERDICT_CUES)
        in_gaps = _field_documents_verdict(gaps, topic, _CLEARANCE_VERDICT_CUES)

        if in_summary and in_gaps:
            return  # model already documented a verdict on both surfaces

        # Prefer the model's own verdict wording (avoids contradicting it); only
        # copy a sentence that actually states a candidate verdict, never a bare
        # requirement echo. Fall back to a transparent generic line otherwise.
        canonical = (
            _extract_verdict_sentence(summary, topic, _CLEARANCE_VERDICT_CUES)
            or _extract_verdict_sentence(gaps, topic, _CLEARANCE_VERDICT_CUES)
        )
        if not canonical:
            level = _detect_clearance_level(requirements_text)
            canonical = (
                f"Scout Screening notes this role requires {level}; no active "
                f"clearance is evidenced on the résumé and clearance eligibility "
                f"could not be automatically determined — recruiter verification "
                f"recommended."
            )

        if not in_summary:
            if summary.strip():
                result['match_summary'] = f"{summary.rstrip().rstrip('.')}. {canonical}"
            else:
                result['match_summary'] = canonical
        if not in_gaps:
            if gaps.strip():
                result['gaps_identified'] = (
                    f"{gaps.rstrip().rstrip('|').rstrip()} | {canonical}"
                )
            else:
                result['gaps_identified'] = canonical

        logger.info(
            f"🛡️ Clearance documentation enforcer: ensured clearance verdict in "
            f"both fields for job {job_id} (was in_summary={in_summary}, "
            f"in_gaps={in_gaps})"
        )
    except Exception as _clearance_doc_err:
        logger.debug(
            f"Clearance documentation enforcer skipped (non-fatal) for "
            f"job {job_id}: {_clearance_doc_err}"
        )


# ── US work-authorization documentation enforcer ──
# Trigger phrases that indicate a job requires US work authorization (Rule 1).
# Detected from the recruiter-entered requirements + JD only (NOT the
# model-generated key_requirements). Kept specific to avoid false positives.
_WORK_AUTH_TRIGGER_PHRASES = (
    'us citizen',
    'u.s. citizen',
    'us citizenship',
    'u.s. citizenship',
    'citizenship required',
    'must be a us citizen',
    'must be a u.s. citizen',
    'authorized to work in the us',
    'authorized to work in the u.s',
    'us work authorization',
    'u.s. work authorization',
    'work authorization required',
    'no sponsorship',
    'will not sponsor',
    'unable to sponsor',
    'cannot sponsor',
    'w2 only',
    'w-2 only',
    'no c2c',
    'no corp to corp',
    'no corp-to-corp',
    'green card',
    'permanent resident',
)

# Topic tokens that mark a field as *about* US work authorization. Pairing these
# with _WORK_AUTH_VERDICT_CUES is what separates a candidate verdict from a bare
# requirement echo (see _field_documents_verdict).
_WORK_AUTH_TOPIC_TOKENS = (
    'authoriz',
    'citizen',
    'sponsor',
    'green card',
    'permanent resident',
    'work auth',
)


def enforce_work_authorization_documentation(result, job_id, custom_requirements, job_description):
    """Guarantee a US work-authorization verdict is documented in BOTH surfaces.

    Mirror of ``enforce_clearance_documentation`` for Rule 1 (US work
    authorization) jobs. The recruiter email renders only ``match_summary``
    while the Bullhorn note renders both fields, so a verdict in only one field
    is invisible on one surface. This safety net guarantees an explicit
    work-authorization statement in both fields for any auth-triggered job.

    Documentation-only: NEVER changes ``match_score`` / ``technical_score`` /
    banding. Fully fail-soft.
    """
    try:
        requirements_text = ' '.join(filter(None, [
            custom_requirements or '',
            job_description or '',
        ])).lower()
        if not requirements_text:
            return
        if not any(p in requirements_text for p in _WORK_AUTH_TRIGGER_PHRASES):
            return

        summary = result.get('match_summary', '') or ''
        gaps = result.get('gaps_identified', '') or ''
        in_summary = _field_documents_verdict(
            summary, _WORK_AUTH_TOPIC_TOKENS, _WORK_AUTH_VERDICT_CUES)
        in_gaps = _field_documents_verdict(
            gaps, _WORK_AUTH_TOPIC_TOKENS, _WORK_AUTH_VERDICT_CUES)

        if in_summary and in_gaps:
            return  # model already documented a verdict on both surfaces

        canonical = (
            _extract_verdict_sentence(
                summary, _WORK_AUTH_TOPIC_TOKENS, _WORK_AUTH_VERDICT_CUES)
            or _extract_verdict_sentence(
                gaps, _WORK_AUTH_TOPIC_TOKENS, _WORK_AUTH_VERDICT_CUES)
        )
        if not canonical:
            canonical = (
                "Scout Screening notes this role requires US work authorization; "
                "the résumé does not explicitly confirm work-authorization status "
                "and it could not be automatically inferred — recruiter "
                "verification recommended."
            )

        if not in_summary:
            if summary.strip():
                result['match_summary'] = f"{summary.rstrip().rstrip('.')}. {canonical}"
            else:
                result['match_summary'] = canonical
        if not in_gaps:
            if gaps.strip():
                result['gaps_identified'] = (
                    f"{gaps.rstrip().rstrip('|').rstrip()} | {canonical}"
                )
            else:
                result['gaps_identified'] = canonical

        logger.info(
            f"🛡️ Work-authorization documentation enforcer: ensured verdict in "
            f"both fields for job {job_id} (was in_summary={in_summary}, "
            f"in_gaps={in_gaps})"
        )
    except Exception as _wa_doc_err:
        logger.debug(
            f"Work-authorization documentation enforcer skipped (non-fatal) for "
            f"job {job_id}: {_wa_doc_err}"
        )
