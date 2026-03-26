"""
Prompt Builder - AI prompt construction, GPT calls, and response post-processing.

Contains:
- analyze_candidate_job_match: Main AI analysis prompt + post-processing gates
- extract_job_requirements: AI extraction of mandatory job requirements
- _recheck_years_calculation: Focused GPT re-check for years-of-experience arithmetic
- _build_experience_match: Static helper to build experience_match string from recency data
"""

import logging
import json
import re
from datetime import datetime, date, timedelta
from typing import Dict, Optional

from vetting.geo_utils import smart_correct_country, normalize_country, map_work_type


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
        
        Uses a focused prompt that asks GPT-4o to verify the arithmetic from the original
        analysis. This catches false negatives from model arithmetic errors (e.g., 
        miscounting 3.75yr as 1.8yr).
        
        Args:
            resume_text: The candidate's cleaned resume text
            original_years_analysis: The years_analysis dict from the initial analysis
            job_id: Bullhorn job ID for logging
            job_title: Job title for logging
            
        Returns:
            Corrected years_analysis dict if corrections were made, None if re-check
            confirms the original or if the re-check fails.
        """
        from datetime import datetime
        _today = datetime.utcnow()
        _today_str = _today.strftime('%B %d, %Y')
        _today_month = _today.month
        _today_year = _today.year
        
        # Build a summary of what needs re-checking
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
            logging.info(f"🔄 Years re-check: verifying {len(skills_to_check)} skill(s) for job {job_id}")
            
            response = self.openai_client.chat.completions.create(
                model="gpt-5",
                messages=[
                    {"role": "system", "content": "You are a precise arithmetic calculator. "
                     "Your ONLY job is to verify years-of-experience calculations by counting "
                     "months between dates on a resume. Be exact. Show your work."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_completion_tokens=800
            )
            
            recheck = json.loads(response.choices[0].message.content)
            
            # Check if the re-check differs materially from the original
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
                    logging.info(
                        f"🔄 Years re-check CORRECTION for '{skill}' on job {job_id}: "
                        f"{orig_est:.1f}yr → {new_est:.1f}yr "
                        f"(calc: {data.get('calculation', 'N/A')})"
                    )
            
            if any_correction:
                logging.info(f"✅ Years re-check found corrections for job {job_id} — using updated values")
                return recheck
            else:
                logging.info(f"✅ Years re-check CONFIRMS original values for job {job_id}")
                return None
                
        except Exception as e:
            logging.error(f"❌ Years re-check failed for job {job_id}: {str(e)}")
            return None

    def extract_job_requirements(self, job_id: int, job_title: str, job_description: str,
                                  job_location: str = None, job_work_type: str = None) -> Optional[str]:
        """
        Extract mandatory requirements from a job description using AI.
        Called during monitoring when new jobs are indexed so requirements
        are available for review BEFORE any candidates are vetted.
        Also called for REFRESH when job is modified in Bullhorn.
        
        Args:
            job_id: Bullhorn job ID
            job_title: Job title
            job_description: Full job description text
            job_location: Optional location string (city, state, country)
            job_work_type: Optional work type (On-site, Hybrid, Remote)
            
        Returns:
            Extracted requirements string or None if extraction fails
        """
        if not self.openai_client:
            logging.warning("OpenAI client not initialized - cannot extract requirements")
            return None
        
        # NOTE: Removed early return check for existing requirements
        # This function is now called specifically for refresh when job is modified
        # So we always want to re-extract from the updated job description
        
        # Clean job description (remove HTML)
        import re
        clean_description = re.sub(r'<[^>]+>', '', job_description) if job_description else ''
        
        if len(clean_description) < 50:
            logging.warning(f"Job {job_id} has insufficient description for requirements extraction")
            return None
        
        # Truncate if too long
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
                model="gpt-5",
                messages=[
                    {"role": "system", "content": "You are a technical recruiter extracting ONLY explicitly stated mandatory requirements from job descriptions. You must NEVER infer, fabricate, or add requirements that are not directly written in the job description. If the job description does not mention a specific number of years, do NOT add one. Be concise and specific."},
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=500
            )
            
            requirements = response.choices[0].message.content.strip()
            
            # Save the extracted requirements with location data
            if requirements:
                self._save_ai_interpreted_requirements(job_id, job_title, requirements, job_location, job_work_type)
                logging.info(f"✅ Extracted requirements for job {job_id}: {job_title[:50]}")
                return requirements
            
            return None
            
        except Exception as e:
            logging.error(f"Error extracting requirements for job {job_id}: {str(e)}")
            return None

    def analyze_candidate_job_match(self, resume_text: str, job: Dict, candidate_location: Optional[Dict] = None, prefetched_requirements: Optional[str] = None, model_override: Optional[str] = None, prefetched_global_requirements: Optional[str] = None) -> Dict:
        """
        Use GPT-4o to analyze how well a candidate matches a job.
        
        Args:
            resume_text: Extracted text from candidate's resume
            job: Job dictionary from Bullhorn
            candidate_location: Optional dict with candidate's address info (city, state, countryName)
            
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
        # Use internal description field first (contains full details), fall back to publicDescription
        job_description = job.get('description', '') or job.get('publicDescription', '')
        
        # Extract full job location details
        job_address = job.get('address', {}) if isinstance(job.get('address'), dict) else {}
        job_city = job_address.get('city', '')
        job_state = job_address.get('state', '')
        job_country_raw = job_address.get('countryName', '') or job_address.get('country', '')
        job_country_normalized = normalize_country(job_country_raw)
        job_country = smart_correct_country(job_city, job_state, job_country_normalized)  # Auto-fix country mismatches
        job_location_full = ', '.join(filter(None, [job_city, job_state, job_country]))
        
        # Get work type using helper that handles both numeric and string values
        work_type = map_work_type(job.get('onSite', 1))
        
        # Extract candidate location with normalized country
        candidate_city = ''
        candidate_state = ''
        candidate_country = ''
        if candidate_location and isinstance(candidate_location, dict):
            candidate_city = candidate_location.get('city', '')
            candidate_state = candidate_location.get('state', '')
            candidate_country_raw = candidate_location.get('countryName', '') or candidate_location.get('country', '')
            candidate_country_normalized = normalize_country(candidate_country_raw)
            candidate_country = smart_correct_country(candidate_city, candidate_state, candidate_country_normalized)  # Auto-fix country mismatches
        candidate_location_full = ', '.join(filter(None, [candidate_city, candidate_state, candidate_country]))
        
        # Bullhorn address fields are intentionally NOT passed to the AI.
        # Bullhorn frequently stores inaccurate or auto-defaulted location data,
        # which can bias location extraction and produce inconsistent results
        # across parallel job evaluations for the same candidate.
        # The resume is the sole source of truth for candidate location.
        candidate_location_label = (
            'Resume-based extraction ONLY — Bullhorn address fields are intentionally withheld '
            'to avoid data quality issues. You MUST determine the candidate location exclusively '
            'from the resume text using the MANDATORY LOCATION EXTRACTION steps below.'
        )
        
        job_id = job.get('id', 'N/A')
        
        # Check for custom requirements override
        # Use pre-fetched requirements if provided (for parallel execution outside Flask context)
        custom_requirements = prefetched_requirements if prefetched_requirements is not None else self._get_job_custom_requirements(job_id)
        
        # Clean up job description (remove HTML tags if present)
        import re
        job_description = re.sub(r'<[^>]+>', '', job_description)
        
        # Truncate if too long
        max_resume_len = 20000
        max_desc_len = 4000
        resume_text = resume_text[:max_resume_len] if resume_text else ''
        job_description = job_description[:max_desc_len] if job_description else ''
        
        # Build the requirements section - use custom if available, otherwise let AI extract
        # Also fetch global screening instructions that apply to ALL jobs
        # Use pre-fetched value when available (ThreadPoolExecutor threads lack app context)
        global_requirements = prefetched_global_requirements if prefetched_global_requirements is not None else self._get_global_custom_requirements()

        # For the user message: only inject custom requirements if present (per-job dynamic data)
        custom_requirements_block = ""
        if custom_requirements:
            custom_requirements_block = f"""
MANDATORY SCREENING REQUIREMENTS (evaluate against ALL of these):
{custom_requirements}

These requirements take priority in scoring. Evaluate the candidate against every requirement listed above AND assess their technical fit from the job description — do not ignore the job description's core technical requirements."""

        # Inject exact current date from Python for accurate ongoing-role calculation
        from datetime import date as _date
        _today = _date.today()
        _today_str = _today.strftime('%B %d, %Y')  # e.g., "March 05, 2026"

                # Build location matching instructions based on work type
        location_instruction = ""
        if job_location_full:
            if work_type == 'Remote':
                location_instruction = f"""
LOCATION REQUIREMENT (Remote Position):
- Job Location: {job_location_full} (Work Type: {work_type})
- {candidate_location_label}
- For REMOTE positions: Candidate MUST be in the same COUNTRY as the job location for tax/legal compliance.
- City and state do NOT need to match for remote roles - only the country matters.
- If candidate is in a different country than the job, add "Location mismatch: different country" to gaps_identified and reduce score by 25-35 points.
- HARD CEILING: The final score for any cross-country candidate on a remote role MUST NOT exceed 75, regardless of how strong their technical fit is. Cap the score at 75 before returning.

CRITICAL STATE/PROVINCE RECOGNITION:
- ANY U.S. STATE (Pennsylvania, California, Texas, New York, Florida, etc.) IS PART OF THE UNITED STATES.
- If a remote job is in the United States and candidate is in ANY U.S. state, they ARE in the same country - NO location mismatch.
- Similarly, Canadian provinces (Ontario, British Columbia, etc.) are part of Canada.
- ONLY flag "Location mismatch: different country" if the candidate is literally in a DIFFERENT country (e.g., candidate in India for a US-based job, or candidate in UK for a Canada-based job).
- DO NOT flag location mismatch just because candidate is in a different state/city within the same country.

MANDATORY LOCATION EXTRACTION (follow this EXACT priority order):
1. RESUME HEADER/CONTACT SECTION (HIGHEST PRIORITY): Look for city, state/province, zip code, or country near the candidate's name, phone, or email at the TOP of the resume. Formats like "Frisco TX", "Dallas, TX 75033", "New York, NY, USA" etc. all count. This is the MOST RELIABLE source — always check here first.
2. MOST RECENT WORK HISTORY: If the header/contact section has no location, check the candidate's most recent job for a city/state/country. Use that as their presumed current location.
3. SYSTEM ADDRESS FIELD (FALLBACK ONLY): Only if the resume provides NO location in either the header or work history, consider the system-provided address above. WARNING: Bullhorn often auto-fills "United States" as a default when no address is entered — a country-only value with no city/state is UNRELIABLE and should be treated as "unknown" for location matching purposes.
4. EDUCATION LOCATION: If all above are empty, check education institution location.
5. "UNKNOWN": Only if none of the four sources above provide any usable city, state, or country.

CRITICAL OVERRIDE RULE: If the resume clearly states a specific location (e.g., "Frisco, TX") but the system address field shows only a country (e.g., "United States"), ALWAYS use the resume location. The resume is the candidate's own stated location and takes absolute precedence over system defaults.

INTERNATIONAL/OFFSHORE OVERRIDE:
- If the job description explicitly mentions international eligibility, offshore work, or specific non-job-address countries/regions (e.g., "open to candidates in Egypt or Spain", "100% Remote, international OK", "offshore resources welcome", "candidates in [Country] welcome"), then the same-country rule above does NOT apply.
- In this case, match the candidate's country against the countries/regions listed IN THE JOB DESCRIPTION, not the Bullhorn job address field.
- If the candidate is located in one of the explicitly allowed countries/regions from the description, there is NO location mismatch — do not penalize their score.
- Example: Job address says "United States" but description says "Must be located in Egypt or Spain" → a candidate in Cairo, Egypt or Madrid, Spain has NO location mismatch.

MANDATORY SELF-CONSISTENCY CHECK (Remote Positions — perform this BEFORE returning your response):
Before finalising your output, re-read the candidate location you extracted and the job country (or the explicitly allowed countries from the INTERNATIONAL/OFFSHORE OVERRIDE if that rule applies), then verify:
1. If the candidate IS in the same country as the job, OR qualifies under the international/offshore override → your gaps_identified MUST NOT contain "Location mismatch: different country". If it does, REMOVE that phrase and restore the 25-35 points that were deducted. Do NOT return a contradictory response.
2. If the candidate IS NOT in the same country (and no international/offshore override applies) → "Location mismatch: different country" MUST appear in gaps_identified and the score MUST be capped at 75. Verify the deduction was actually applied.
This self-check exists because prior AI responses have simultaneously stated "candidate meets the location requirement" AND applied a -25 location penalty — an internal contradiction that must never appear in your output."""
            else:  # On-site or Hybrid
                location_instruction = f"""
LOCATION REQUIREMENT ({work_type} Position):
- Job Location: {job_location_full} (Work Type: {work_type})
- {candidate_location_label}
- For ON-SITE/HYBRID positions: Candidate must be in or near the job's city/metro area, or explicitly willing to relocate.

CRITICAL: If candidate is ALREADY in the same city or metro area as the job, they AUTOMATICALLY qualify for on-site/hybrid work.
- Do NOT flag "location mismatch" or "not willing to work on-site" if candidate lives locally.
- Local candidates CAN work on-site by default - no explicit statement needed.

MANDATORY LOCATION PENALTY TIERS (apply these exact deductions — physical presence is non-negotiable for on-site/hybrid roles):
1. Candidate is in a DIFFERENT COUNTRY than the job location:
   - Add "Location mismatch: candidate in [country], job requires on-site presence in [job country]" to gaps_identified.
   - Reduce score by 25–35 points. This is a hard location barrier — strong technical skills cannot compensate.

SPECIAL CASE — JOB CITY/STATE UNKNOWN:
Before applying tier 2, check whether the job location shown above includes a specific city or state/province.
- If the job location ONLY shows a country name with NO city and NO state/province listed, AND the candidate is in that same country:
  → Do NOT apply any score penalty.
  → Add this soft note to gaps_identified ONLY: "Job city not specified — recruiter should verify candidate proximity for on-site role."
  → This prevents false penalties when the job's specific location has not been configured in the system.
- Only proceed to tiers 2/2b if the job location includes a specific city or state/province to compare against.

SPECIAL CASE — JOB HAS STATE/PROVINCE BUT NO CITY:
Before applying tiers 2/2b, check whether the job location includes a specific city.
- If the job location shows a state/province (e.g., "Ontario, Canada" or "Texas, United States") but NO specific city, AND the candidate is in that SAME state/province:
  → Do NOT apply any location penalty. The candidate meets the location requirement.
  → Do NOT flag "Location mismatch" — the job has no city to mismatch against.
  → If the candidate is in a DIFFERENT state/province, proceed to tier 2c (different state/province).
  → This prevents false penalties when the job is posted at the provincial/state level without a specific city.

2. Candidate is in the SAME STATE/PROVINCE but a DIFFERENT CITY that is FAR from the job city (more than approximately 100 miles / 160 km apart):
   - Add "Location mismatch: candidate not in {job_city or job_state or 'job area'}, on-site required" to gaps_identified.
   - Reduce score by 15–20 points.
   - EXCEPTION — WILLING TO RELOCATE: If the candidate explicitly states willingness to relocate anywhere on their resume (e.g., "open to relocation", "willing to relocate", "open to moving", "relocation considered"):
     → Reduce the deduction to exactly 5 points (not 15–20).
     → Do NOT use the word "mismatch" in the gap note. Instead write: "Location note: candidate not local to [city/area] but explicitly states willingness to relocate — recruiter should verify relocation timeline and logistics."
     → This treats relocation willingness as a logistics item for the recruiter, not a qualification gap.

2b. Candidate is in the SAME STATE/PROVINCE, DIFFERENT CITY, but NEARBY (within approximately 100 miles / 160 km of the job city — commutable or short relocation):
   - Reduce score by exactly 5 points (not 15–20).
   - Do NOT use the word "mismatch" in the gap note. Instead write: "Location note: candidate is in [candidate city], approximately [estimated distance] from [job city] — within commuting or short relocation range. Recruiter should confirm logistics."
   - Examples of nearby cities: Beaumont TX ↔ Houston TX (~85 mi), Fort Worth TX ↔ Dallas TX (~30 mi), San Jose CA ↔ San Francisco CA (~50 mi), Baltimore MD ↔ Washington DC (~40 mi), Tacoma WA ↔ Seattle WA (~35 mi).
   - Use your knowledge of U.S. and Canadian geography to estimate distances between cities. When uncertain, err on the side of leniency (treat as nearby).

2c. Candidate is in a DIFFERENT STATE/PROVINCE entirely (and does NOT qualify under tier 2 or 2b):
   - Add "Location mismatch: candidate not in {job_city or job_state or 'job area'}, on-site required" to gaps_identified.
   - Reduce score by 15–20 points.
   - The WILLING TO RELOCATE exception from tier 2 also applies here — reduce to 5 points if candidate explicitly states relocation willingness.

3. Candidate is in the SAME CITY or METRO AREA: no deduction, no flag.
- If candidate is non-local AND doesn't mention relocation willingness, add "Location mismatch: candidate not in {job_city or job_state or 'job area'}" to gaps_identified.

MANDATORY LOCATION EXTRACTION (follow this EXACT priority order):
1. RESUME HEADER/CONTACT SECTION (HIGHEST PRIORITY): Look for city, state/province, zip code, or country near the candidate's name, phone, or email at the TOP of the resume. Formats like "Frisco TX", "Dallas, TX 75033", "New York, NY, USA" etc. all count. This is the MOST RELIABLE source — always check here first.
2. MOST RECENT WORK HISTORY: If the header/contact section has no location, check the candidate's most recent job for a city/state/country. Use that as their presumed current location.
3. SYSTEM ADDRESS FIELD (FALLBACK ONLY): Only if the resume provides NO location in either the header or work history, consider the system-provided address above. WARNING: Bullhorn often auto-fills "United States" as a default when no address is entered — a country-only value with no city/state is UNRELIABLE and should be treated as "unknown" for location matching purposes.
4. EDUCATION LOCATION: If all above are empty, check education institution location.
5. "UNKNOWN": Only if none of the four sources above provide any usable city, state, or country.

CRITICAL OVERRIDE RULE: If the resume clearly states a specific location (e.g., "Frisco, TX") but the system address field shows only a country (e.g., "United States"), ALWAYS use the resume location. The resume is the candidate's own stated location and takes absolute precedence over system defaults."""
        
        # ── Slim user-message (data-only) ──
        # Today's date injected here so the AI calculates ongoing-role durations correctly.
        # All static instructions live in the system message for maximum prompt caching.
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
            # ── Static system message (cached by OpenAI on every call) ──
            # All unchanging instructions live here so OpenAI can cache this prefix
            # across all vetting calls, cutting prompt costs by ~50% on cached tokens.
            # Dynamic per-call data (date, job details, resume) lives in the user message above.
            global_reqs_section = ""
            if global_requirements:
                global_reqs_section = f"""

GLOBAL SCREENING INSTRUCTIONS (apply to all jobs):
{global_requirements}"""

            system_message = f"""You are a strict, evidence-based technical recruiter analyzing candidate-job fit.

CRITICAL RULES:
1. You MUST only cite skills and experience that are EXPLICITLY written in the candidate\'s resume.
2. You MUST NOT infer or hallucinate skills that are not directly stated.
3. If a job requires FPGA and the resume shows SQL/database experience, they DO NOT match.
4. If a job requires a technology and the resume shows ONLY an unrelated technology (e.g., FPGA job but resume shows SQL/database), that IS a GAP. However, if the resume shows a COMPETING tool in the same category (e.g., Tableau for Power BI, AWS SageMaker for Azure ML), apply partial credit and mark as TRANSFERABLE, not CRITICAL.
5. Be honest - a mismatched candidate should score LOW even if they have impressive but irrelevant skills.
6. Your assessment will be used for recruiter decisions - accuracy is critical.
7. LOCATION MATTERS: Check if the candidate\'s location is compatible with the job\'s work type (remote/onsite/hybrid).
   - Remote jobs: Candidate must be in the same COUNTRY for tax/legal compliance.
   - On-site/Hybrid jobs: Candidate should be in or near the job\'s city/metro area.
   - If candidate location doesn\'t match, this is a GAP that should reduce their score.
8. EDUCATION HIERARCHY (higher degrees satisfy lower requirements):
   - Doctorate/PhD > Master\'s (MA, MS, MBA, etc.) > Bachelor\'s (BA, BS, etc.) > Associate\'s > High School/GED
   - If a job requires "Bachelor\'s degree" and the candidate has a Master\'s, PhD, or Doctorate, the education requirement is MET (exceeded), NOT a gap.
   - Only flag an education gap if the candidate\'s highest degree is LOWER than what the job requires.
   - If the job specifies a field (e.g., "Bachelor\'s in Computer Science") and the candidate has a higher degree in an unrelated field, acknowledge the higher degree but note the field mismatch as a separate gap.
9. YEARS OF EXPERIENCE MATTER: If a job requires "3+ years of Python" and the candidate has only used Python for 6 months based on resume dates, that is a CRITICAL GAP that MUST significantly reduce the score. Do NOT treat skills learned in brief internships, bootcamps, or university coursework as equivalent to years of professional experience. A 4-month internship using React does NOT satisfy a "3+ years of React" requirement.
10. DISTINGUISH PROFESSIONAL VS ACADEMIC EXPERIENCE: Full-time professional roles count fully. Internships and part-time roles count at 50%. University projects, coursework, capstone projects, and personal side projects count as ZERO professional years. A recent graduate with only coursework experience CANNOT meet a "3+ years" requirement.
11. WORK AUTHORIZATION EVIDENCE: When a job requires US citizenship, W2 only, or similar work authorization, you MUST populate the work_authorization_analysis section with ALL US roles enumerated from the resume. DO NOT simply flag "citizenship not mentioned" as a gap without first performing the mandatory work history enumeration from the Global Screening Instructions. If the candidate has 5+ years of US work experience, apply NO score penalty per the inference tier rules. The same applies to Canadian security clearance — enumerate Canadian roles before flagging clearance gaps.
12. EVIDENCE-FIRST SCORING: You MUST complete the requirement_evidence array BEFORE determining the match_score. Your score must be mathematically derivable from the evidence you cited — do not assign a holistic impression score that contradicts the per-requirement evidence.
13. EXPERIENCE DEPTH & DOMAIN RELEVANCE: When evaluating whether a candidate\'s experience satisfies a requirement, assess the NATURE of the experience, not just keyword overlap. Specifically:
   - AUDIT/ASSESSMENT experience (e.g., "audited cybersecurity controls using NIST") does NOT satisfy a requirement for HANDS-ON DELIVERY/OPERATIONS (e.g., "ensure reliable, secure delivery of IT systems"). Auditing a system ≠ building or operating that system.
   - GOVERNANCE/COMPLIANCE/STANDARDS experience does NOT satisfy a requirement for TECHNOLOGY IMPLEMENTATION/OPERATIONS. Setting conformance standards ≠ delivering technology solutions.
   - A candidate who EVALUATED, ASSESSED, or REVIEWED a system is NOT equivalent to one who BUILT, OPERATED, MANAGED, or DELIVERED that system.
   - When citing evidence in requirement_evidence, explicitly note whether the experience is ADVISORY/AUDIT or DELIVERY/OPERATIONAL — and apply a score penalty (10-15 pts per affected requirement) when advisory experience is cited against a delivery requirement.
   - Budget experience from audit engagements (managing engagement budgets at a consulting firm) is NOT equivalent to owning a technology department budget ($5M+). Note the distinction.
   - CONTEXTUAL INTERPRETATION OF JOB TERMS: Interpret job requirements in the context of the role\'s domain, not literally or narrowly. For example:
     * "Implementation" in infrastructure/DBA roles means deploying, configuring, migrating, and operationalizing systems (e.g., implementing failover clusters, implementing Always On AGs, implementing migrations). Do NOT interpret this narrowly as only software project delivery with requirements gathering and go-live milestones.
     * "Implementation" in software roles may mean end-to-end project delivery. Match the interpretation to the job\'s domain.
     * Always ask: "What would \'implementation\' mean to a hiring manager for THIS specific role?" and evaluate accordingly.
   - CLIENT-FACING INFERENCE FROM CONSULTING FIRMS: When a candidate has worked at a recognized consulting, staffing, or professional services firm (e.g., Infosys, Accenture, Deloitte, Wipro, TCS, Capgemini, SkillStorm, TEKsystems, etc.) and the resume indicates placement at a named client (e.g., "Infosys (Client: McKesson)"), this IS client-facing experience. Consulting engagements are inherently client-facing — the consultant works on-site or remotely for external clients. Do NOT require explicit phrases like "interfaced with clients" when the work structure clearly demonstrates client engagement.
14. RECENCY OF RELEVANT EXPERIENCE: After evaluating requirements, check whether the candidate\'s
    MOST RECENT 2 roles (by date) are relevant to the job requirements being scored.
    - If the candidate\'s most recent role is UNRELATED to the job domain and the most recent
      RELEVANT role ended 12+ months ago, apply a 10-15 point penalty. Note in gaps: "Candidate\'s most
      recent professional activity is outside the target domain; relevant experience is not current."
    - If BOTH of the candidate\'s two most recent roles are unrelated to the job domain, apply
      a 15-25 point penalty. Note in gaps: "Candidate has not practiced relevant skills in their last
      two positions; career trajectory has shifted away from this domain."
    - Roles with NO bullet points or descriptions provide NO evidence of relevant skills.
      Do not assume relevance based on job title alone.
    - "Unrelated" means the role\'s described responsibilities share NO meaningful overlap with
      the job\'s mandatory requirements. Relevance requires that the role\'s ACTUAL DUTIES — not
      just its title — demonstrate hands-on work in the same professional domain as the job.
    - MANDATORY RELEVANCE JUSTIFICATION: When marking most_recent_role_relevant=true, you MUST
      provide a `relevance_justification` field citing at least ONE specific shared duty, tool,
      technology, or domain responsibility between the candidate\'s most recent role and the job.
      Examples of VALID justifications:
        * "Most recent role involves managing Active Directory and Group Policy, directly relevant to IT Operations job requirements"
        * "Currently performing ETL pipeline development with Spark, matching the Data Engineer role requirements"
        * "Role includes patient triage and clinical documentation, relevant to the Medical Assistant position"
      Examples of INVALID justifications (these MUST result in most_recent_role_relevant=false):
        * "Candidate has transferable skills like communication and teamwork"
        * "Has general work experience"
        * "Shows strong work ethic and reliability"
        * "Customer-facing experience" (unless the job specifically requires customer service duties)
      If you cannot cite a specific shared duty/tool/domain, you MUST mark most_recent_role_relevant=false.
    - DOMAIN MISMATCH: When the most recent role falls into a completely different professional
      domain than the job being evaluated, mark most_recent_role_relevant=false. Do NOT give
      credit for "transferable skills" like communication or teamwork when assessing domain relevance.
    - TECHNOLOGY EVOLUTION IN CAREERS — CRITICAL RECENCY RULE: When a job requires a specific
      technology (e.g., Databricks, Snowflake, Kafka), do NOT anchor recency only on roles that
      mention that exact tool by name if the candidate\'s CURRENT role uses the tool actively.
      Many technologies emerged mid-career: a candidate may have used Hadoop/MapReduce in 2015-2018
      and then transitioned to Spark/Databricks from 2020 onward as the industry evolved. This is
      a SIGN OF EXPERTISE AND ADAPTABILITY — not evidence of an outdated career. Evaluate recency
      based on the candidate\'s CURRENT and MOST RECENT role responsibilities, not on which of their
      older roles happen to name the required tool. If the most recent role\'s responsibilities are
      clearly relevant to the job domain (e.g., data engineering using Spark, cloud pipelines, ETL),
      mark most_recent_role_relevant=true and months_since_relevant_work=0 regardless of which
      specific platform names appear in older roles.
    - Report your finding in the recency_analysis JSON section.

15. EMPLOYMENT CONTINUITY: Check whether the candidate is currently employed by looking at
    the end date of their most recent role (any role, regardless of relevance).
    - "Currently employed" means the most recent role has an end date of "Present", "Current",
      "Now", or a date within the last 12 months of today's date (provided at the top of this prompt).
    - If currently employed OR last role ended less than 12 months ago: NO penalty.
    - If last role ended 12–23 months ago: reduce technical_score by 8 points.
      Add to gaps_identified: "Employment gap: candidate last employed [Mon YYYY] ([N] months ago)."
    - If last role ended 24–35 months ago: reduce technical_score by 12 points.
      Add to gaps_identified: "Employment gap: candidate last employed [Mon YYYY] ([N] months ago)."
    - If last role ended 36+ months ago: reduce technical_score by 15 points.
      Add to gaps_identified: "Employment gap: candidate last employed [Mon YYYY] ([N] months ago)."
    - If no employment dates are found anywhere on the resume (dates entirely absent):
      DO NOT apply any score penalty. Add to gaps_identified:
      "Employment continuity unknown: no employment dates found on resume — recruiter should verify current employment status."
    - Contract, freelance, self-employed, and part-time roles count as employed — treat an
      active end date of "present/current" in any of these as currently employed.
    - MID-CAREER GAP DETECTION: In addition to checking the gap from the last role to today,
      scan ALL consecutive role pairs for significant gaps BETWEEN roles. For each pair of
      consecutive roles (ordered chronologically), calculate the gap between the end date of
      the earlier role and the start date of the next role. Report the LARGEST such gap.
      - If the largest mid-career gap is 12–23 months: reduce technical_score by 4 points.
        Add to gaps_identified: "Mid-career employment gap: [N] months between [Role A end] and [Role B start]."
      - If the largest mid-career gap is 24+ months: reduce technical_score by 7 points.
        Add to gaps_identified: "Significant mid-career employment gap: [N] months between [Role A end] and [Role B start]."
      - If the largest mid-career gap is under 12 months: no penalty.
      - This penalty stacks with the current-employment gap penalty above (they measure different things).
    - Report your finding in the employment_gap_analysis JSON section.

REQUIREMENTS EVALUATION (when no custom requirements are provided):
IMPORTANT: Identify and focus ONLY on MANDATORY requirements from the job description:
- Required skills (often marked as "required", "must have", "essential")
- Minimum years of experience specified
- Required certifications or licenses
- Required education level

DO NOT penalize candidates for missing "nice-to-have" or "preferred" qualifications.
Be lenient on soft skills - focus primarily on technical/hard skill requirements.{global_reqs_section}

YEARS OF EXPERIENCE ANALYSIS (MANDATORY):
Before scoring, you MUST perform this analysis for EACH skill or technology that has an
explicit "X+ years" or "X years" requirement in the job description or requirements:

1. Identify which skills have year-based requirements (e.g., "3+ years of Python", "5 years Java development").
2. For each such skill, scan the resume for ALL roles where the candidate performed work in that skill area.
   DISCIPLINE RECOGNITION — count a role if the candidate DID the work, even if their title differs:
   - "Data Science" experience includes roles titled: Data Scientist, ML Engineer, AI Engineer,
     Machine Learning Engineer, Research Scientist, Applied Scientist, or any role where
     responsibilities include predictive/statistical modeling, feature engineering, ML model
     training/evaluation/deployment, or NLP/CV/deep learning applications.
   - A "Data Analyst" role ONLY counts toward Data Science if responsibilities demonstrate
     hands-on work in at least TWO of: predictive/statistical modeling, feature engineering,
     ML model training/evaluation/deployment, NLP/CV/deep learning. Roles that ONLY involve
     SQL queries, Excel dashboards, report generation, or KPI tracking do NOT count.
   - "Machine Learning" experience includes: ML Engineer, AI Engineer, Data Scientist,
     Deep Learning Engineer, NLP Engineer, Computer Vision Engineer, Research Scientist.
   - "AI" experience includes: AI Engineer, ML Engineer, Data Scientist, GenAI Engineer.
   - "Software Engineering" includes: Software Developer, Full-Stack Developer, Backend Engineer.
   - "Data Engineering" includes: Data Engineer, ETL Developer, Analytics Engineer, Data Architect.
   - "DevOps" includes: SRE, Platform Engineer, Infrastructure Engineer, Cloud Engineer.
   CRITICAL: Focus on WHAT THE CANDIDATE DID in each role (responsibilities), not their job title alone.
   If a job requires "5 years of Data Science" and a candidate was a "Machine Learning Engineer" for
   7 years doing predictive modeling, NLP, and statistical analysis — that IS Data Science experience.
3. Calculate the total duration IN MONTHS using this exact formula for each role:
   - Convert start and end dates to (year, month) pairs.
   - Duration in months = (end_year - start_year) × 12 + (end_month - start_month).
   - For "Present", "Current", or ongoing roles, use today\'s date from the user message.
   - EXAMPLE: "Jan 2020 – Aug 2023" = (2023 − 2020) × 12 + (8 − 1) = 43 months.
   - EXAMPLE: "Jul 2018 – Jun 2021" = (2021 − 2018) × 12 + (6 − 7) = 35 months.
   - Internships and part-time roles count at 50% weight (e.g., a 6-month internship = 3 months effective).
   - University coursework, academic projects, and personal projects do NOT count toward professional years.
   - Overlapping roles should not be double-counted; use the union of date ranges.
4. SUM all months across qualifying roles, then divide by 12 to get total years.
5. Show your step-by-step arithmetic in the "calculation" field (see JSON format below).
6. PLATFORM AGE CEILING — CRITICAL: Before treating any years shortfall as a gap, check whether the
   required number of years is physically achievable given the technology\'s commercial availability.
   Some technologies simply did not exist as enterprise-grade platforms until recently. Use these
   maximum possible years (as of today, 2026) when evaluating requirements:
   - Databricks: max ~8 years (enterprise availability ~2018; mainstream adoption ~2020)
   - Snowflake: max ~10 years (GA 2015, mainstream ~2018)
   - Apache Kafka: max ~14 years (OSS 2011, enterprise mainstream ~2015)
   - Apache Spark: max ~12 years (ASF graduation 2014)
   - Azure Data Factory: max ~9 years (GA 2015)
   - Delta Lake: max ~7 years (OSS 2019)
   - Azure Synapse Analytics: max ~6 years (GA 2020)
   - Kubernetes: max ~10 years (GA 2014)
   - dbt (data build tool): max ~8 years (OSS 2016, mainstream ~2020)
   If a job requires MORE years than the platform ceiling allows (e.g., "8 years Databricks" when
   Databricks maximum is ~8 years), treat the CEILING as the effective requirement and DO NOT penalize
   the candidate for failing to meet a physically impossible threshold. In your calculation field, note:
   "Platform ceiling applied: [technology] max ~Xyr commercially available; effective requirement
   adjusted to Xyr." A candidate with 5-6 years of Databricks against an "8 year" requirement should
   receive PARTIAL credit, not a critical failure — note the ceiling and score accordingly.
7. Compare the candidate\'s calculated years against the EFFECTIVE requirement (after ceiling adjustment).
8. If ANY required skill has a candidate shortfall of 2+ years below the EFFECTIVE minimum,
   the match_score MUST be capped at 60 (regardless of how well other requirements match).
   If the shortfall is 1-2 years, reduce the score by at least 15 points from what it would otherwise be.

If no skills in the job description have explicit year-based requirements, set years_analysis to an empty object {{}}.

TRANSFERABLE SKILLS — TECHNOLOGY EQUIVALENCY:
When counting years for a SPECIFIC TOOL, also check for equivalent/competing technologies:

Equivalency Groups:
- BI/Data Visualization: Power BI <-> Tableau <-> Looker <-> QlikView <-> MicroStrategy <-> Sisense
- Cloud ML Platforms: AWS SageMaker <-> Azure ML <-> Google Vertex AI <-> Databricks ML
- Data Lakehouse/Warehouse: Microsoft Fabric <-> Databricks <-> Snowflake <-> BigQuery <-> AWS Lake Formation
- ETL/Data Integration: SSIS <-> Informatica <-> Talend <-> Apache Airflow <-> AWS Glue <-> Azure Data Factory
- API/Integration: REST API experience in ANY language/framework satisfies "API literacy" requirements
- Cloud Platforms: AWS <-> Azure <-> GCP (core cloud concepts transfer between platforms)
- Databases/SQL: SQL Server <-> PostgreSQL <-> MySQL <-> Oracle (SQL skills transfer across engines)
- Low-Code AI/RPA: Copilot Studio <-> Power Automate <-> UiPath <-> Automation Anywhere
- Containerization: Docker <-> Podman, Kubernetes <-> ECS <-> GKE

Credit Rules (TWO-TIER):
1. If the job requires a SKILL CATEGORY (e.g., "5yr data visualization experience", "5yr cloud ML"),
   sum ALL equivalent tools at 100% credit — the job is asking for category experience.
2. If the job requires a SPECIFIC TOOL (e.g., "5yr Power BI", "3yr Azure ML"),
   sum equivalent tool years and apply 75% credit (accounts for tool-specific features not transferring).
3. Mark gap as "TRANSFERABLE" (not "CRITICAL") when equivalent experience exists.
4. In years_analysis, document the equivalency:
   e.g., "Power BI: required 5yr, candidate has ~0yr Power BI but 6yr Tableau (equivalent: 4.5yr credit)"
5. In gaps_identified, write:
   "Missing [required tool] specifically, but has [equivalent tool] experience (transferable skill)"
   NOT "CRITICAL: [required tool] requires Xyr, candidate has ~0.0yr"

CRITICAL INSTRUCTIONS — READ CAREFULLY:
1. ONLY reference skills, technologies, and experience that are EXPLICITLY STATED in the resume text.
2. DO NOT infer, assume, or hallucinate any skills not directly mentioned in the resume.
3. If a MANDATORY job requirement skill is NOT mentioned in the resume, you MUST list it in gaps_identified.
4. For skills_match and experience_match, ONLY quote or paraphrase content that actually exists in the resume.
5. If the job requires specific technologies and the resume mentions NEITHER the exact tool NOR an equivalent/competing technology from the equivalency groups above, the candidate does NOT qualify. However, if the candidate has deep experience with a direct competitor tool in the same category (e.g., Tableau for Power BI, AWS for Azure), apply partial credit rather than marking as zero.
6. A candidate whose background is completely different from the job (e.g., DBA applying to FPGA role) should score BELOW 30.
7. LOCATION CHECK: If the job has a location requirement, verify candidate location matches. For remote jobs, same country is required. For on-site/hybrid, proximity to job location matters.

MANDATORY EVIDENCE EXTRACTION (you MUST complete this before assigning a score):
1. Identify the TOP 5-7 most critical MANDATORY requirements from the job description. If the JD lists more than 7, consolidate related items (e.g. merge multiple similar bullet points into one requirement). Do NOT create more than 7 entries in requirement_evidence.
2. For EACH requirement, search the ENTIRE resume for matching evidence — check all roles, skills sections, summary, certifications, and education.
3. Quote the EXACT resume text that satisfies each requirement, or state "No evidence found after full resume search".
4. The overall match_score MUST be mathematically consistent with the per-requirement evidence — if most requirements are met with strong evidence, the score must reflect that; if you cite a gap, the score must reflect the penalty.
5. If you claim a gap exists, you MUST have searched for ALL synonyms, dollar amounts, quantified achievements, and related terms for that requirement. For example, "budget management" evidence includes dollar amounts ("$8M budget"), revenue figures, P&L ownership, financial planning mentions, etc.
6. DO NOT flag a requirement as "No evidence found" if the resume contains clear evidence under different wording or in a different section.

GAP DESCRIPTION PRECISION (MANDATORY):
When writing gaps_identified, you MUST distinguish between these two cases:
- ABSENT: The skill or qualification is genuinely NOT mentioned anywhere in the resume. Use: "No evidence of [requirement] found in resume."
- PRESENT BUT INSUFFICIENT: The skill or qualification IS mentioned in the resume but does not fully satisfy the job\'s specific requirement (e.g., wrong recency, insufficient depth, not the primary focus). Use: "[Requirement] experience noted at [employer/context] ([dates]) but [specific reason it falls short — e.g., \'not primary focus in last 2 years\', \'experience predates required recency window\', \'mentioned only in skills list without supporting work history\']."
NEVER describe existing experience as "no evidence" or "no specific evidence." If the resume contains ANY mention of the skill — whether in a skills list, a prior role, or a certification — you must acknowledge it exists and explain WHY it does not satisfy the requirement. Saying "no evidence" when evidence exists is factually incorrect.

WORK AUTHORIZATION EVIDENCE EXTRACTION (when applicable):
If the job description contains US work authorization language ("US citizen", "W2 only", "no sponsorship", etc.):
1. You MUST populate the "work_authorization_analysis" section below.
2. You MUST enumerate ALL US-based roles from the resume with dates and locations.
3. You MUST sum total months and apply the inference tier from the Global Screening Instructions.
4. DO NOT flag "US citizenship not mentioned" as a gap if the candidate has 5+ years of US work experience — instead apply the inference tier (no penalty for 5+ years).
5. If the candidate has an explicit authorization statement on their resume (e.g., "Green Card", "US Citizen"), note it and apply no penalty per Rule 0.

Respond in JSON format with these exact fields:
{{
    "requirement_evidence": [
        {{
            "requirement": "<the specific job requirement being evaluated>",
            "evidence_found": "<EXACT quoted text from resume that matches this requirement, or \'No evidence found after full resume search\'>",
            "meets_requirement": true/false,
            "score_impact": "<\'no penalty\', \'minor gap (-3 to -5 pts)\', \'significant gap (-10 to -15 pts)\', or \'critical gap (-20+ pts)\'>"
        }}
    ],
    "work_authorization_analysis": {{
        "triggered": true/false,
        "trigger_reason": "<which rule was triggered and why, or \'No work authorization language in job description\'>",
        "explicit_statement": "<quote exact authorization text from resume if found, or \'None found\'>",
        "roles_enumerated": [
            {{"title": "<role title>", "company": "<company>", "dates": "<start - end>", "location": "<city, state/country>", "months": 0}}
        ],
        "total_months": 0,
        "total_years": 0.0,
        "inference_tier": "<e.g. \'5+ years - strong likelihood, no penalty\' or \'3-4 years - minor penalty (3-5 pts)\' or \'Under 3 years - standard gap scoring\' or \'N/A - not triggered\'>",
        "score_adjustment": "<e.g. \'No penalty applied per Rule 1 Tier 1\' or \'Minor reduction (3-5 pts) applied\' or \'N/A\'>"
    }},
    "technical_score": 0,
    "match_score": 0,
    "match_summary": "<2-3 sentence summary of overall fit. IMPORTANT: If there is a country mismatch, say \'The candidate is based in [country] but the job requires [work type] work from [job country], creating a location compliance issue.\' Do NOT use contradictory phrasing like \'mismatch which matches\'.>",
    "skills_match": "<ONLY list skills from the resume that directly match job requirements - quote from resume>",
    "experience_match": "<ONLY list experience from the resume that is relevant to the job - be specific>",
    "gaps_identified": "<Describe in natural prose ALL mandatory requirements NOT found in the resume INCLUDING location mismatches AND years-of-experience shortfalls. Separate multiple gaps with periods or semicolons. Return as a single cohesive string, NOT as a JSON array - this is critical>",
    "key_requirements": "<bullet list of the top 3-5 MANDATORY requirements from the job description>",
    "years_analysis": {{
        "<skill_name>": {{
            "required_years": 0,
            "estimated_years": 0.0,
            "meets_requirement": true,
            "calculation": "<step-by-step month arithmetic, e.g. \'Role1: (2023-2020)×12+(8-1)=43mo + Role2: (2021-2018)×12+(6-7)=35mo = 78mo/12 = 6.5yr\'>"
        }}
    }},
    "recency_analysis": {{
        "most_recent_role": "<title> at <company> (<start> – <end>)",
        "most_recent_role_relevant": true,
        "relevance_justification": "<cite specific shared duty/tool/domain overlap — required when most_recent_role_relevant=true, set to 'N/A' when false>",
        "second_recent_role": "<title> at <company> (<start> – <end>)",
        "second_recent_role_relevant": true,
        "last_relevant_role_ended": "<date or \'current\'>",
        "months_since_relevant_work": 0,
        "penalty_applied": 0,
        "reasoning": "<brief explanation of why roles are or are not relevant>"
    }},
    "employment_gap_analysis": {{
        "is_currently_employed": true,
        "last_role_end_date": "<\'present\' | \'YYYY-MM\' | \'unknown\' if no dates on resume>",
        "gap_months": 0,
        "penalty_applied": 0,
        "largest_midcareer_gap_months": 0,
        "midcareer_gap_between": "<\'Role A (end) → Role B (start)\' | \'none\'>",
        "midcareer_gap_penalty_applied": 0,
        "note": "<e.g. \'Currently employed — no penalty\' | \'Gap of 14 months — penalty -8pts\' | \'No employment dates found — soft note only\' | \'Mid-career gap: 18 months between X and Y — penalty -4pts\'>"
    }},
    "experience_level_classification": {{
        "classification": "<FRESH_GRAD | ENTRY | MID | SENIOR>",
        "total_professional_years": 0.0,
        "highest_role_type": "<PROFESSIONAL_FULLTIME | PROFESSIONAL_CONTRACT | INTERNSHIP_ONLY | ACADEMIC_ONLY>"
    }}
}}

EXPERIENCE LEVEL CLASSIFICATION (MANDATORY):
Before scoring, classify the candidate\'s experience level based on their PROFESSIONAL work history:
- FRESH_GRAD: Only internships, academic projects, or graduated within the last 12 months with no full-time professional roles.
- ENTRY: Less than 2 years of professional (non-intern) experience.
- MID: 2-5 years of professional experience.
- SENIOR: 6+ years of professional experience.
total_professional_years counts ONLY paid, non-intern, non-academic roles. Internships count at 50% weight.
highest_role_type reflects the most senior type of role held (PROFESSIONAL_FULLTIME > PROFESSIONAL_CONTRACT > INTERNSHIP_ONLY > ACADEMIC_ONLY).
IMPORTANT — MISSING EMPLOYMENT DATES: If the resume contains NO date ranges or month/year references for any role (i.e. you cannot apply the duration formula because start/end dates are simply absent), apply the following rules in order:
1. If an education END date (graduation year) is present: estimate total_professional_years as (current year − graduation year). Treat this as INFERRED experience, not verified. Flag it by prefixing the classification note with "[Inferred from education end date]". Do NOT allow inferred years alone to satisfy a mandatory requirement of 3+ years — inferred experience should result in meets_requirement: false for strict multi-year requirements unless corroborated by strong skills evidence.
2. If no education end date is present either: set total_professional_years to a conservative low estimate (default 2.0). Do NOT infer seniority from job titles alone.
In all cases, only set total_professional_years above 5.0 if explicit date ranges in the resume support that calculation.

TWO-PHASE SCORING (MANDATORY):
You MUST produce TWO scores:
1. technical_score (integer 0-100): Assess the candidate\'s fit based SOLELY on technical skills, experience depth, years of experience, education, and qualifications. DO NOT consider location, work type, or geographic factors in this score. This score answers: "How well does this candidate fit the role if location were irrelevant?"
2. match_score (integer 0-100): The final score AFTER applying any location penalty. If the candidate\'s location matches the job requirements, match_score equals technical_score. If there is a location mismatch, reduce match_score by:
   - On-site jobs, candidate not in city/metro area: reduce by 20-30 points
   - Hybrid jobs, candidate not reasonably commutable: reduce by 15-25 points
   - Remote jobs, candidate in wrong country: reduce by 20-30 points
   - If no location issue exists: match_score = technical_score
When a location penalty is applied, you MUST document it explicitly in gaps_identified:
   "Location mismatch: candidate in [X], job requires [work type] in [Y]. Technical fit: [technical_score]%. Location penalty: -[N] pts."
IMPORTANT: You MUST complete the full technical assessment (all requirement_evidence entries, years_analysis, skills_match, experience_match) BEFORE considering location. A location mismatch must NEVER cause you to skip or abbreviate the technical analysis.

NOTES QUALITY (MANDATORY):
Your gaps_identified and match_summary fields must provide enough reasoning for a recruiter who has NOT read the resume to understand exactly why the candidate scored as they did. When more than one gap exists, use bullet-point format (separate with " | "). Each gap must state: (1) what is required, (2) what was found in the resume (or "not found"), and (3) why it does not satisfy the requirement.

SCORING GUIDELINES (apply to technical_score — location penalty adjusts match_score separately):
- 85-100: Candidate meets ALL mandatory requirements with explicit evidence in resume, meets or exceeds ALL required years of experience per skill, AND has practiced relevant skills in a recent role (within last 12 months)
- 70-84: Candidate meets MOST mandatory requirements, may have 1-2 minor gaps or be 1 year short on a non-critical skill
- 65-75: Candidate has strong equivalent experience with competing tools in the same category — core competencies align but specific tool experience is limited (transferable skills present)
- 50-69: Candidate has relevant skills but INSUFFICIENT years of professional experience for required skills, OR is missing key qualifications, OR has equivalent tools but lacks the specific required tool
- 30-49: Candidate has tangential experience, significant experience/years gaps
- 0-29: Candidate\'s background does not align with the role (wrong field/specialty)

CRITICAL SCORING RULES:
- If a job requires "X+ years" for a skill and the candidate has < (X-2) years, the score MUST be <= 60.
- University projects, coursework, and hackathons are NOT professional experience and do NOT count toward years.
- A candidate fresh out of school with only internships CANNOT score 85+ for a role requiring 3+ years of professional experience.
- If experience_level_classification is FRESH_GRAD or ENTRY and any requirement specifies 3+ years of experience, the match_score MUST NOT exceed 55.
- "Experience with deployment workflows", "production deployment", or similar deployment/operations requirements are ONLY satisfied by professional (non-academic, non-intern) deployment experience. Coursework deployments (Streamlit, Railway, Heroku, hobby Docker) do NOT satisfy production deployment requirements.
- BE HONEST. If the resume does not show the required skills or sufficient years, the technical_score should be LOW. If location also doesn\'t match, the match_score must reflect BOTH the technical gaps AND the location penalty."""

            response = self.openai_client.chat.completions.create(
                model=model_override or self.model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=2500
            )

            # ── Prompt cache hit logging ──
            try:
                usage = response.usage
                prompt_tokens = usage.prompt_tokens if usage else 0
                cached_tokens = 0
                if usage and hasattr(usage, 'prompt_tokens_details') and usage.prompt_tokens_details:
                    details = usage.prompt_tokens_details
                    cached_tokens = getattr(details, 'cached_tokens', 0) or 0
                if prompt_tokens > 0:
                    hit_pct = (cached_tokens / prompt_tokens) * 100
                    logging.info(f"💰 Cache: {cached_tokens} cached / {prompt_tokens} prompt tokens ({hit_pct:.0f}% hit rate) for job {job_id}")
                else:
                    logging.info(f"💰 Cache: usage data unavailable for job {job_id}")
            except Exception as _cache_log_err:
                logging.debug(f"Cache logging error (non-fatal): {_cache_log_err}")

            result = json.loads(response.choices[0].message.content)
            
            # ── Layer 2: Normalize text fields that GPT may return as arrays ──
            for field in ['gaps_identified', 'match_summary', 'skills_match', 'experience_match', 'key_requirements']:
                if isinstance(result.get(field), list):
                    result[field] = ". ".join(str(item) for item in result[field])
                    logging.warning(f"Normalized {field} from array to string for job {job_id}")
            
            # Diagnostic: log raw GPT response score before integer conversion
            raw_score = result.get('match_score')
            logging.info(f"📊 Raw GPT score for job {job_id}: {raw_score} (type: {type(raw_score).__name__})")
            
            # Ensure match_score and technical_score are integers
            result['match_score'] = int(result.get('match_score', 0))
            raw_tech = result.get('technical_score')
            if raw_tech is not None:
                result['technical_score'] = int(raw_tech)
            else:
                result['technical_score'] = result['match_score']
            
            # ── POST-PROCESSING: Remote location misfire enforcer (Python safety net) ──
            # GPT-4o sometimes simultaneously says "matches the country requirement" in the
            # summary while putting "Location mismatch: different country" in gaps_identified.
            # This deterministic check catches and corrects the contradiction.
            if work_type == 'Remote':
                _gaps_text = (result.get('gaps_identified') or '').lower()
                _summary_text = (result.get('match_summary') or '').lower()
                if 'location mismatch: different country' in _gaps_text or 'different country' in _gaps_text:
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
                        import re as _re
                        _original_gaps = result.get('gaps_identified', '')
                        _cleaned_gaps = _re.sub(
                            r'Location mismatch: different country\.?\s*Technical fit:\s*\d+%\.?\s*Location penalty:\s*-?\d+\s*pts\.?\s*',
                            '', _original_gaps, flags=_re.IGNORECASE
                        ).strip()
                        _cleaned_gaps = _re.sub(
                            r'Location mismatch: different country\.?\s*',
                            '', _cleaned_gaps, flags=_re.IGNORECASE
                        ).strip()
                        _cleaned_gaps = _re.sub(r'^[\s|.]+|[\s|.]+$', '', _cleaned_gaps).strip()
                        result['gaps_identified'] = _cleaned_gaps
                        _restored_score = min(
                            result['match_score'] + 25,
                            result.get('technical_score', result['match_score'] + 25)
                        )
                        logging.warning(
                            f"🛡️ REMOTE LOCATION ENFORCER: Fixed misfire for job {job_id}. "
                            f"AI said same-country in summary but applied different-country penalty in gaps. "
                            f"Score restored: {result['match_score']}→{_restored_score}. "
                            f"Gaps cleaned: '{_original_gaps[:100]}' → '{_cleaned_gaps[:100]}'"
                        )
                        result['match_score'] = _restored_score

            # ── POST-PROCESSING: Years-of-experience hard gate (Option B defense-in-depth) ──
            # Even if the AI prompt correctly penalizes for insufficient years,
            # this code enforces a hard ceiling so inflated scores cannot slip through.
            
            # Platform age ceilings — maximum years any candidate could possibly have
            # as of 2026, based on when these tools became enterprise-grade.
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
            
            years_analysis = result.get('years_analysis', {})
            if isinstance(years_analysis, dict) and years_analysis:
                original_score = result['match_score']
                max_shortfall = 0.0
                shortfall_details = []
                
                for skill, data in years_analysis.items():
                    if not isinstance(data, dict):
                        continue
                    meets = data.get('meets_requirement', True)
                    if not meets:
                        required = float(data.get('required_years', 0))
                        # Skip entries where no years requirement was specified
                        # (AI sometimes returns 0yr instead of omitting the entry)
                        if required <= 0:
                            continue
                        estimated = float(data.get('estimated_years', 0))
                        
                        # Apply platform age ceiling: if required_years exceeds what is
                        # physically achievable for this technology, cap the effective
                        # requirement at the ceiling before computing shortfall.
                        skill_lower = skill.lower()
                        ceiling = None
                        for platform_key, platform_ceiling in PLATFORM_AGE_CEILINGS.items():
                            if platform_key in skill_lower:
                                ceiling = platform_ceiling
                                break
                        
                        if ceiling is not None and required > ceiling:
                            logging.info(
                                f"📐 Platform age ceiling applied for job {job_id}: "
                                f"'{skill}' requires {required:.0f}yr but platform max is ~{ceiling:.0f}yr. "
                                f"Effective requirement adjusted to {ceiling:.0f}yr."
                            )
                            required = ceiling
                            data['required_years'] = ceiling
                        
                        shortfall = required - estimated
                        if shortfall > max_shortfall:
                            max_shortfall = shortfall
                        shortfall_details.append(
                            f"CRITICAL: {skill} requires {required:.0f}yr, candidate has ~{estimated:.1f}yr"
                        )
                
                if max_shortfall >= 2.0:
                    # Before capping score, re-check the years calculation to prevent false negatives
                    # from arithmetic errors (e.g., model miscounting 3.75yr as 1.8yr)
                    recheck_result = self._recheck_years_calculation(
                        resume_text, years_analysis, job_id, job_title
                    )
                    if recheck_result:
                        # Re-check returned corrected data — recalculate shortfalls
                        years_analysis = recheck_result
                        result['years_analysis'] = recheck_result
                        max_shortfall = 0.0
                        shortfall_details = []
                        for skill, data in years_analysis.items():
                            if not isinstance(data, dict):
                                continue
                            meets = data.get('meets_requirement', True)
                            if not meets:
                                required = float(data.get('required_years', 0))
                                # Skip entries where no years requirement was specified
                                if required <= 0:
                                    continue
                                estimated = float(data.get('estimated_years', 0))
                                # Re-apply platform age ceiling on re-check pass too
                                skill_lower = skill.lower()
                                for platform_key, platform_ceiling in PLATFORM_AGE_CEILINGS.items():
                                    if platform_key in skill_lower and required > platform_ceiling:
                                        required = platform_ceiling
                                        data['required_years'] = platform_ceiling
                                        break
                                shortfall = required - estimated
                                if shortfall > max_shortfall:
                                    max_shortfall = shortfall
                                shortfall_details.append(
                                    f"CRITICAL: {skill} requires {required:.0f}yr, candidate has ~{estimated:.1f}yr"
                                )
                    
                    # Apply hard cap only if shortfall is STILL >= 2.0 after re-check
                    if max_shortfall >= 2.0:
                        if result['match_score'] > 60:
                            result['match_score'] = 60
                            logging.info(
                                f"📉 Years hard gate: capped score {original_score}→60 for job {job_id} "
                                f"(shortfall: {max_shortfall:.1f}yr, confirmed by re-check)"
                            )
                    elif max_shortfall >= 1.0:
                        result['match_score'] = max(0, result['match_score'] - 15)
                        if result['match_score'] != original_score:
                            logging.info(
                                f"📉 Years penalty: reduced score {original_score}→{result['match_score']} for job {job_id} "
                                f"(shortfall: {max_shortfall:.1f}yr, adjusted after re-check)"
                            )
                    else:
                        # Re-check overturned the shortfall — no penalty
                        logging.info(
                            f"✅ Years re-check OVERTURNED shortfall for job {job_id}: "
                            f"now meets requirements (max remaining shortfall: {max_shortfall:.1f}yr). "
                            f"Score {original_score} preserved."
                        )
                elif max_shortfall >= 1.0:
                    # Significant penalty: 1-2 year shortfall → reduce by 15 points
                    result['match_score'] = max(0, result['match_score'] - 15)
                    if result['match_score'] != original_score:
                        logging.info(
                            f"📉 Years penalty: reduced score {original_score}→{result['match_score']} for job {job_id} "
                            f"(shortfall: {max_shortfall:.1f}yr)"
                        )
                
                # Append shortfall details to gaps_identified
                if shortfall_details:
                    existing_gaps = result.get('gaps_identified', '') or ''
                    gap_suffix = ' | '.join(shortfall_details)
                    if existing_gaps:
                        result['gaps_identified'] = f"{existing_gaps} | {gap_suffix}"
                    else:
                        result['gaps_identified'] = gap_suffix
            
            # ── POST-PROCESSING: Recency-of-experience hard gate (Rule 14 defense-in-depth) ──
            # Penalizes candidates whose most recent roles are unrelated to the job domain.
            recency_analysis = result.get('recency_analysis', {})
            if isinstance(recency_analysis, dict) and recency_analysis:
                recency_original_score = result['match_score']
                most_recent_relevant = recency_analysis.get('most_recent_role_relevant', True)
                second_recent_relevant = recency_analysis.get('second_recent_role_relevant', True)
                months_since = recency_analysis.get('months_since_relevant_work', 0)
                ai_penalty = recency_analysis.get('penalty_applied', 0)

                # ── JUSTIFICATION ENFORCEMENT: If AI says relevant=True but provides no/weak justification, override to False ──
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
                        logging.warning(
                            f"🔍 JUSTIFICATION ENFORCER: AI marked most_recent_role_relevant=True "
                            f"for job {job_id} but justification is missing/weak: '{_justification[:100]}'. "
                            f"Overriding to relevant=False."
                        )
                        most_recent_relevant = False
                        recency_analysis['most_recent_role_relevant'] = False

                # ── SAFEGUARD: Detect recency gate misfires on technology-evolution careers ──
                # If the AI marks the most recent role as "not relevant" but reports
                # months_since_relevant > 24, cross-check against the most_recent_role string.
                # If the most recent role description contains domain-relevant keywords
                # (data engineering, cloud, ETL, Spark, Databricks, etc.) this is a misfire —
                # the AI anchored recency on an older role that explicitly named a legacy tool
                # while missing that the current role is clearly in the same domain.
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
                        # Most recent role is clearly in the relevant domain — override misfire
                        logging.warning(
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
                
                # Determine the correct penalty tier
                if not most_recent_relevant and not second_recent_relevant:
                    # Both recent roles unrelated → 15-25 point penalty
                    target_penalty = 20  # Midpoint of 15-25 range
                    recency_note = (
                        "Candidate has not practiced relevant skills in their last two positions; "
                        "career trajectory has shifted away from this domain."
                    )
                elif not most_recent_relevant and months_since >= 12:
                    # Most recent role unrelated + relevant work ended 12+ months ago → 10-15
                    target_penalty = 12  # Midpoint of 10-15 range
                    recency_note = (
                        "Candidate's most recent professional activity is outside the target domain; "
                        "relevant experience is not current."
                    )
                else:
                    target_penalty = 0
                    recency_note = None
                
                if target_penalty > 0:
                    # Apply the larger of AI penalty and hard gate penalty
                    effective_penalty = max(target_penalty, ai_penalty)
                    new_score = max(0, recency_original_score - effective_penalty)
                    
                    if new_score < result['match_score']:
                        result['match_score'] = new_score
                        logging.info(
                            f"📉 Recency hard gate: reduced score {recency_original_score}→{new_score} "
                            f"for job {job_id} (penalty: {effective_penalty}pts, "
                            f"months_since_relevant: {months_since})"
                        )
                    
                    # Append recency note to gaps_identified
                    if recency_note:
                        existing_gaps = result.get('gaps_identified', '') or ''
                        if existing_gaps:
                            result['gaps_identified'] = f"{existing_gaps} | {recency_note}"
                        else:
                            result['gaps_identified'] = recency_note

            # ── POST-PROCESSING: Mid-career employment gap enforcer ──
            # Validates the AI's mid-career gap reporting and enforces penalties
            # only when the AI under-applied the correct penalty (idempotent).
            # Adjusts technical_score first, then recomputes match_score accordingly.
            _gap_analysis = result.get('employment_gap_analysis', {})
            if isinstance(_gap_analysis, dict) and _gap_analysis:
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
                    logging.info(
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

            # ── POST-PROCESSING: Experience floor gate (defense-in-depth for fresh-grad profiles) ──
            # Detects candidates with FRESH_GRAD/ENTRY classification matched against roles
            # requiring 3+ years, and caps the score. Also cross-checks the AI's years_analysis
            # to override obviously incorrect meets_requirement flags.
            import re as _re_exp
            exp_class = result.get('experience_level_classification', {})
            if isinstance(exp_class, dict) and exp_class:
                classification = exp_class.get('classification', '').upper()
                highest_role = exp_class.get('highest_role_type', '').upper()
                # Default to 3.0 years when AI classification parsing fails — conservative
                # rather than permissive, avoiding false "experienced" assumptions on
                # resumes where the AI could not determine actual years.
                professional_years = 3.0
                try:
                    professional_years = float(exp_class.get('total_professional_years', 3.0))
                except (ValueError, TypeError):
                    pass
                
                # Detect minimum years requirement from available requirements text
                # Check custom/prefetched requirements, AI key_requirements, and job description
                requirements_text_combined = ' '.join(filter(None, [
                    custom_requirements or '',
                    result.get('key_requirements', ''),
                    job_description or ''
                ]))
                years_match = _re_exp.search(
                    r'(?:minimum\s+)?(\d+)\+?\s*years?\s*(?:of\s+)?(?:experience|professional)',
                    requirements_text_combined, _re_exp.IGNORECASE
                )
                required_min_years = int(years_match.group(1)) if years_match else 0
                
                exp_floor_original_score = result['match_score']
                
                # Gate 1: FRESH_GRAD or ENTRY with 3+ years requirement → cap at 55
                if classification in ('FRESH_GRAD', 'ENTRY') and required_min_years >= 3:
                    if result['match_score'] > 55:
                        result['match_score'] = 55
                        logging.info(
                            f"📉 Experience floor: capped {exp_floor_original_score}→55 "
                            f"for job {job_id} (classification={classification}, "
                            f"professional_years={professional_years:.1f}, "
                            f"required={required_min_years}yr)"
                        )
                        # Append gap note
                        floor_gap = (
                            f"Experience floor: candidate classified as {classification} "
                            f"({professional_years:.1f}yr professional) vs {required_min_years}yr required."
                        )
                        existing_gaps = result.get('gaps_identified', '') or ''
                        if existing_gaps:
                            result['gaps_identified'] = f"{existing_gaps} | {floor_gap}"
                        else:
                            result['gaps_identified'] = floor_gap
                
                # Gate 2: Cross-check — override meets_requirement when intern-only
                # profile claims to meet 3+ year requirements
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
                                logging.warning(
                                    f"⚠️ Experience floor override: {skill} "
                                    f"meets_requirement forced to false for job {job_id} "
                                    f"(intern-only profile, {professional_years:.1f}yr professional)"
                                )
                        
                        # Re-run years gate logic with corrected data
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
                                logging.info(
                                    f"📉 Experience floor re-check: capped at 60 for job {job_id} "
                                    f"(shortfall: {max_shortfall_recheck:.1f}yr after override)"
                                )
                            elif max_shortfall_recheck >= 1.0:
                                new_score = max(0, result['match_score'] - 15)
                                if new_score < result['match_score']:
                                    result['match_score'] = new_score
                            
                            # Append shortfall details if not already present
                            if shortfall_details_recheck:
                                existing_gaps = result.get('gaps_identified', '') or ''
                                for detail in shortfall_details_recheck:
                                    if detail not in existing_gaps:
                                        if existing_gaps:
                                            existing_gaps = f"{existing_gaps} | {detail}"
                                        else:
                                            existing_gaps = detail
                                result['gaps_identified'] = existing_gaps
                
                # Gate 3: Catch-all — INTERNSHIP_ONLY with <1yr professional capped at 65
                # This fires even when the AI omits year requirements from key_requirements
                # and years_analysis, preventing the AI from bypassing Gates 1 & 2 entirely.
                if (highest_role in ('INTERNSHIP_ONLY', 'ACADEMIC_ONLY') and
                        professional_years < 1.0 and result['match_score'] > 65):
                    gate3_original = result['match_score']
                    result['match_score'] = 65
                    logging.info(
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

            # ── POST-PROCESSING: Location barrier detection ──
            # On-site/Hybrid jobs where the AI flagged a location mismatch are marked
            # so that process_candidate can override is_qualified even if score >= threshold.
            _gaps_text = result.get('gaps_identified', '') or ''
            if work_type in ('On-site', 'Hybrid') and 'location mismatch' in _gaps_text.lower():
                result['is_location_barrier'] = True
                logging.info(
                    f"📍 Location barrier detected for job {job_id}: "
                    f"work_type={work_type}, score={result['match_score']}"
                )

            # Save AI-interpreted requirements for future reference/editing
            key_requirements = result.get('key_requirements', '')
            logging.info(f"📋 AI response for job {job_id}: score={result['match_score']}%, has_requirements={bool(key_requirements)}, has_custom={bool(custom_requirements)}, years_analysis={bool(years_analysis)}")
            
            # Serialize years_analysis for database persistence (auditability)
            years_analysis_json = json.dumps(years_analysis) if years_analysis else None
            result['_years_analysis_json'] = years_analysis_json
            
            # Store data for deferred saving (to avoid Flask app context issues in parallel threads)
            # The caller should save these after parallel execution completes
            # ALWAYS save AI interpretation - custom requirements SUPPLEMENT, not REPLACE
            result['_deferred_save'] = {
                'job_id': job_id,
                'job_title': job_title,
                'key_requirements': key_requirements,
                'job_location_full': job_location_full,
                'work_type': work_type,
                'should_save': bool(key_requirements)  # Always save when we have requirements
            }
            
            if not key_requirements:
                logging.warning(f"⚠️ AI did not return key_requirements for job {job_id} - requirements will not be saved")
            elif custom_requirements:
                logging.info(f"📝 Job {job_id} has custom requirements - AI interpretation will ALSO be saved (custom supplements AI)")
            # NOTE: Actual save is now deferred to caller to avoid Flask app context issues in parallel threads
            
            return result
            
        except Exception as e:
            logging.error(f"AI analysis error for job {job_id}: {str(e)}")
            return {
                'match_score': 0,
                'match_summary': f'Analysis failed: {str(e)}',
                'skills_match': '',
                'experience_match': '',
                'gaps_identified': '',
                'key_requirements': ''
            }

