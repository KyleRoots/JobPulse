from __future__ import annotations
"""
Note Builder - Bullhorn note formatting and creation for screening results.

Contains:
- create_candidate_note: Creates structured notes on candidate records in Bullhorn
- _format_match_note_block: Formats individual job match blocks for notes
- _normalize_gaps_text: Normalizes gaps_identified field to clean prose
"""

import logging
import json
from datetime import datetime, timedelta
from app import db
from models import CandidateJobMatch, CandidateVettingLog, JobVettingRequirements


class NoteBuilderMixin:
    """Bullhorn note formatting and creation."""

    def _format_match_note_block(self, match, job_threshold_map, is_applied=False, show_gaps=False, candidate_id=None):
        lines = []
        lines.append(f"• Job ID: {match.bullhorn_job_id} - {match.job_title}")

        tech = match.technical_score
        has_location_penalty = (
            tech is not None
            and tech != match.match_score
            and 'location mismatch' in (match.gaps_identified or '').lower()
        )

        match_custom = job_threshold_map.get(match.bullhorn_job_id)
        if has_location_penalty:
            score_text = f"  Technical Fit: {tech:.0f}% → Location Penalty → Final: {match.match_score:.0f}%"
        else:
            score_text = f"  Match Score: {match.match_score:.0f}%"

        if match_custom:
            score_text += f"  |  Threshold: {match_custom:.0f}% (custom)"
        lines.append(score_text)

        if is_applied:
            lines.append(f"  ⭐ APPLIED TO THIS POSITION")

        lines.append(f"  Summary: {match.match_summary}")
        lines.append(f"  Skills: {match.skills_match}")

        if show_gaps and match.gaps_identified:
            gaps_text = self._normalize_gaps_text(match.gaps_identified, candidate_id)
            lines.append(f"  Gaps: {gaps_text}")

        return lines

    def _normalize_gaps_text(self, gaps, candidate_id=None):
        """Layer 3 safety net: normalize gaps_identified to clean prose.
        
        Handles:
        - list type: GPT returned an array that bypassed Layer 2
        - str starting with '[': legacy JSON array stored as string in DB
        - str: returned as-is (already clean prose)
        """
        if isinstance(gaps, list):
            logging.warning(f"Render-time array normalization for candidate {candidate_id}")
            return ". ".join(str(item) for item in gaps)
        
        if isinstance(gaps, str) and gaps.startswith('['):
            try:
                gaps_list = json.loads(gaps)
                if isinstance(gaps_list, list):
                    logging.warning(f"Render-time JSON string normalization for candidate {candidate_id}")
                    return ". ".join(str(item) for item in gaps_list)
            except json.JSONDecodeError:
                pass  # Not valid JSON, keep original
        
        return gaps
    
    def create_candidate_note(self, vetting_log: CandidateVettingLog) -> bool:
        """
        Create a note on the candidate record summarizing the vetting results.
        
        Args:
            vetting_log: The vetting log with analysis results
            
        Returns:
            True if note was created successfully (or already exists)
        """
        # DEDUPLICATION SAFETY: Skip if note already created for this vetting log
        if vetting_log.note_created:
            logging.info(f"⏭️ Note already exists for vetting log {vetting_log.id} (candidate {vetting_log.bullhorn_candidate_id}), skipping creation")
            return True  # Return True to indicate note exists
        
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return False
        
        # PRE-CREATION SAFEGUARD: Check Bullhorn for existing AI vetting notes (6h window)
        # This prevents duplicate notes even if upstream dedup logic has a bug.
        # "Incomplete" notes never block a new complete result — a successful re-screen
        # must always be able to overwrite a prior failure.
        from datetime import timedelta
        _INCOMPLETE_ACTIONS = {
            "Scout Screen - Incomplete",
            "Scout Screening - Incomplete",
            "AI Vetting - Incomplete",
        }
        try:
            existing_notes = bullhorn.get_candidate_notes(
                vetting_log.bullhorn_candidate_id,
                action_filter=[
                    "Scout Screen - Qualified",
                    "Scout Screen - Not Qualified",
                    "Scout Screen - Incomplete",
                    "Scout Screen - Loc Barrier",
                    "Scout Screen - Location Barrier",
                    "Scout Screening - Qualified",
                    "Scout Screening - Not Recommended",
                    "Scout Screening - Incomplete",
                    "AI Vetting - Qualified",
                    "AI Vetting - Not Recommended",
                    "AI Vetting - Incomplete"
                ],
                since=datetime.utcnow() - timedelta(hours=6)
            )
            if existing_notes:
                _all_incomplete = all(
                    n.get('action', '') in _INCOMPLETE_ACTIONS for n in existing_notes
                )
                _all_failed_analysis = all(
                    'Analysis failed' in (n.get('comments', '') or '')
                    or 'Match Score: 0%' in (n.get('comments', '') or '')
                    for n in existing_notes
                )
                _has_match_records = CandidateJobMatch.query.filter_by(
                    vetting_log_id=vetting_log.id
                ).count() > 0
                _is_supersedable = (_all_incomplete or _all_failed_analysis) and _has_match_records
                if _is_supersedable:
                    override_reason = "Incomplete" if _all_incomplete else "failed analysis (0%)"
                    logging.info(
                        f"ℹ️ DUPLICATE SAFEGUARD OVERRIDE: Candidate {vetting_log.bullhorn_candidate_id} "
                        f"has {len(existing_notes)} {override_reason} note(s) in Bullhorn from last 6h. "
                        f"Allowing new complete result to supersede."
                    )
                else:
                    logging.warning(
                        f"⚠️ DUPLICATE SAFEGUARD: Candidate {vetting_log.bullhorn_candidate_id} already has "
                        f"{len(existing_notes)} AI vetting note(s) in Bullhorn from last 6h. "
                        f"Skipping duplicate note creation."
                    )
                    vetting_log.note_created = True
                    vetting_log.bullhorn_note_id = existing_notes[0].get('id')
                    db.session.commit()
                    return True
        except Exception as e:
            # Don't block note creation if the safety check itself fails
            logging.warning(f"Pre-note duplicate check failed (proceeding with creation): {str(e)}")
        
        # Get all match results for this candidate
        matches = CandidateJobMatch.query.filter_by(
            vetting_log_id=vetting_log.id
        ).order_by(CandidateJobMatch.match_score.desc()).all()
        
        # Build note content
        # Header shows global threshold; inline annotations show per-job custom thresholds
        global_threshold = self.get_threshold()
        threshold = global_threshold
        qualified_matches = [m for m in matches if m.is_qualified] if matches else []
        
        # Pre-fetch per-job thresholds for matched jobs to annotate inline
        job_ids = [m.bullhorn_job_id for m in matches if m.bullhorn_job_id]
        job_threshold_map = {}
        if job_ids:
            try:
                from models import JobVettingRequirements
                custom_reqs = JobVettingRequirements.query.filter(
                    JobVettingRequirements.bullhorn_job_id.in_(job_ids),
                    JobVettingRequirements.vetting_threshold.isnot(None)
                ).all()
                for req in custom_reqs:
                    job_threshold_map[req.bullhorn_job_id] = float(req.vetting_threshold)
            except Exception as e:
                logging.warning(f"Could not fetch per-job thresholds for note: {str(e)}")
        
        # Handle case where no jobs were analyzed (no matches recorded)
        all_analysis_failed = matches and all(
            m.match_score == 0 and 'Analysis failed' in (m.match_summary or '')
            for m in matches
        )
        if not matches or all_analysis_failed:
            if all_analysis_failed:
                error_reason = "All job analyses returned API errors (0% scores)"
            else:
                error_reason = vetting_log.error_message or "No job matches could be performed"
            note_lines = [
                f"📋 SCOUT SCREENING - INCOMPLETE ANALYSIS",
                f"",
                f"Analysis Date: {vetting_log.analyzed_at.strftime('%Y-%m-%d %H:%M UTC') if vetting_log.analyzed_at else 'N/A'}",
                f"Status: {vetting_log.status}",
                f"",
                f"Reason: {error_reason}",
                f"",
                f"This candidate could not be fully analyzed. Possible causes:",
                f"• No active jobs found in monitored tearsheets",
                f"• Resume could not be extracted or parsed",
                f"• Technical issue during processing",
                f"",
                f"Please review manually if needed."
            ]
            note_text = "\n".join(note_lines)
            action = "Scout Screen - Incomplete"
            
            note_id = bullhorn.create_candidate_note(
                vetting_log.bullhorn_candidate_id,
                note_text,
                action=action
            )
            
            if note_id:
                vetting_log.note_created = True
                vetting_log.bullhorn_note_id = note_id
                db.session.commit()
                logging.info(f"Created incomplete vetting note for candidate {vetting_log.bullhorn_candidate_id}")
                return True
            else:
                logging.error(f"Failed to create incomplete vetting note for candidate {vetting_log.bullhorn_candidate_id}")
                return False
        
        # ── LOCATION BARRIER DETECTION ──
        # Candidate scored above the technical threshold but was overridden out of
        # qualified status because the role is on-site/hybrid and they're non-local.
        location_barrier_matches = [
            m for m in matches
            if not m.is_qualified
            and 'location mismatch' in (m.gaps_identified or '').lower()
            and (m.technical_score or m.match_score) >= (threshold - 15)
        ]
        is_location_barrier_candidate = (
            len(qualified_matches) == 0 and len(location_barrier_matches) > 0
        )

        if is_location_barrier_candidate:
            # Strong fit / location barrier note
            top_lb = sorted(location_barrier_matches, key=lambda m: (m.technical_score or m.match_score), reverse=True)
            top_tech = top_lb[0].technical_score or top_lb[0].match_score if top_lb else 0
            top_final = top_lb[0].match_score if top_lb else 0
            note_lines = [
                f"📍 SCOUT SCREENING - STRONG FIT / LOCATION BARRIER",
                f"",
                f"Analysis Date: {vetting_log.analyzed_at.strftime('%Y-%m-%d %H:%M UTC') if vetting_log.analyzed_at else 'N/A'}",
                f"Threshold: {threshold}%",
                f"Technical Fit: {top_tech:.0f}% (skills & experience, before location penalty)",
                f"Final Score: {top_final:.0f}% (after location penalty)",
                f"",
                f"This candidate is a strong technical fit but has a location barrier",
                f"for an on-site/hybrid position. Recruiter review is recommended.",
                f"",
                f"POSITION(S) AFFECTED:",
            ]
            for m in top_lb:
                tech = m.technical_score or m.match_score
                match_custom = job_threshold_map.get(m.bullhorn_job_id)
                if tech != m.match_score:
                    score_line = f"  Technical Fit: {tech:.0f}% → Location Penalty → Final: {m.match_score:.0f}%"
                else:
                    score_line = f"  Score: {m.match_score:.0f}%"
                if match_custom:
                    score_line += f"  |  Threshold: {match_custom:.0f}% (custom)"
                gaps_full = m.gaps_identified or ''
                loc_gap_parts = [
                    part.strip() for part in gaps_full.replace(' | ', '|').split('|')
                    if 'location mismatch' in part.lower()
                ]
                non_loc_parts = [
                    part.strip() for part in gaps_full.replace(' | ', '|').split('|')
                    if 'location mismatch' not in part.lower() and part.strip()
                ]
                loc_gap_text = ' | '.join(loc_gap_parts) if loc_gap_parts else ''
                note_lines += [
                    f"",
                    f"• Job ID: {m.bullhorn_job_id} - {m.job_title}",
                    score_line,
                    f"  ⚠️  LOCATION BARRIER",
                    f"  Summary: {m.match_summary}",
                    f"  Skills: {m.skills_match}",
                ]
                if non_loc_parts:
                    note_lines.append(f"  Other Gaps: {' | '.join(non_loc_parts)}")
                if loc_gap_text:
                    note_lines.append(f"  Location: {loc_gap_text}")
            note_text = "\n".join(note_lines)
            action = "Scout Screen - Loc Barrier"

            note_id = bullhorn.create_candidate_note(
                vetting_log.bullhorn_candidate_id,
                note_text,
                action=action
            )
            if note_id:
                vetting_log.note_created = True
                vetting_log.bullhorn_note_id = note_id
                db.session.commit()
                logging.info(
                    f"📍 Created location barrier note for candidate {vetting_log.bullhorn_candidate_id} "
                    f"(tech fit: {top_tech:.0f}%, final: {top_final:.0f}%)"
                )
                return True
            else:
                logging.error(f"Failed to create location barrier note for candidate {vetting_log.bullhorn_candidate_id}")
                return False

        elif vetting_log.is_qualified:
            # Qualified candidate note
            note_lines = [
                f"🎯 SCOUT SCREENING - QUALIFIED CANDIDATE",
                f"",
                f"Analysis Date: {vetting_log.analyzed_at.strftime('%Y-%m-%d %H:%M UTC') if vetting_log.analyzed_at else 'N/A'}",
                f"Threshold: {threshold}%",
                f"Qualified Matches: {len(qualified_matches)} of {len(matches)} jobs",
                f"Highest Match Score: {vetting_log.highest_match_score:.0f}%",
                f"",
            ]
            
            applied_match = None
            other_qualified = []
            for match in qualified_matches:
                if match.is_applied_job:
                    applied_match = match
                else:
                    other_qualified.append(match)
            
            other_qualified.sort(key=lambda m: m.match_score, reverse=True)
            
            if applied_match:
                note_lines.append(f"APPLIED POSITION (QUALIFIED):")
                note_lines.append(f"")
                note_lines += self._format_match_note_block(applied_match, job_threshold_map, is_applied=True)
                if other_qualified:
                    note_lines.append(f"")
                    note_lines.append(f"OTHER QUALIFIED POSITIONS:")
            else:
                note_lines.append(f"QUALIFIED POSITIONS:")
            
            for match in other_qualified:
                note_lines.append(f"")
                note_lines += self._format_match_note_block(match, job_threshold_map)
        else:
            # Not qualified note
            note_lines = [
                f"📋 SCOUT SCREENING - NOT RECOMMENDED",
                f"",
                f"Analysis Date: {vetting_log.analyzed_at.strftime('%Y-%m-%d %H:%M UTC') if vetting_log.analyzed_at else 'N/A'}",
                f"Threshold: {threshold}%",
                f"Highest Match Score: {vetting_log.highest_match_score:.0f}%",
                f"Jobs Analyzed: {len(matches)}",
                f"",
                f"This candidate did not meet the {threshold}% match threshold for any current open positions.",
                f"",
            ]
            
            applied_match = None
            other_matches = []
            for match in matches:
                if match.is_applied_job:
                    applied_match = match
                else:
                    other_matches.append(match)
            
            other_matches.sort(key=lambda m: m.match_score, reverse=True)
            
            if applied_match:
                note_lines.append(f"APPLIED POSITION:")
                note_lines.append(f"")
                note_lines += self._format_match_note_block(applied_match, job_threshold_map, is_applied=True, show_gaps=True, candidate_id=vetting_log.bullhorn_candidate_id)
                note_lines.append(f"")
                note_lines.append(f"OTHER TOP MATCHES:")
            else:
                note_lines.append(f"TOP ANALYSIS RESULTS:")
            
            for match in other_matches[:5]:
                note_lines.append(f"")
                note_lines += self._format_match_note_block(match, job_threshold_map, show_gaps=True, candidate_id=vetting_log.bullhorn_candidate_id)
        
        note_text = "\n".join(note_lines)
        
        # Create the note
        action = "Scout Screen - Qualified" if vetting_log.is_qualified else "Scout Screen - Not Qualified"
        note_id = bullhorn.create_candidate_note(
            vetting_log.bullhorn_candidate_id,
            note_text,
            action=action
        )
        
        if note_id:
            vetting_log.note_created = True
            vetting_log.bullhorn_note_id = note_id
            db.session.commit()
            logging.info(f"Created vetting note for candidate {vetting_log.bullhorn_candidate_id}")
            return True
        else:
            logging.error(f"Failed to create vetting note for candidate {vetting_log.bullhorn_candidate_id}")
            return False

