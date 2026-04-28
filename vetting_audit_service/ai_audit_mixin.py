"""Auto-split from vetting_audit_service.py — see vetting_audit_service/__init__.py."""
import json
import logging
import os
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

from .helpers import get_auditor_model

logger = logging.getLogger(__name__)

class AIAuditMixin:
    """AI confirmation step — calls the auditor model with heuristic findings."""

    def _run_ai_audit(self, job_match, resume_text: str, job_title: str,
                      suspected_issues: List[Dict], mode: str = 'not_qualified') -> Dict:
        if not self.openai_api_key:
            logger.error("OpenAI API key not available for screening audit")
            return {'finding_type': 'no_issue', 'confidence': 'low', 'reasoning': 'No API key'}

        issues_text = '\n'.join(
            f"- [{issue['check_type']}] {issue['description']}"
            for issue in suspected_issues
        )

        resume_snippet = resume_text[:4000] if resume_text else 'No resume text available'
        gaps = job_match.gaps_identified or 'None recorded'
        score = job_match.match_score or 0
        summary = job_match.match_summary or 'No summary'

        if mode == 'qualified_false_positive':
            prompt = f"""You are a quality auditor for an AI-powered candidate screening system.
A candidate was scored {score}% (Qualified — recommended to a recruiter) for the job: "{job_title}".

ORIGINAL AI ASSESSMENT:
- Match Summary: {summary}
- Gaps Identified: {gaps}

SUSPECTED FALSE-POSITIVE SIGNALS (flagged by heuristic pre-checks):
{issues_text}

CANDIDATE RESUME (first 4000 chars):
{resume_snippet}

YOUR TASK:
Review each suspected signal and determine if the candidate was OVER-SCORED — i.e.,
the original AI assessment looks favorable but the resume actually fails one or more
mandatory requirements.

For each suspected signal, consider:
1. If gaps mention multiple mandatory skills missing, are those skills genuinely absent from the resume? (If absent, the score should NOT be Qualified.)
2. If the summary uses negative language ("lacks", "limited experience", "no evidence"), does that contradict a score of {score}%?
3. If years_analysis shows large experience shortfalls (less than half required years), did the AI under-weight the requirement?
4. Is there a clear mandatory requirement (e.g., active security clearance, specific certification, location/work-authorization compliance) that the resume cannot satisfy?

Respond in JSON format:
{{
    "finding_type": "<false_positive_skill_gap | false_positive_experience_short | false_positive_negative_summary | false_positive_compliance | no_issue>",
    "confidence": "<high | medium | low>",
    "reasoning": "<2-3 sentence explanation of your finding>",
    "recommended_action": "<revet | flag_for_review | no_action>"
}}

IMPORTANT:
- Only return "high" confidence if the over-scoring is clear and unambiguous
- If multiple issues are confirmed, pick the MOST impactful one as finding_type
- "no_issue" means the Qualified score was correct despite the heuristic flag
- Be conservative: false alarms cost recruiters trust, so prefer "medium" / "low" over "high" when in doubt"""
        else:
            prompt = f"""You are a quality auditor for an AI-powered candidate screening system.
A candidate was scored {score}% (Not Qualified) for the job: "{job_title}".

ORIGINAL AI ASSESSMENT:
- Match Summary: {summary}
- Gaps Identified: {gaps}

SUSPECTED ISSUES (flagged by heuristic pre-checks):
{issues_text}

CANDIDATE RESUME (first 4000 chars):
{resume_snippet}

YOUR TASK:
Review each suspected issue and determine if the original AI assessment contains a genuine error.

For each issue, consider:
1. Is the candidate's CURRENT role relevant to the job domain? Check their most recent position.
2. Are any year-of-experience requirements physically impossible given the technology's age?
3. Does the AI summary contradict the score (positive language but low score)?
4. Are there skills mentioned in the resume that the AI incorrectly said were missing?
5. Does the years_analysis data show the candidate MEETS a requirement but the AI marked it as not met?
6. Was the candidate penalized for an employment gap even though their resume shows "Present" or "Current" employment?
7. Did the AI flag a work authorization concern but also state it infers strong authorization likelihood?

Respond in JSON format:
{{
    "finding_type": "<recency_misfire | platform_age_violation | false_gap_claim | score_inconsistency | experience_undercounting | employment_gap_misfire | authorization_misfire | no_issue>",
    "confidence": "<high | medium | low>",
    "reasoning": "<2-3 sentence explanation of your finding>",
    "recommended_action": "<revet | flag_for_review | no_action>"
}}

IMPORTANT:
- Only return "high" confidence if the error is clear and unambiguous
- If multiple issues are confirmed, pick the MOST impactful one as finding_type
- "no_issue" means the original assessment was correct despite the heuristic flag"""

        try:
            import httpx
            response = httpx.post(
                'https://api.openai.com/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {self.openai_api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': get_auditor_model(),
                    'messages': [
                        {'role': 'system', 'content': 'You are a quality auditor. Respond only in valid JSON.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    'max_completion_tokens': 1500,
                    'response_format': {'type': 'json_object'}
                },
                timeout=30.0
            )
            response.raise_for_status()
            result = response.json()
            content = result['choices'][0]['message']['content']
            return json.loads(content)

        except Exception as e:
            logger.error(f"❌ AI audit call failed: {str(e)}")
            return {
                'finding_type': 'no_issue',
                'confidence': 'low',
                'reasoning': f'AI audit call failed: {str(e)}',
                'recommended_action': 'no_action'
            }
