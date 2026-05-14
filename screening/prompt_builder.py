"""
Prompt Builder - AI prompt construction, GPT calls, and response post-processing.

Contains:
- PromptBuilderMixin: Mixin class with AI analysis methods
- analyze_candidate_job_match: Main AI analysis prompt + post-processing gates
- extract_job_requirements: AI extraction of mandatory job requirements
- _recheck_years_calculation: Focused GPT re-check for years-of-experience arithmetic
- _build_experience_match: Static helper to build experience_match string from recency data

Sub-modules:
- screening.prestige: Prestige employer constants and detection
- screening.system_prompt: System/location prompt construction
- screening.post_processing: Defense-in-depth post-processing gates
"""

import json
import os
import re
import time
import logging
import threading
from datetime import datetime, date, timedelta
from typing import Dict, Optional

from vetting.geo_utils import smart_correct_country, normalize_country, map_work_type
from screening.prestige import (
    PRESTIGE_FIRMS,
    PRESTIGE_DISPLAY_NAMES,
    PRESTIGE_BOOST_POINTS,
    detect_prestige_employer,
)
from screening.system_prompt import build_system_message, build_location_instruction


# --- Shadow A/B harness (S2) -------------------------------------------------
# Module-level helpers + per-hour rate cap. Behavior is gated entirely by the
# env var SCREENING_AB_SHADOW_ENABLED (default off). When the cap is hit, the
# shadow path is silently skipped — production scoring is never affected.
_SHADOW_RATE_LOCK = threading.Lock()
_SHADOW_RATE_WINDOW_START: float = 0.0
_SHADOW_RATE_COUNT: int = 0


def _shadow_screening_enabled() -> bool:
    """Read SCREENING_AB_SHADOW_ENABLED env var. Off by default."""
    return os.environ.get('SCREENING_AB_SHADOW_ENABLED', '').lower() in ('true', '1', 'yes')


def _shadow_screening_max_per_hour() -> int:
    """Per-hour cap on shadow scoring calls (env SCREENING_AB_SHADOW_MAX_CALLS_PER_HOUR).
    Default 100. Set to 0 for unlimited (not recommended in production)."""
    raw = os.environ.get('SCREENING_AB_SHADOW_MAX_CALLS_PER_HOUR', '100')
    try:
        n = int(raw)
        return n if n >= 0 else 100
    except (TypeError, ValueError):
        return 100


def _shadow_screening_pick_model(prod_model: str) -> str:
    """Pick the OTHER model for shadow comparison.
    If prod is gpt-5.4 (current), shadow is gpt-4.1-mini. Otherwise shadow is
    gpt-5.4 (regression-watch on a downgraded prod)."""
    p = (prod_model or '').lower()
    if 'mini' in p or '4.1' in p:
        return 'gpt-5.4'
    return 'gpt-4.1-mini'


def _shadow_screening_rate_check() -> bool:
    """Atomic check + increment of the per-hour rate counter.
    Returns True if the call is allowed, False if the cap is hit."""
    global _SHADOW_RATE_WINDOW_START, _SHADOW_RATE_COUNT
    cap = _shadow_screening_max_per_hour()
    if cap == 0:
        return True
    now = time.time()
    with _SHADOW_RATE_LOCK:
        # Roll the window forward every 3600s
        if now - _SHADOW_RATE_WINDOW_START >= 3600.0:
            _SHADOW_RATE_WINDOW_START = now
            _SHADOW_RATE_COUNT = 0
        if _SHADOW_RATE_COUNT >= cap:
            return False
        _SHADOW_RATE_COUNT += 1
        return True


