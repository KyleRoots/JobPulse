import logging
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

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
    'terraform': 10.0,
    'docker': 12.0,
}

DOMAIN_KEYWORDS = [
    'data engineer', 'azure data', 'databricks', 'spark', 'etl', 'pipeline',
    'data lake', 'synapse', 'snowflake', 'cloud engineer', 'big data',
    'data warehouse', 'kafka', 'airflow', 'dbt', 'analytics engineer',
    'machine learning', 'ml engineer', 'ai engineer', 'software engineer',
    'devops', 'platform engineer', 'backend engineer', 'data architect',
    'full stack', 'frontend engineer', 'cloud architect', 'sre',
    'infrastructure engineer', 'data scientist', 'business intelligence',
]


class VettingAuditService:
    """AI-powered quality auditor for Scout Screening results.
    
    Tier 1: Runs heuristic checks on recent Not Qualified results,
    confirms findings with GPT-4o, and auto-triggers re-vets for
    high-confidence misfires.
    """

    def __init__(self):
        self.openai_api_key = os.environ.get('OPENAI_API_KEY')

    def run_audit_cycle(self, batch_size=20):
        from app import db
        from models import (
            CandidateVettingLog, CandidateJobMatch, VettingAuditLog,
            VettingConfig, ParsedEmail, EmbeddingFilterLog, EscalationLog,
            EmailDeliveryLog
        )

        summary = {
            'total_audited': 0,
            'issues_found': 0,
            'revets_triggered': 0,
            'details': []
        }

        try:
            from sqlalchemy import and_

            already_audited = db.session.query(VettingAuditLog.candidate_vetting_log_id).subquery()

            candidates = CandidateVettingLog.query.filter(
                CandidateVettingLog.status == 'completed',
                CandidateVettingLog.is_qualified == False,
                CandidateVettingLog.is_sandbox == False,
                ~CandidateVettingLog.id.in_(
                    db.session.query(already_audited)
                )
            ).order_by(
                CandidateVettingLog.analyzed_at.desc()
            ).limit(batch_size).all()

            if not candidates:
                logging.info("🔍 Screening audit: no new unaudited results to review")
                return summary

            logging.info(f"🔍 Screening audit: reviewing {len(candidates)} unaudited results")

            for vetting_log in candidates:
                try:
                    applied_match = CandidateJobMatch.query.filter_by(
                        vetting_log_id=vetting_log.id,
                        is_applied_job=True
                    ).first()

                    if not applied_match:
                        applied_match = CandidateJobMatch.query.filter_by(
                            vetting_log_id=vetting_log.id
                        ).order_by(CandidateJobMatch.match_score.desc()).first()

                    if not applied_match:
                        audit_log = VettingAuditLog(
                            candidate_vetting_log_id=vetting_log.id,
                            bullhorn_candidate_id=vetting_log.bullhorn_candidate_id,
                            candidate_name=vetting_log.candidate_name,
                            job_id=vetting_log.applied_job_id,
                            job_title=vetting_log.applied_job_title,
                            original_score=vetting_log.highest_match_score,
                            finding_type='no_issue',
                            action_taken='no_action',
                            audit_finding='No job match records found to audit'
                        )
                        db.session.add(audit_log)
                        db.session.commit()
                        summary['total_audited'] += 1
                        continue

                    suspected_issues = self._run_heuristic_checks(vetting_log, applied_match)

                    if not suspected_issues:
                        audit_log = VettingAuditLog(
                            candidate_vetting_log_id=vetting_log.id,
                            bullhorn_candidate_id=vetting_log.bullhorn_candidate_id,
                            candidate_name=vetting_log.candidate_name,
                            job_id=applied_match.bullhorn_job_id,
                            job_title=applied_match.job_title,
                            original_score=applied_match.match_score,
                            finding_type='no_issue',
                            confidence='high',
                            action_taken='no_action',
                            audit_finding='Heuristic checks passed — no issues detected'
                        )
                        db.session.add(audit_log)
                        db.session.commit()
                        summary['total_audited'] += 1
                        continue

                    logging.info(
                        f"⚠️ Screening audit: {len(suspected_issues)} suspected issue(s) "
                        f"for candidate {vetting_log.bullhorn_candidate_id} "
                        f"({vetting_log.candidate_name}) on job {applied_match.bullhorn_job_id}"
                    )

                    ai_finding = self._run_ai_audit(
                        applied_match,
                        vetting_log.resume_text or '',
                        applied_match.job_title or vetting_log.applied_job_title or '',
                        suspected_issues
                    )

                    finding_type = ai_finding.get('finding_type', 'no_issue')
                    confidence = ai_finding.get('confidence', 'low')
                    action_taken = 'no_action'
                    revet_new_score = None

                    if confidence == 'high' and finding_type != 'no_issue':
                        action_taken = 'revet_triggered'
                        revet_new_score = self._trigger_revet(
                            vetting_log.bullhorn_candidate_id,
                            vetting_log.id
                        )
                        summary['revets_triggered'] += 1
                        logging.info(
                            f"✅ Screening audit: re-vet triggered for candidate "
                            f"{vetting_log.bullhorn_candidate_id} ({vetting_log.candidate_name}). "
                            f"Original score: {applied_match.match_score}%, "
                            f"New score: {revet_new_score}%"
                        )
                    elif confidence == 'medium' and finding_type != 'no_issue':
                        action_taken = 'flagged_for_review'

                    summary['total_audited'] += 1
                    if finding_type != 'no_issue':
                        summary['issues_found'] += 1
                        summary['details'].append({
                            'candidate_id': vetting_log.bullhorn_candidate_id,
                            'candidate_name': vetting_log.candidate_name,
                            'job_title': applied_match.job_title,
                            'original_score': applied_match.match_score,
                            'finding_type': finding_type,
                            'confidence': confidence,
                            'action_taken': action_taken,
                            'new_score': revet_new_score
                        })

                    audit_log = VettingAuditLog(
                        candidate_vetting_log_id=vetting_log.id,
                        bullhorn_candidate_id=vetting_log.bullhorn_candidate_id,
                        candidate_name=vetting_log.candidate_name,
                        job_id=applied_match.bullhorn_job_id,
                        job_title=applied_match.job_title,
                        original_score=applied_match.match_score,
                        audit_finding=ai_finding.get('reasoning', ''),
                        finding_type=finding_type,
                        confidence=confidence,
                        action_taken=action_taken,
                        revet_new_score=revet_new_score
                    )
                    db.session.add(audit_log)
                    db.session.commit()

                except Exception as e:
                    logging.error(
                        f"❌ Screening audit error for candidate "
                        f"{vetting_log.bullhorn_candidate_id}: {str(e)}"
                    )
                    try:
                        audit_log = VettingAuditLog(
                            candidate_vetting_log_id=vetting_log.id,
                            bullhorn_candidate_id=vetting_log.bullhorn_candidate_id,
                            candidate_name=vetting_log.candidate_name,
                            finding_type='no_issue',
                            action_taken='no_action',
                            audit_finding=f'Audit error: {str(e)}'
                        )
                        db.session.add(audit_log)
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    summary['total_audited'] += 1

            if summary['issues_found'] > 0 or summary['revets_triggered'] > 0:
                try:
                    self._send_audit_summary_email(summary)
                    summary['email_sent'] = True
                except Exception as e:
                    logging.error(f"❌ Screening audit email error: {str(e)}")
                    summary['email_sent'] = False

            logging.info(
                f"✅ Screening audit cycle complete: "
                f"{summary['total_audited']} audited, "
                f"{summary['issues_found']} issues found, "
                f"{summary['revets_triggered']} re-vets triggered"
            )

        except Exception as e:
            logging.error(f"❌ Screening audit cycle failed: {str(e)}")

        return summary

    def _run_heuristic_checks(self, vetting_log, job_match) -> List[Dict]:
        issues = []

        gaps = (job_match.gaps_identified or '').lower()
        match_summary = (job_match.match_summary or '').lower()
        job_title = (job_match.job_title or '').lower()

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
                        for platform_key, ceiling in PLATFORM_AGE_CEILINGS.items():
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
                logging.debug(f"remote_location_misfire check error: {_loc_err}")

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

    def _run_ai_audit(self, job_match, resume_text: str, job_title: str,
                      suspected_issues: List[Dict]) -> Dict:
        if not self.openai_api_key:
            logging.error("OpenAI API key not available for screening audit")
            return {'finding_type': 'no_issue', 'confidence': 'low', 'reasoning': 'No API key'}

        issues_text = '\n'.join(
            f"- [{issue['check_type']}] {issue['description']}"
            for issue in suspected_issues
        )

        resume_snippet = resume_text[:4000] if resume_text else 'No resume text available'
        gaps = job_match.gaps_identified or 'None recorded'
        score = job_match.match_score or 0
        summary = job_match.match_summary or 'No summary'

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
                    'model': 'gpt-5',
                    'messages': [
                        {'role': 'system', 'content': 'You are a quality auditor. Respond only in valid JSON.'},
                        {'role': 'user', 'content': prompt}
                    ],
                    'max_completion_tokens': 500,
                    'response_format': {'type': 'json_object'}
                },
                timeout=30.0
            )
            response.raise_for_status()
            result = response.json()
            content = result['choices'][0]['message']['content']
            return json.loads(content)

        except Exception as e:
            logging.error(f"❌ AI audit call failed: {str(e)}")
            return {
                'finding_type': 'no_issue',
                'confidence': 'low',
                'reasoning': f'AI audit call failed: {str(e)}',
                'recommended_action': 'no_action'
            }

    def _trigger_revet(self, candidate_id: int, original_log_id: int) -> Optional[float]:
        from app import db, app
        from models import (
            CandidateVettingLog, CandidateJobMatch, ParsedEmail,
            EmbeddingFilterLog, EscalationLog, VettingAuditLog
        )

        try:
            parsed_emails = ParsedEmail.query.filter(
                ParsedEmail.bullhorn_candidate_id == candidate_id,
                ParsedEmail.status == 'completed'
            ).all()

            if not parsed_emails:
                logging.warning(f"No ParsedEmail records for candidate {candidate_id}")
                return None

            pe_ids = [pe.id for pe in parsed_emails]

            vetting_logs = CandidateVettingLog.query.filter(
                CandidateVettingLog.parsed_email_id.in_(pe_ids)
            ).all()

            log_ids = [vl.id for vl in vetting_logs]

            if log_ids:
                EmbeddingFilterLog.query.filter(
                    EmbeddingFilterLog.vetting_log_id.in_(log_ids)
                ).delete(synchronize_session=False)

                EscalationLog.query.filter(
                    EscalationLog.vetting_log_id.in_(log_ids)
                ).delete(synchronize_session=False)

                CandidateJobMatch.query.filter(
                    CandidateJobMatch.vetting_log_id.in_(log_ids)
                ).delete(synchronize_session=False)

                CandidateVettingLog.query.filter(
                    CandidateVettingLog.id.in_(log_ids)
                ).delete(synchronize_session=False)

            for pe in parsed_emails:
                pe.vetted_at = None

            db.session.commit()

            logging.info(
                f"🔄 Audit re-vet: reset candidate {candidate_id} — "
                f"cleared {len(log_ids)} vetting logs, reset {len(pe_ids)} ParsedEmails. "
                f"Will be picked up by next vetting cycle."
            )

            return None

        except Exception as e:
            db.session.rollback()
            logging.error(f"❌ Audit re-vet failed for candidate {candidate_id}: {str(e)}")
            return None

    def _send_audit_summary_email(self, summary: Dict):
        from app import db as app_db
        from models import VettingConfig, EmailDeliveryLog
        from email_service import EmailService

        admin_email = VettingConfig.get_value('admin_notification_email', '')
        if not admin_email:
            logging.warning("No admin_notification_email configured — skipping audit summary email")
            return

        details_html = ''
        if summary.get('details'):
            rows = ''
            for d in summary['details']:
                action_badge = {
                    'revet_triggered': '<span style="color: #22c55e;">✅ Re-vetted</span>',
                    'flagged_for_review': '<span style="color: #f59e0b;">⚠️ Flagged</span>',
                    'no_action': '<span style="color: #6b7280;">—</span>'
                }.get(d.get('action_taken', ''), '—')

                new_score_str = f"{d['new_score']:.0f}%" if d.get('new_score') is not None else 'Pending'

                rows += f"""<tr>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{d.get('candidate_name', 'Unknown')}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{d.get('job_title', 'Unknown')}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{d.get('original_score', 0):.0f}%</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{new_score_str}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{d.get('finding_type', '')}</td>
                    <td style="padding: 8px; border-bottom: 1px solid #374151;">{action_badge}</td>
                </tr>"""

            details_html = f"""
            <table style="width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 14px;">
                <thead>
                    <tr style="background: #1f2937; color: #e5e7eb;">
                        <th style="padding: 8px; text-align: left;">Candidate</th>
                        <th style="padding: 8px; text-align: left;">Job</th>
                        <th style="padding: 8px; text-align: left;">Original</th>
                        <th style="padding: 8px; text-align: left;">New</th>
                        <th style="padding: 8px; text-align: left;">Issue</th>
                        <th style="padding: 8px; text-align: left;">Action</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>"""

        html_body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto; background: #111827; color: #e5e7eb; padding: 24px; border-radius: 8px;">
            <h2 style="color: #f59e0b; margin-bottom: 16px;">🔍 Scout Screening Quality Audit</h2>
            <div style="background: #1f2937; padding: 16px; border-radius: 8px; margin-bottom: 16px;">
                <p style="margin: 4px 0;"><strong>Results Audited:</strong> {summary['total_audited']}</p>
                <p style="margin: 4px 0;"><strong>Issues Found:</strong> {summary['issues_found']}</p>
                <p style="margin: 4px 0;"><strong>Re-vets Triggered:</strong> {summary['revets_triggered']}</p>
            </div>
            {details_html}
            <p style="color: #6b7280; font-size: 12px; margin-top: 24px;">
                This is an automated quality audit from Scout Genius™. 
                Re-vetted candidates will have updated Bullhorn notes once the next vetting cycle processes them.
            </p>
        </div>
        """

        email_service = EmailService(db=app_db, EmailDeliveryLog=EmailDeliveryLog)
        email_service.send_email(
            to_email=admin_email,
            subject=f"Scout Screening Audit: {summary['issues_found']} issue(s) found, {summary['revets_triggered']} re-vet(s) triggered",
            html_content=html_body,
            notification_type='screening_audit_summary'
        )
        logging.info(f"📧 Audit summary email sent to {admin_email}")
