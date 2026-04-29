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
import re
import logging
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
from screening.post_processing import (
    normalize_response_fields,
    coerce_scores,
    enforce_remote_location,
    enforce_years_hard_gate,
    enforce_recency_hard_gate,
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

            response = self.openai_client.chat.completions.create(
                model="gpt-5.4",
                messages=[
                    {"role": "system", "content": "You are a precise arithmetic calculator. "
                     "Your ONLY job is to verify years-of-experience calculations by counting "
                     "months between dates on a resume. Be exact. Show your work."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=1500
            )

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
            response = self.openai_client.chat.completions.create(
                model="gpt-5.4",
                messages=[
                    {"role": "system", "content": "You are a technical recruiter extracting ONLY explicitly stated "
                     "mandatory requirements from job descriptions. You must NEVER infer, fabricate, or add "
                     "requirements that are not directly written in the job description. If the job description "
                     "does not mention a specific number of years, do NOT add one. Be concise and specific."},
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=1000
            )

            requirements = response.choices[0].message.content.strip()
            if requirements:
                logger.info(f"✅ Extracted requirements for job {job_id}: {job_title[:50]}")
                return requirements
            return None

        except Exception as e:
            logger.error(f"Error extracting requirements for job {job_id}: {str(e)}")
            return None

    def _reverify_zero_score(self, resume_text: str, job: Dict,
                              candidate_location: Optional[Dict] = None) -> Optional[Dict]:
        """Re-verify a candidate who scored 0% to check for false negatives.

        Uses a lightweight, focused prompt to quickly determine if the candidate
        has ANY relevant skills. This catches cases where the main analysis
        produced a zero score due to JSON parsing errors or other anomalies.
        """
        try:
            job_title = job.get('title', 'Unknown')
            job_description = job.get('description', '') or job.get('publicDescription', '')
            job_description = re.sub(r'<[^>]+>', '', job_description)[:2000]

            prompt = f"""Quick relevance check: Does this candidate have ANY skills relevant to this job?

Job: {job_title}
Key requirements from description: {job_description[:1000]}

Resume (first 3000 chars):
{resume_text[:3000]}

Rate 0-100 how relevant this candidate is. Be strict but fair.
If they have ZERO relevant skills, score 0-10.
If they have SOME relevant skills but major gaps, score 20-50.
If they have MOST required skills, score 60-80.

Respond in JSON:
{{"match_score": <int>, "match_summary": "<1 sentence>", "skills_match": "<relevant skills found>", "gaps_identified": "<major gaps>"}}"""

            response = self.openai_client.chat.completions.create(
                model="gpt-5.4",
                messages=[
                    {"role": "system", "content": "You are a quick-check technical screener. "
                     "Determine if a candidate has ANY relevant skills for a job. Be concise."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=500
            )

            result = json.loads(response.choices[0].message.content)
            result['match_score'] = int(result.get('match_score', 0))

            result.setdefault('experience_match', '')
            result.setdefault('key_requirements', '')

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

            response = self.openai_client.chat.completions.create(
                model=model_override or self.model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=4096
            )

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