def _save_screening_ab_row(row: Dict) -> None:
    """Insert one shadow A/B row on an ISOLATED transaction.

    Critical: shadow logging must NEVER affect the calling request's ORM
    session. We use a short-lived raw connection so a write failure rolls
    back only the AB insert — not any pending production writes the caller
    has staged. Fully fail-soft.
    """
    try:
        from app import db, app
        from flask import has_app_context
        from sqlalchemy import text as _text
        sql = _text(
            "INSERT INTO screening_ab_log "
            "(vetting_log_id, candidate_job_match_id, bullhorn_candidate_id, "
            " bullhorn_job_id, job_title, prod_model, shadow_model, "
            " prod_score, shadow_score, score_delta, "
            " prod_qualified, shadow_qualified_inferred, "
            " shadow_input_tokens, shadow_output_tokens, "
            " shadow_estimated_cost_usd, shadow_duration_ms, shadow_error, "
            " created_at) "
            "VALUES "
            "(:vetting_log_id, :candidate_job_match_id, :bullhorn_candidate_id, "
            " :bullhorn_job_id, :job_title, :prod_model, :shadow_model, "
            " :prod_score, :shadow_score, :score_delta, "
            " :prod_qualified, :shadow_qualified_inferred, "
            " :shadow_input_tokens, :shadow_output_tokens, "
            " :shadow_estimated_cost_usd, :shadow_duration_ms, :shadow_error, "
            " :created_at)"
        )
        row.setdefault('created_at', datetime.utcnow())
        # Ensure a Flask app context is active before touching db.engine —
        # the screening pipeline runs both inside Flask requests AND from
        # APScheduler background threads (which have no implicit context).
        # Skip the push/pop overhead on the request-path where a context
        # already exists.
        if has_app_context():
            with db.engine.begin() as conn:
                conn.execute(sql, row)
        else:
            with app.app_context():
                with db.engine.begin() as conn:
                    conn.execute(sql, row)
    except Exception as exc:
        # Isolated connection auto-rolls-back on context exit. Caller's
        # ORM session is untouched.
        logging.getLogger(__name__).warning(
            f"Failed to save screening A/B row: {exc}"
        )


def _run_screening_shadow(
    *,
    system_message: str,
    user_prompt: str,
    prod_model: str,
    prod_score: float,
    prod_qualified: Optional[bool],
    job_id,
    job_title: str,
    openai_client,
) -> None:
    """Run the gpt-4.1-mini shadow scoring call and log the comparison.

    Fully fail-soft: any error is swallowed so production scoring is never
    affected. Cost-tagged via log_call(site_id='screening.scoring.shadow').
    """
    try:
        if not _shadow_screening_enabled():
            return
        if openai_client is None:
            return
        if not _shadow_screening_rate_check():
            # Rate cap hit — skip silently
            return

        shadow_model = _shadow_screening_pick_model(prod_model)
        from services.openai_helper import log_call, _extract_usage, estimate_cost

        # Use the explicit shadow model (no env override): we want the actual
        # comparison candidate, not whatever the env may have rerouted.
        call_model = shadow_model

        t0 = time.time()
        shadow_error: Optional[str] = None
        shadow_score: Optional[float] = None
        shadow_qualified: Optional[bool] = None
        shadow_input_tokens: Optional[int] = None
        shadow_output_tokens: Optional[int] = None
        shadow_cost_usd = None
        shadow_response = None

        try:
            shadow_response = openai_client.chat.completions.create(
                model=call_model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=3750,
            )
            log_call('screening.scoring.shadow', call_model, shadow_response)

            # Compute cost deterministically from THIS response's usage data
            # (avoids racing other workers' inserts into openai_call_log).
            try:
                inp_tok, cached_tok, out_tok = _extract_usage(shadow_response)
                shadow_input_tokens = inp_tok
                shadow_output_tokens = out_tok
                shadow_cost_usd = estimate_cost(call_model, inp_tok, cached_tok, out_tok)
            except Exception:
                pass

            content = shadow_response.choices[0].message.content if shadow_response.choices else None
            if content and content.strip():
                try:
                    parsed = json.loads(content)
                    raw_score = parsed.get('match_score')
                    if raw_score is not None:
                        shadow_score = float(raw_score)
                        # Mini does NOT run prod's post-processing gates (this is
                        # measurement-only). qualified_inferred is the raw model
                        # output crossing the conventional 80 floor.
                        shadow_qualified = shadow_score >= 80.0
                except (ValueError, TypeError) as parse_err:
                    shadow_error = f"parse_error: {parse_err}"[:500]
            else:
                finish_reason = (
                    shadow_response.choices[0].finish_reason
                    if shadow_response.choices else 'unknown'
                )
                shadow_error = f"empty_response (finish={finish_reason})"[:500]
        except Exception as call_err:
            shadow_error = f"call_error: {call_err}"[:500]

        shadow_duration_ms = int((time.time() - t0) * 1000)
        score_delta = (shadow_score - prod_score) if shadow_score is not None else None

        _save_screening_ab_row({
            'vetting_log_id': None,
            'candidate_job_match_id': None,
            'bullhorn_candidate_id': None,
            'bullhorn_job_id': int(job_id) if isinstance(job_id, (int, str)) and str(job_id).isdigit() else None,
            'job_title': (job_title or '')[:500],
            'prod_model': prod_model,
            'shadow_model': shadow_model,
            'prod_score': float(prod_score),
            'shadow_score': shadow_score,
            'score_delta': score_delta,
            'prod_qualified': prod_qualified,
            'shadow_qualified_inferred': shadow_qualified,
            'shadow_input_tokens': shadow_input_tokens,
            'shadow_output_tokens': shadow_output_tokens,
            'shadow_estimated_cost_usd': shadow_cost_usd,
            'shadow_duration_ms': shadow_duration_ms,
            'shadow_error': shadow_error,
        })
    except Exception as outer_err:
        # Absolute outermost guard — shadow path can NEVER raise.
        logging.getLogger(__name__).warning(
            f"Screening shadow A/B outer failure (suppressed): {outer_err}"
        )
from screening.post_processing import (
    normalize_response_fields,
    coerce_scores,
    enforce_remote_location,
    enforce_years_hard_gate,
    enforce_recency_hard_gate,
    enforce_employment_continuity_gap,
    enforce_midcareer_gap,
    enforce_experience_floor,
    apply_prestige_detection,
    apply_location_barrier,
)

logger = logging.getLogger(__name__)


class PromptBuilderMixin:
    """AI prompt construction and GPT response post-processing."""

    @staticmethod
    def _build_experience_match(analysis: dict) -> str:
        base = analysis.get('experience_match', '') or ''
        recency = analysis.get('recency_analysis', {})
        if isinstance(recency, dict):
            justification = recency.get('relevance_justification', '')
            most_recent = recency.get('most_recent_role', '')
            relevant = recency.get('most_recent_role_relevant', False)
            if justification and justification != 'N/A':
                tag = f" [Recency: {most_recent} — relevant={'yes' if relevant else 'no'}, justification: {justification}]"
                return f"{base}{tag}" if base else tag.strip()
        return base

    def _recheck_years_calculation(self, resume_text: str, original_years_analysis: dict,
                                    job_id: int, job_title: str) -> Optional[dict]:
        """Re-check years-of-experience calculation when a >2yr shortfall is detected.

        Uses a focused prompt that asks AI to verify the arithmetic from the original
        analysis. This catches false negatives from model arithmetic errors (e.g.,
        miscounting 3.75yr as 1.8yr).
        """
        _today = datetime.utcnow()
        _today_str = _today.strftime('%B %d, %Y')
        _today_month = _today.month
        _today_year = _today.year

        skills_to_check = []
        for skill, data in original_years_analysis.items():
            if not isinstance(data, dict):
                continue
            if not data.get('meets_requirement', True):
                required = data.get('required_years', 0)
                estimated = data.get('estimated_years', 0)
                calculation = data.get('calculation', 'N/A')
                skills_to_check.append(
                    f"- {skill}: required {required}yr, initial estimate {estimated}yr, "
                    f"calculation: {calculation}"
                )

        if not skills_to_check:
            return None

        skills_summary = "\n".join(skills_to_check)

        prompt = f"""You are verifying a years-of-experience calculation. An initial AI analysis 
found the following shortfalls for Job "{job_title}" (ID: {job_id}):

{skills_summary}

TODAY'S DATE: {_today_str} (month {_today_month} of {_today_year}).

CANDIDATE RESUME:
{resume_text[:8000]}

INSTRUCTIONS:
1. For EACH skill listed above, re-scan the resume for ALL roles where the candidate performed 
   relevant work (focus on responsibilities, not just titles).
2. Calculate total months using the EXACT formula:
   Duration = (end_year - start_year) × 12 + (end_month - start_month)
   For "Present"/"Current" roles, use today: month {_today_month} of {_today_year}.
3. Show your step-by-step arithmetic.
4. Internships/part-time = 50% weight. University projects = 0.
5. Return the CORRECTED years_analysis.

Respond in JSON format:
{{
    "<skill_name>": {{
        "required_years": <N>,
        "estimated_years": <M>,
        "meets_requirement": true/false,
        "calculation": "<step-by-step month arithmetic>"
    }}
}}"""

        try:
            logger.info(f"🔄 Years re-check: verifying {len(skills_to_check)} skill(s) for job {job_id}")

            from services.openai_helper import resolve_model, log_call
            _model = resolve_model('screening.years_recheck', 'gpt-4.1-mini')
            response = self.openai_client.chat.completions.create(
                model=_model,
                messages=[
                    {"role": "system", "content": "You are a precise arithmetic calculator. "
                     "Your ONLY job is to verify years-of-experience calculations by counting "
                     "months between dates on a resume. Be exact. Show your work."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=1500
            )
            log_call('screening.years_recheck', _model, response, entity_type='Job', entity_id=job_id)

            recheck = json.loads(response.choices[0].message.content)

            any_correction = False
            for skill, data in recheck.items():
                if not isinstance(data, dict):
                    continue
                original = original_years_analysis.get(skill, {})
                if not isinstance(original, dict):
                    continue

                orig_est = float(original.get('estimated_years', 0))
                new_est = float(data.get('estimated_years', 0))

                if abs(new_est - orig_est) >= 0.5:
                    any_correction = True
                    logger.info(
                        f"🔄 Years re-check CORRECTION for '{skill}' on job {job_id}: "
                        f"{orig_est:.1f}yr → {new_est:.1f}yr "
                        f"(calc: {data.get('calculation', 'N/A')})"
                    )

            if any_correction:
                logger.info(f"✅ Years re-check found corrections for job {job_id} — using updated values")
                return recheck
            else:
                logger.info(f"✅ Years re-check CONFIRMS original values for job {job_id}")
                return None

        except Exception as e:
            logger.error(f"❌ Years re-check failed for job {job_id}: {str(e)}")
            return None

    def extract_job_requirements(self, job_id: int, job_title: str, job_description: str,
                                  job_location: str = None, job_work_type: str = None) -> Optional[str]:
        """Extract mandatory requirements from a job description using AI.

        Called during monitoring when new jobs are indexed so requirements
        are available for review BEFORE any candidates are vetted.
        Also called for REFRESH when job is modified in Bullhorn.
        """
        if not self.openai_client:
            logger.warning("OpenAI client not initialized - cannot extract requirements")
            return None

        clean_description = re.sub(r'<[^>]+>', '', job_description) if job_description else ''

        if len(clean_description) < 50:
            logger.warning(f"Job {job_id} has insufficient description for requirements extraction")
            return None

        clean_description = clean_description[:6000]

        prompt = f"""Analyze this job posting and extract ONLY the MANDATORY requirements.

JOB TITLE: {job_title}

JOB DESCRIPTION:
{clean_description}

You MUST output EXACTLY 5-7 requirements. No more, no less.
If the JD lists more than 7 qualifications, prioritize the most critical mandatory qualifications and CONSOLIDATE related items into a single requirement (e.g. merge "Python" + "SQL" + "data pipelines" into one "Technical skills" requirement).
Do NOT list every bullet point as a separate requirement.

Focus on requirements that are EXPLICITLY STATED in the job description:
1. Required technical skills (programming languages, tools, technologies)
2. Required years of experience — ONLY if the JD explicitly states a specific NUMBER (e.g., "5+ years", "3 years of experience", "10 or more years")
3. Required certifications or licenses
4. Required education level
5. Required industry-specific knowledge
6. Required location or work authorization

CRITICAL ANTI-HALLUCINATION RULES:
- ONLY list requirements that are EXPLICITLY written in the job description text above.
- Do NOT infer or fabricate years-of-experience requirements — if the JD does not state a specific number of years, do NOT add one based on the job title, seniority level, or your assumptions about the role.
- Do NOT add requirements based on what you think the role "should" need — only what the JD actually says.
- If the JD says "experience with X" without specifying years, list it as "Experience with X" — NOT "X+ years of X".
- If the JD uses vague phrases like "significant experience" or "proven track record", quote that phrase directly — do NOT convert it to a specific number of years.

Also DO NOT include:
- "Nice to have" or "preferred" qualifications
- Soft skills (communication, teamwork, etc.)
- Generic requirements that apply to any job

Format as a bullet-point list. Be specific and concise."""

        try:
            from services.openai_helper import resolve_model, log_call
            _model = resolve_model('screening.requirements_extract', 'gpt-5.4')
            response = self.openai_client.chat.completions.create(
                model=_model,
                messages=[
                    {"role": "system", "content": "You are a technical recruiter extracting ONLY explicitly stated "
                     "mandatory requirements from job descriptions. You must NEVER infer, fabricate, or add "
                     "requirements that are not directly written in the job description. If the job description "
                     "does not mention a specific number of years, do NOT add one. Be concise and specific."},
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=1000
            )
            log_call('screening.requirements_extract', _model, response, entity_type='Job', entity_id=job_id)

            requirements = response.choices[0].message.content.strip()
            if requirements:
                logger.info(f"✅ Extracted requirements for job {job_id}: {job_title[:50]}")
                return requirements
            return None

        except Exception as e:
            logger.error(f"Error extracting requirements for job {job_id}: {str(e)}")
            return None

    def _reverify_zero_score(self, resume_text: str, job: Dict,
                              candidate_location: Optional[Dict] = None,
                              prior_summary: str = '',
                              prior_gaps: str = '',
                              global_requirements: Optional[str] = None) -> Optional[Dict]:
        """Re-verify a candidate who scored 0% to check for false negatives.

        Uses a focused prompt with prior analysis context to determine whether
        the zero score was accurate or a false negative.  Returns a
        ``revised_score`` key so the caller can update the match record when
        a genuine false negative is detected.

        Args:
            resume_text: Extracted resume text.
            job: Bullhorn job dict.
            candidate_location: Optional candidate address dict (unused in
                prompt today but preserved for future location-aware checks).
            prior_summary: Match summary from the initial 0% result.
            prior_gaps: Gaps identified in the initial 0% result.
            global_requirements: Pre-fetched global screening requirements.
        """
        try:
            job_title = job.get('title', 'Unknown')
            job_description = job.get('description', '') or job.get('publicDescription', '')
            job_description = re.sub(r'<[^>]+>', '', job_description)[:2000]

            prior_context = ''
            if prior_summary or prior_gaps:
                prior_context = (
                    f"\nPrior analysis (scored 0%):\n"
                    f"- Summary: {(prior_summary or 'N/A')[:300]}\n"
                    f"- Gaps: {(prior_gaps or 'N/A')[:300]}\n"
                )

            global_req_block = ''
            if global_requirements:
                global_req_block = (
                    f"\nGlobal screening requirements:\n{global_requirements}\n"
                )

            prompt = (
                f"You are re-checking a candidate who scored 0% in an initial AI screen. "
                f"Determine whether the 0% was accurate or a false negative.\n\n"
                f"Job: {job_title}\n"
                f"Job description: {job_description[:1000]}\n"
                f"{global_req_block}"
                f"{prior_context}\n"
                f"Resume (first 3000 chars):\n{resume_text[:3000]}\n\n"
                f"Instructions:\n"
                f"- Review the prior analysis. If it correctly identified a clear mismatch, "
                f"confirm the 0% score.\n"
                f"- If it missed genuine relevant experience, provide a revised score (20-100).\n"
                f"- Only revise upward when there is clear resume evidence.\n\n"
                f'Respond in JSON:\n'
                f'{{"revised_score": <int 0-100>, "revised_summary": "<1 sentence>", '
                f'"revised_gaps": "<major gaps>", '
                f'"revision_reason": "<why you revised or confirmed>", '
                f'"confidence_reason": "<your confidence assessment>"}}'
            )

            from services.openai_helper import resolve_model, log_call
            _model = resolve_model('screening.zero_recheck', 'gpt-5.4')
            response = self.openai_client.chat.completions.create(
                model=_model,
                messages=[
                    {"role": "system", "content": (
                        "You are a precise technical screener re-checking a zero-score "
                        "result. Return only valid JSON."
                    )},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=500
            )
            log_call('screening.zero_recheck', _model, response)

            result = json.loads(response.choices[0].message.content)
            result['revised_score'] = int(result.get('revised_score', 0))

            result.setdefault('revised_summary', '')
            result.setdefault('revised_gaps', '')
            result.setdefault('revision_reason', '')
            result.setdefault('confidence_reason', '')

            return result

        except Exception as e:
            logger.warning(f"Zero-score re-verification failed: {e}")
            return None

    def analyze_candidate_job_match(self, resume_text: str, job: Dict,
                                     candidate_location: Optional[Dict] = None,
                                     prefetched_requirements: Optional[str] = None,
                                     model_override: Optional[str] = None,
                                     prefetched_global_requirements: Optional[str] = None) -> Dict:
        """
        Use AI to analyze how well a candidate matches a job.

        Args:
            resume_text: Extracted text from candidate's resume
            job: Job dictionary from Bullhorn
            candidate_location: Optional dict with candidate's address info
            prefetched_requirements: Pre-fetched custom requirements (for parallel execution)
            model_override: Optional model name override
            prefetched_global_requirements: Pre-fetched global requirements

        Returns:
            Dictionary with match_score, match_summary, skills_match, experience_match, gaps_identified
        """
        if not self.openai_client:
            return {
                'match_score': 0,
                'match_summary': 'AI analysis unavailable',
                'skills_match': '',
                'experience_match': '',
                'gaps_identified': '',
                'key_requirements': ''
            }

        job_title = job.get('title', 'Unknown Position')
        job_description = job.get('description', '') or job.get('publicDescription', '')

        job_address = job.get('address', {}) if isinstance(job.get('address'), dict) else {}
        job_city = job_address.get('city', '')
        job_state = job_address.get('state', '')
        job_country_raw = job_address.get('countryName', '') or job_address.get('country', '')
        job_country_normalized = normalize_country(job_country_raw)
        job_country = smart_correct_country(job_city, job_state, job_country_normalized)
        job_location_full = ', '.join(filter(None, [job_city, job_state, job_country]))

        work_type = map_work_type(job.get('onSite', 1))

        candidate_city = ''
        candidate_state = ''
        candidate_country = ''
        if candidate_location and isinstance(candidate_location, dict):
            candidate_city = candidate_location.get('city', '')
            candidate_state = candidate_location.get('state', '')
            candidate_country_raw = candidate_location.get('countryName', '') or candidate_location.get('country', '')
            candidate_country_normalized = normalize_country(candidate_country_raw)
            candidate_country = smart_correct_country(candidate_city, candidate_state, candidate_country_normalized)
        candidate_location_full = ', '.join(filter(None, [candidate_city, candidate_state, candidate_country]))

        candidate_location_label = (
            'Resume-based extraction ONLY — Bullhorn address fields are intentionally withheld '
            'to avoid data quality issues. You MUST determine the candidate location exclusively '
            'from the resume text using the MANDATORY LOCATION EXTRACTION steps below.'
        )

        job_id = job.get('id', 'N/A')

        custom_requirements = prefetched_requirements if prefetched_requirements is not None else self._get_job_custom_requirements(job_id)

        job_description = re.sub(r'<[^>]+>', '', job_description)

        max_resume_len = 20000
        max_desc_len = 4000
        resume_text = resume_text[:max_resume_len] if resume_text else ''
        job_description = job_description[:max_desc_len] if job_description else ''

        global_requirements = prefetched_global_requirements if prefetched_global_requirements is not None else self._get_global_custom_requirements()

        custom_requirements_block = ""
        if custom_requirements:
            custom_requirements_block = f"""
MANDATORY SCREENING REQUIREMENTS (evaluate against ALL of these):
{custom_requirements}

These requirements take priority in scoring. Evaluate the candidate against every requirement listed above AND assess their technical fit from the job description — do not ignore the job description's core technical requirements."""

        _today = date.today()
        _today_str = _today.strftime('%B %d, %Y')

        location_instruction = build_location_instruction(work_type, job_location_full, candidate_location_label)

        prompt = f"""Today's date: {_today_str}.

Evaluate the candidate below against the job below. Apply all instructions from your system prompt.
{custom_requirements_block}
{location_instruction}

JOB DETAILS:
- Job ID: {job_id}
- Title: {job_title}
- Location: {job_location_full} (Work Type: {work_type})
- Description: {job_description}

CANDIDATE INFORMATION:
- {candidate_location_label}

CANDIDATE RESUME:
{resume_text}"""

        try:
            global_reqs_section = ""
            if global_requirements:
                global_reqs_section = f"""

GLOBAL SCREENING INSTRUCTIONS (apply to all jobs):
{global_requirements}"""

            system_message = build_system_message(global_reqs_section)

            from services.openai_helper import resolve_model, log_call
            _model = resolve_model('screening.scoring', model_override or self.model)
            response = self.openai_client.chat.completions.create(
                model=_model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=3750
            )
            log_call('screening.scoring', _model, response)

            try:
                usage = response.usage
                prompt_tokens = usage.prompt_tokens if usage else 0
                cached_tokens = 0
                if usage and hasattr(usage, 'prompt_tokens_details') and usage.prompt_tokens_details:
                    details = usage.prompt_tokens_details
                    cached_tokens = getattr(details, 'cached_tokens', 0) or 0
                if prompt_tokens > 0:
                    hit_pct = (cached_tokens / prompt_tokens) * 100
                    logger.info(f"💰 Cache: {cached_tokens} cached / {prompt_tokens} prompt tokens ({hit_pct:.0f}% hit rate) for job {job_id}")
                else:
                    logger.info(f"💰 Cache: usage data unavailable for job {job_id}")
            except Exception as _cache_log_err:
                logger.debug(f"Cache logging error (non-fatal): {_cache_log_err}")

            response_content = response.choices[0].message.content
            if not response_content or not response_content.strip():
                finish_reason = response.choices[0].finish_reason if response.choices[0] else 'unknown'
                logger.error(f"Empty GPT response for job {job_id} (finish_reason={finish_reason})")
                return {
                    'match_score': 0,
                    'match_summary': f'Analysis failed: Empty API response (finish_reason={finish_reason})',
                    'skills_match': '',
                    'experience_match': '',
                    'gaps_identified': '',
                    'key_requirements': ''
                }
            result = json.loads(response_content)

            normalize_response_fields(result, job_id)
            coerce_scores(result, job_id)
            enforce_remote_location(result, job_id, work_type)
            enforce_years_hard_gate(result, job_id, job_title, resume_text, self._recheck_years_calculation)
            enforce_recency_hard_gate(result, job_id)
            enforce_employment_continuity_gap(result, job_id)
            enforce_midcareer_gap(result, job_id)
            enforce_experience_floor(result, job_id, custom_requirements, job_description)
            apply_prestige_detection(result, job_id, resume_text)
            apply_location_barrier(result, job_id, work_type)

            key_requirements = result.get('key_requirements', '')
            years_analysis = result.get('years_analysis', {})
            logger.info(f"📋 AI response for job {job_id}: score={result['match_score']}%, has_requirements={bool(key_requirements)}, has_custom={bool(custom_requirements)}, years_analysis={bool(years_analysis)}")

            years_analysis_json = json.dumps(years_analysis) if years_analysis else None
            result['_years_analysis_json'] = years_analysis_json

            result['_deferred_save'] = {
                'job_id': job_id,
                'job_title': job_title,
                'key_requirements': key_requirements,
                'job_location_full': job_location_full,
                'work_type': work_type,
                'should_save': bool(key_requirements)
            }

            if not key_requirements:
                logger.warning(f"⚠️ AI did not return key_requirements for job {job_id} - requirements will not be saved")
            elif custom_requirements:
                logger.info(f"📝 Job {job_id} has custom requirements - AI interpretation will ALSO be saved (custom supplements AI)")

            # Shadow A/B (S2): fire-and-forget gpt-4.1-mini comparison call.
            # Gated by env SCREENING_AB_SHADOW_ENABLED (default off). Fully
            # fail-soft — never raises, never affects prod scoring.
            try:
                _prod_score_for_shadow = float(result.get('match_score', 0) or 0)
                _run_screening_shadow(
                    system_message=system_message,
                    user_prompt=prompt,
                    prod_model=_model,
                    prod_score=_prod_score_for_shadow,
                    prod_qualified=(_prod_score_for_shadow >= 80.0),
                    job_id=job_id,
                    job_title=job_title,
                    openai_client=self.openai_client,
                )
            except Exception as _shadow_outer:
                logger.debug(f"Shadow A/B invocation suppressed: {_shadow_outer}")

            return result

        except Exception as e:
            logger.error(f"AI analysis error for job {job_id}: {str(e)}")
            return {
                'match_score': 0,
                'match_summary': f'Analysis failed: {str(e)}',
                'skills_match': '',
                'experience_match': '',
                'gaps_identified': '',
                'key_requirements': ''
            }
