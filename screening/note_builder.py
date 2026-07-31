from __future__ import annotations
"""
Note Builder - Bullhorn note formatting and creation for screening results.

Contains:
- create_candidate_note: Creates structured notes on candidate records in Bullhorn
- _format_match_note_block: Formats individual job match blocks for notes
- _normalize_gaps_text: Normalizes gaps_identified field to clean prose
"""

import logging
logger = logging.getLogger(__name__)
import json
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Sequence
from app import db
from models import CandidateJobMatch, CandidateVettingLog, JobVettingRequirements
from screening.location_review import is_location_review_match, resolve_match_threshold

# Outcome buckets used by the 6h Bullhorn note duplicate safeguard.
# Same-outcome duplicates stay blocked; outcome flips must supersede so
# recruiter emails and notes cannot diverge after auditor re-vets
# (Femi Oyesanya / 4553046 — Not Qualified note + Qualified email).
_NOTE_OUTCOME_QUALIFIED = 'qualified'
_NOTE_OUTCOME_NOT_QUALIFIED = 'not_qualified'
_NOTE_OUTCOME_LOCATION_REVIEW = 'location_review'
_NOTE_OUTCOME_INCOMPLETE = 'incomplete'

_INCOMPLETE_NOTE_ACTIONS = frozenset({
    'Scout Screen - Incomplete',
    'Scout Screening - Incomplete',
    'AI Vetting - Incomplete',
})

_OUTCOME_LABELS = {
    _NOTE_OUTCOME_QUALIFIED: 'Qualified',
    _NOTE_OUTCOME_NOT_QUALIFIED: 'Not Qualified',
    _NOTE_OUTCOME_LOCATION_REVIEW: 'Location Review',
    _NOTE_OUTCOME_INCOMPLETE: 'Incomplete',
}


def classify_scout_note_action(action: Optional[str]) -> Optional[str]:
    """Map a Bullhorn note action string to a screening outcome bucket."""
    a = (action or '').strip().lower()
    if not a:
        return None
    if 'incomplete' in a:
        return _NOTE_OUTCOME_INCOMPLETE
    if 'location review' in a or 'loc barrier' in a or 'location barrier' in a:
        return _NOTE_OUTCOME_LOCATION_REVIEW
    # Check negative forms before bare "qualified"
    if 'not qualified' in a or 'not recommended' in a:
        return _NOTE_OUTCOME_NOT_QUALIFIED
    if 'qualified' in a:
        return _NOTE_OUTCOME_QUALIFIED
    return None


def intended_scout_note_outcome(
    matches: Sequence,
    *,
    job_threshold_map: Optional[dict] = None,
    global_threshold: float = 80.0,
) -> str:
    """Derive the note outcome this create_candidate_note call would write."""
    if not matches:
        return _NOTE_OUTCOME_INCOMPLETE
    # A full set of genuine 0% fits is still a not-qualified complete result;
    # only treat as Incomplete when summaries show analysis failure.
    if all((m.match_summary or '').startswith('Analysis failed') for m in matches):
        return _NOTE_OUTCOME_INCOMPLETE

    qualified = [m for m in matches if m.is_qualified]
    if qualified:
        return _NOTE_OUTCOME_QUALIFIED

    thresholds = job_threshold_map or {}
    location_review = [
        m for m in matches
        if is_location_review_match(
            m, resolve_match_threshold(m, thresholds, global_threshold)
        )
    ]
    if location_review:
        return _NOTE_OUTCOME_LOCATION_REVIEW
    return _NOTE_OUTCOME_NOT_QUALIFIED


def existing_note_outcomes(existing_notes: Iterable[dict]) -> List[str]:
    """Unique outcome buckets present on recent Scout/AI vetting notes."""
    seen = []
    for note in existing_notes:
        outcome = classify_scout_note_action(note.get('action'))
        if outcome and outcome not in seen:
            seen.append(outcome)
    return seen


def should_supersede_existing_notes(
    existing_notes: Sequence[dict],
    intended_outcome: str,
    *,
    has_match_records: bool,
) -> tuple[bool, str]:
    """Return (allow_write, reason) for the 6h Bullhorn note safeguard.

    Blocks true same-outcome duplicates. Allows write when prior notes are
    incomplete/failed-analysis, or when the intended outcome differs from
    every existing complete outcome (Qualified ↔ Not Qualified, etc.).
    """
    if not existing_notes:
        return True, 'no_existing'

    incomplete_actions = _INCOMPLETE_NOTE_ACTIONS
    all_incomplete = all(
        (n.get('action') or '') in incomplete_actions for n in existing_notes
    )
    all_failed_analysis = all(
        'Analysis failed' in (n.get('comments') or '')
        or 'Match Score: 0%' in (n.get('comments') or '')
        for n in existing_notes
    )
    if (all_incomplete or all_failed_analysis) and has_match_records:
        reason = 'incomplete' if all_incomplete else 'failed_analysis'
        return True, reason

    existing = existing_note_outcomes(existing_notes)
    # Ignore incomplete leftovers when comparing outcome flips against a
    # complete intended result (e.g. Incomplete + Not Qualified → Qualified).
    complete_existing = [o for o in existing if o != _NOTE_OUTCOME_INCOMPLETE]
    if not complete_existing:
        return True, 'only_incomplete_existing'

    if intended_outcome not in complete_existing:
        prior = ', '.join(_OUTCOME_LABELS.get(o, o) for o in complete_existing)
        new = _OUTCOME_LABELS.get(intended_outcome, intended_outcome)
        return True, f'outcome_changed:{prior}->{new}'

    return False, 'same_outcome'


class NoteBuilderMixin:
    """Bullhorn note formatting and creation."""

    @staticmethod
    def _threshold_delta_phrase(original_score, threshold: float) -> str:
        """Human-readable original-score vs threshold wording for note banners."""
        try:
            orig = float(original_score)
            thr = float(threshold)
        except (TypeError, ValueError):
            return ''
        delta = thr - orig
        if abs(delta) < 0.5:
            return f" (exactly at the {thr:.0f}% threshold)"
        if delta > 0:
            return f" (just {delta:.0f} points below the {thr:.0f}% threshold)"
        return f" ({abs(delta):.0f} points above the {thr:.0f}% threshold)"

    @staticmethod
    def _score_change_phrase(original_score, new_score) -> Optional[str]:
        """Return 'Score change: 80% → 57% (−23 pts)' or None if either score missing."""
        try:
            orig = float(original_score)
            new = float(new_score)
        except (TypeError, ValueError):
            return None
        delta_pts = new - orig
        if abs(delta_pts) < 0.05:
            return f"Score change: {orig:.0f}% → {new:.0f}% (unchanged)"
        sign = f"+{delta_pts:.0f}" if delta_pts > 0 else f"{delta_pts:.0f}"
        return f"Score change: {orig:.0f}% → {new:.0f}% ({sign} pts)"

    @staticmethod
    def _top_match_job(matches: Sequence):
        """Return (job_id, job_title) for the highest-scoring match, if any."""
        if not matches:
            return None, None
        ranked = sorted(
            matches,
            key=lambda m: (m.match_score is not None, m.match_score or 0),
            reverse=True,
        )
        top = ranked[0]
        return getattr(top, 'bullhorn_job_id', None), getattr(top, 'job_title', None)

    def _build_revet_banner(
        self,
        candidate_id: int,
        applied_job_id,
        *,
        new_score=None,
        new_best_job_id=None,
        new_best_job_title=None,
    ):
        """Return banner lines explaining the AI Quality Auditor revet, if applicable.

        Fires when the most recent VettingAuditLog row for this (candidate, job)
        has action_taken='revet_triggered' AND revet_new_score IS NULL — meaning
        this screening cycle was queued by the auditor and the audit row hasn't
        been closed out yet (backfill runs AFTER note write, per cycle.py).

        Layout separates historical auditor context from the current recommendation
        so recruiters do not mistake pre-re-screen reasoning for the final call.

        Fully fail-soft: any DB error returns []. Banner never blocks note creation.
        """
        if not candidate_id or not applied_job_id:
            return []
        try:
            from models import VettingAuditLog
            cutoff = datetime.utcnow() - timedelta(days=14)
            row = (
                VettingAuditLog.query
                .filter(
                    VettingAuditLog.bullhorn_candidate_id == int(candidate_id),
                    VettingAuditLog.job_id == int(applied_job_id),
                    VettingAuditLog.action_taken == 'revet_triggered',
                    VettingAuditLog.revet_new_score.is_(None),
                    VettingAuditLog.created_at >= cutoff,
                )
                .order_by(VettingAuditLog.created_at.desc())
                .first()
            )
            if not row:
                return []

            orig = row.original_score
            try:
                from services.vetting_config_service import VettingConfig
                threshold_raw = VettingConfig.get_value('match_threshold')
                threshold = float(threshold_raw) if threshold_raw else 80.0
            except Exception:
                threshold = 80.0

            delta_txt = self._threshold_delta_phrase(orig, threshold)

            finding = (row.audit_finding or '').strip()
            if len(finding) > 300:
                finding = finding[:297].rstrip() + '…'
            if not finding:
                finding = (
                    'The Quality Auditor identified this screen as a borderline call '
                    'with elevated risk of being a false negative.'
                )

            orig_str = f"{float(orig):.0f}%" if orig is not None else 'n/a'
            orig_date = (
                row.created_at.strftime('%Y-%m-%d %H:%M UTC')
                if row.created_at else 'n/a'
            )

            lines = [
                "── WHY A SECOND LOOK HAPPENED (historical) ──",
                "The Scout Quality Auditor flagged this candidate for a second look.",
                "The block below is context from before the re-screen — not the final recommendation.",
                "",
                f"Original screening: {orig_str}{delta_txt}",
                f"Flagged on: {orig_date}",
                f"Historical auditor note (from before re-screen): {finding}",
            ]

            score_change = self._score_change_phrase(orig, new_score)
            if score_change:
                lines.append(score_change)

            orig_job_id = getattr(row, 'job_id', None)
            orig_job_title = (getattr(row, 'job_title', None) or '').strip()
            if orig_job_id:
                title_bit = f" — {orig_job_title}" if orig_job_title else ""
                lines.append(f"Best job on original screen: #{int(orig_job_id)}{title_bit}")

            try:
                new_jid = int(new_best_job_id) if new_best_job_id is not None else None
            except (TypeError, ValueError):
                new_jid = None
            try:
                orig_jid = int(orig_job_id) if orig_job_id is not None else None
            except (TypeError, ValueError):
                orig_jid = None

            if new_jid is not None and new_jid != orig_jid:
                new_title = (new_best_job_title or '').strip()
                title_bit = f" — {new_title}" if new_title else ""
                lines.append(f"Best job on re-screen: #{new_jid}{title_bit}")

            lines.extend([
                "",
                "── CURRENT SCOUT RECOMMENDATION ──",
                "",
            ])
            return lines
        except Exception as e:
            logger.warning(f"_build_revet_banner: failed for candidate {candidate_id}: {e!r}")
            return []

    def _format_match_note_block(self, match, job_threshold_map, is_applied=False, show_gaps=False, candidate_id=None, brief=False):
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

        if match.prestige_boost_applied and match.prestige_employer:
            score_text += f"  (includes +5 prestige boost)"

        if match_custom:
            score_text += f"  |  Threshold: {match_custom:.0f}% (custom)"
        lines.append(score_text)

        if match.prestige_employer:
            lines.append(f"  🏢 Currently at Tier-1 firm: {match.prestige_employer}")

        if is_applied:
            lines.append(f"  ⭐ APPLIED TO THIS POSITION")

        # Compact trailing related matches: score + one-line gap only (no full
        # Summary/Skills dossier). Top related matches keep the full block.
        if brief:
            if show_gaps and match.gaps_identified:
                gaps_text = self._normalize_gaps_text(match.gaps_identified, candidate_id)
                if len(gaps_text) > 220:
                    gaps_text = gaps_text[:217].rsplit(' ', 1)[0] + '…'
                lines.append(f"  Gaps: {gaps_text}")
            elif match.match_summary:
                summary = (match.match_summary or '').strip()
                if len(summary) > 160:
                    summary = summary[:157].rsplit(' ', 1)[0] + '…'
                lines.append(f"  Summary: {summary}")
            return lines

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
            logger.warning(f"Render-time array normalization for candidate {candidate_id}")
            return ". ".join(str(item) for item in gaps)
        
        if isinstance(gaps, str) and gaps.startswith('['):
            try:
                gaps_list = json.loads(gaps)
                if isinstance(gaps_list, list):
                    logger.warning(f"Render-time JSON string normalization for candidate {candidate_id}")
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
            logger.info(f"⏭️ Note already exists for vetting log {vetting_log.id} (candidate {vetting_log.bullhorn_candidate_id}), skipping creation")
            return True  # Return True to indicate note exists
        
        bullhorn = self._get_bullhorn_service()
        if not bullhorn:
            return False
        
        # PRE-CREATION SAFEGUARD: Check Bullhorn for existing AI vetting notes (6h window)
        # This prevents duplicate same-outcome notes even if upstream dedup has a bug.
        # Incomplete/failed notes never block a new complete result. Outcome flips
        # (Qualified ↔ Not Qualified / Location Review) also supersede so re-vets
        # cannot leave recruiters with an email that contradicts the note.
        from datetime import timedelta
        outcome_supersession_banner: List[str] = []
        try:
            # Need matches before the safeguard so we know the intended outcome.
            matches_for_outcome = CandidateJobMatch.query.filter_by(
                vetting_log_id=vetting_log.id
            ).order_by(CandidateJobMatch.match_score.desc()).all()
            global_threshold_preview = self.get_threshold()
            job_ids_preview = [
                m.bullhorn_job_id for m in matches_for_outcome if m.bullhorn_job_id
            ]
            job_threshold_map_preview = {}
            if job_ids_preview:
                try:
                    custom_reqs = JobVettingRequirements.query.filter(
                        JobVettingRequirements.bullhorn_job_id.in_(job_ids_preview),
                        JobVettingRequirements.vetting_threshold.isnot(None),
                    ).all()
                    for req in custom_reqs:
                        job_threshold_map_preview[req.bullhorn_job_id] = float(
                            req.vetting_threshold
                        )
                except Exception:
                    job_threshold_map_preview = {}

            intended_outcome = intended_scout_note_outcome(
                matches_for_outcome,
                job_threshold_map=job_threshold_map_preview,
                global_threshold=global_threshold_preview,
            )

            existing_notes = bullhorn.get_candidate_notes(
                vetting_log.bullhorn_candidate_id,
                action_filter=[
                    "Scout Screen - Qualified",
                    "Scout Screen - Not Qualified",
                    "Scout Screen - Incomplete",
                    "Scout Screen - Loc Barrier",
                    "Scout Screen - Location Barrier",
                    "Scout Screen - Location Review",
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
                allow_write, reason = should_supersede_existing_notes(
                    existing_notes,
                    intended_outcome,
                    has_match_records=bool(matches_for_outcome),
                )
                if allow_write:
                    logger.info(
                        f"ℹ️ DUPLICATE SAFEGUARD OVERRIDE: Candidate "
                        f"{vetting_log.bullhorn_candidate_id} has "
                        f"{len(existing_notes)} Scout note(s) in Bullhorn from last 6h. "
                        f"Allowing write ({reason}). "
                        f"event=note_dedupe_supersede intended={intended_outcome}"
                    )
                    if reason.startswith('outcome_changed:'):
                        change = reason.split(':', 1)[1]
                        outcome_supersession_banner = [
                            "⚠️ UPDATED SCOUT SCREENING RESULT",
                            f"This note replaces an earlier Scout note from the last 6 hours "
                            f"because the outcome changed ({change}).",
                            "Use this note (and any recruiter email from this re-screen) as the "
                            "current Scout recommendation.",
                            "",
                        ]
                else:
                    try:
                        from screening import note_builder as _nb_mod
                        if not hasattr(_nb_mod, '_DEDUPE_REJECTION_COUNTER'):
                            _nb_mod._DEDUPE_REJECTION_COUNTER = 0
                        _nb_mod._DEDUPE_REJECTION_COUNTER += 1
                        _counter_val = _nb_mod._DEDUPE_REJECTION_COUNTER
                    except Exception:
                        _counter_val = -1
                    _existing_actions = sorted({
                        (n.get('action') or 'unknown') for n in existing_notes
                    })
                    logger.warning(
                        f"⚠️ DUPLICATE SAFEGUARD: Candidate {vetting_log.bullhorn_candidate_id} already has "
                        f"{len(existing_notes)} AI vetting note(s) in Bullhorn from last 6h. "
                        f"Skipping duplicate note creation (same outcome={intended_outcome}). "
                        f"event=note_dedupe_blocked counter={_counter_val} "
                        f"vetting_log_id={vetting_log.id} candidate_id={vetting_log.bullhorn_candidate_id} "
                        f"existing_actions={_existing_actions}"
                    )
                    vetting_log.note_created = True
                    vetting_log.bullhorn_note_id = existing_notes[0].get('id')
                    db.session.commit()
                    return True
        except Exception as e:
            # Don't block note creation if the safety check itself fails
            logger.warning(f"Pre-note duplicate check failed (proceeding with creation): {str(e)}")

        def _compose_note_text(lines: List[str]) -> str:
            return "\n".join(list(outcome_supersession_banner) + lines)
        
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
                logger.warning(f"Could not fetch per-job thresholds for note: {str(e)}")
        
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
            note_text = _compose_note_text(note_lines)
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
                logger.info(f"Created incomplete vetting note for candidate {vetting_log.bullhorn_candidate_id}")
                return True
            else:
                logger.error(f"Failed to create incomplete vetting note for candidate {vetting_log.bullhorn_candidate_id}")
                return False
        
        # ── LOCATION REVIEW DETECTION ──
        # Candidates who are technically at or above threshold but were knocked
        # below it by either (a) a small location penalty (≤ 15 pts) or
        # (b) a hard AI-flagged location barrier on an on-site/hybrid role.
        # In both cases the technical fit is real and the recruiter should make
        # the judgment call rather than the system silently rejecting them.
        # Use per-job threshold (matches the per-job qualification logic
        # in candidate_vetting_service.py); falls back to the global threshold
        # for jobs without a custom override.
        location_review_matches = [
            m for m in matches
            if is_location_review_match(
                m, resolve_match_threshold(m, job_threshold_map, threshold)
            )
        ]
        is_location_review_candidate = (
            len(qualified_matches) == 0 and len(location_review_matches) > 0
        )

        if is_location_review_candidate:
            # Location-review note: tech-fit-qualified candidate flagged for recruiter judgment
            top_lr = sorted(
                location_review_matches,
                key=lambda m: (m.technical_score or m.match_score or 0),
                reverse=True,
            )
            top_tech = (top_lr[0].technical_score or top_lr[0].match_score) if top_lr else 0
            top_final = top_lr[0].match_score if top_lr else 0
            # Use per-job threshold of the top match for the header summary
            # (avoids stating a global threshold that may not apply to this
            # candidate's actual matched position).
            top_match_threshold = resolve_match_threshold(top_lr[0], job_threshold_map, threshold) if top_lr else threshold
            _lr_applied = next((m for m in location_review_matches if getattr(m, 'is_applied_job', False)), None)
            _lr_job_id = (
                getattr(vetting_log, 'applied_job_id', None)
                or (_lr_applied.bullhorn_job_id if _lr_applied else None)
                or (top_lr[0].bullhorn_job_id if top_lr else None)
            )
            _new_best_id, _new_best_title = self._top_match_job(matches)
            _revet_banner_lines = self._build_revet_banner(
                vetting_log.bullhorn_candidate_id,
                _lr_job_id,
                new_score=getattr(vetting_log, 'highest_match_score', None),
                new_best_job_id=_new_best_id,
                new_best_job_title=_new_best_title,
            )
            note_lines = list(_revet_banner_lines) + [
                f"📍 SCOUT SCREENING - LOCATION REVIEW REQUIRED",
                f"",
                f"Analysis Date: {vetting_log.analyzed_at.strftime('%Y-%m-%d %H:%M UTC') if vetting_log.analyzed_at else 'N/A'}",
                f"Match Threshold: {top_match_threshold:.0f}% (see per-position thresholds below)",
                f"Technical Fit: {top_tech:.0f}% (skills & experience, before location penalty)",
                f"Final Score: {top_final:.0f}% (after location penalty)",
                f"",
                f"This candidate's technical fit meets or exceeds the configured match",
                f"threshold for one or more positions below. A location penalty brought",
                f"the final score below threshold. The candidate is being surfaced for",
                f"recruiter judgment rather than auto-rejected — please review commute,",
                f"relocation, or hybrid logistics before deciding.",
                f"",
                f"POSITION(S) AFFECTED:",
            ]
            for m in top_lr:
                tech = m.technical_score or m.match_score
                match_custom = job_threshold_map.get(m.bullhorn_job_id)
                if tech and tech != m.match_score:
                    score_line = f"  Technical Fit: {tech:.0f}% → Location Penalty → Final: {m.match_score:.0f}%"
                else:
                    score_line = f"  Score: {m.match_score:.0f}%"
                if match_custom:
                    score_line += f"  |  Threshold: {match_custom:.0f}% (custom)"
                gaps_full = m.gaps_identified or ''
                loc_gap_parts = [
                    part.strip() for part in gaps_full.replace(' | ', '|').split('|')
                    if 'location' in part.lower()
                ]
                non_loc_parts = [
                    part.strip() for part in gaps_full.replace(' | ', '|').split('|')
                    if 'location' not in part.lower() and part.strip()
                ]
                loc_gap_text = ' | '.join(loc_gap_parts) if loc_gap_parts else ''
                note_lines += [
                    f"",
                    f"• Job ID: {m.bullhorn_job_id} - {m.job_title}",
                    score_line,
                    f"  ⚠️  LOCATION REVIEW",
                    f"  Summary: {m.match_summary}",
                    f"  Skills: {m.skills_match}",
                ]
                if non_loc_parts:
                    note_lines.append(f"  Other Gaps: {' | '.join(non_loc_parts)}")
                if loc_gap_text:
                    note_lines.append(f"  Location: {loc_gap_text}")
            note_text = _compose_note_text(note_lines)
            action = "Scout Screen - Location Review"

            note_id = bullhorn.create_candidate_note(
                vetting_log.bullhorn_candidate_id,
                note_text,
                action=action
            )
            if note_id:
                vetting_log.note_created = True
                vetting_log.bullhorn_note_id = note_id
                db.session.commit()
                logger.info(
                    f"📍 Created location review note for candidate {vetting_log.bullhorn_candidate_id} "
                    f"(tech fit: {top_tech:.0f}%, final: {top_final:.0f}%)"
                )
                return True
            else:
                logger.error(f"Failed to create location review note for candidate {vetting_log.bullhorn_candidate_id}")
                return False

        elif vetting_log.is_qualified:
            # Qualified candidate note
            #
            # Recruiter-transparency fix (May 2026): The applied-job match must
            # be searched in ALL matches, not just qualified_matches. Otherwise
            # candidates like Lei Gao (3808669) — who applied to one job but
            # qualified only for related roles — produce notes that never
            # mention the role they actually applied to. Recruiters following
            # up have no idea where to start the conversation.
            applied_match = None
            applied_match_qualified = False
            for match in matches:
                if match.is_applied_job:
                    applied_match = match
                    applied_match_qualified = bool(match.is_qualified)
                    break
            other_qualified = [m for m in qualified_matches if not m.is_applied_job]

            _new_best_id, _new_best_title = self._top_match_job(matches)
            _revet_banner_lines = self._build_revet_banner(
                vetting_log.bullhorn_candidate_id,
                getattr(vetting_log, 'applied_job_id', None)
                    or (applied_match.bullhorn_job_id if applied_match else None),
                new_score=getattr(vetting_log, 'highest_match_score', None),
                new_best_job_id=_new_best_id,
                new_best_job_title=_new_best_title,
            )

            note_lines = list(_revet_banner_lines) + [
                f"🎯 SCOUT SCREENING - QUALIFIED CANDIDATE",
                f"",
                f"Analysis Date: {vetting_log.analyzed_at.strftime('%Y-%m-%d %H:%M UTC') if vetting_log.analyzed_at else 'N/A'}",
                f"Threshold: {threshold}%",
                f"Qualified Matches: {len(qualified_matches)} of {len(matches)} jobs",
                f"Highest Match Score: {vetting_log.highest_match_score:.0f}%",
                f"",
            ]
            
            other_qualified.sort(key=lambda m: m.match_score, reverse=True)
            
            if applied_match and applied_match_qualified:
                # Existing happy path: candidate qualified for the role they applied to
                note_lines.append(f"APPLIED POSITION (QUALIFIED):")
                note_lines.append(f"")
                note_lines += self._format_match_note_block(applied_match, job_threshold_map, is_applied=True)
                if other_qualified:
                    note_lines.append(f"")
                    note_lines.append(f"OTHER QUALIFIED POSITIONS:")
                for match in other_qualified:
                    note_lines.append(f"")
                    note_lines += self._format_match_note_block(match, job_threshold_map)
            elif applied_match and not applied_match_qualified:
                # Recruiter-transparency case: qualified for related roles only
                # Render qualified roles FIRST (most actionable), then a compact
                # applied-job context block at the end so recruiters know how to
                # frame the outreach call.
                note_lines.append(f"QUALIFIED POSITIONS (RELATED ROLES):")
                for match in other_qualified:
                    note_lines.append(f"")
                    note_lines += self._format_match_note_block(match, job_threshold_map)
                note_lines.append(f"")
                _ctx_summary_raw = (applied_match.match_summary or '').strip()
                _ctx_summary = (
                    _ctx_summary_raw if len(_ctx_summary_raw) <= 220
                    else _ctx_summary_raw[:217].rsplit(' ', 1)[0] + '…'
                )
                note_lines += [
                    f"📥 JOB ORIGINALLY APPLIED TO (BELOW THRESHOLD):",
                    f"",
                    f"• Job ID: {applied_match.bullhorn_job_id} - {applied_match.job_title}",
                    f"  Match Score: {(applied_match.match_score or 0):.0f}% (did not meet qualifying threshold)",
                    f"  Note: Candidate is being recommended for the related role(s) above, not this one.",
                ]
                if _ctx_summary:
                    note_lines.append(f"  Summary: {_ctx_summary}")
            else:
                # No applied-job record at all (e.g. inbound email scrape with no app)
                note_lines.append(f"QUALIFIED POSITIONS:")
                for match in other_qualified:
                    note_lines.append(f"")
                    note_lines += self._format_match_note_block(match, job_threshold_map)
        else:
            # Not qualified note
            applied_match = None
            other_matches = []
            for match in matches:
                if match.is_applied_job:
                    applied_match = match
                else:
                    other_matches.append(match)

            _new_best_id, _new_best_title = self._top_match_job(matches)
            _revet_banner_lines = self._build_revet_banner(
                vetting_log.bullhorn_candidate_id,
                getattr(vetting_log, 'applied_job_id', None)
                    or (applied_match.bullhorn_job_id if applied_match else None),
                new_score=getattr(vetting_log, 'highest_match_score', None),
                new_best_job_id=_new_best_id,
                new_best_job_title=_new_best_title,
            )

            note_lines = list(_revet_banner_lines) + [
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
            
            other_matches.sort(key=lambda m: m.match_score, reverse=True)
            
            if applied_match:
                note_lines.append(f"APPLIED POSITION:")
                note_lines.append(f"")
                note_lines += self._format_match_note_block(applied_match, job_threshold_map, is_applied=True, show_gaps=True, candidate_id=vetting_log.bullhorn_candidate_id)
                # Only show this section when association logic actually scored
                # related roles — an empty heading is noise for single-job screens.
                if other_matches:
                    note_lines.append(f"")
                    note_lines.append(f"OTHER TOP MATCHES:")
            else:
                # Safety net: we know which job they applied to, but it never
                # landed in CandidateJobMatch (historically: half-closed applied
                # jobs skipped injection). Surfacing the ID prevents the note
                # from looking like Scout screened the wrong role.
                applied_job_id = getattr(vetting_log, 'applied_job_id', None)
                applied_job_title = (
                    getattr(vetting_log, 'applied_job_title', None) or ''
                ).strip()
                if applied_job_id:
                    title_bit = f" - {applied_job_title}" if applied_job_title else ""
                    note_lines += [
                        f"📥 JOB ORIGINALLY APPLIED TO (NOT SCORED):",
                        f"",
                        f"• Job ID: {applied_job_id}{title_bit}",
                        f"  Note: Scout could not score this job at analysis time "
                        f"(job unavailable/ineligible for scoring). Related matches below.",
                        f"",
                        f"TOP ANALYSIS RESULTS (RELATED ROLES):",
                    ]
                else:
                    note_lines.append(f"TOP ANALYSIS RESULTS:")
            
            for idx, match in enumerate(other_matches[:5]):
                note_lines.append(f"")
                # Full write-up for top 2 related scores; thin blocks after that.
                note_lines += self._format_match_note_block(
                    match, job_threshold_map, show_gaps=True,
                    candidate_id=vetting_log.bullhorn_candidate_id,
                    brief=(idx >= 2),
                )
        
        note_text = _compose_note_text(note_lines)
        
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
            logger.info(f"Created vetting note for candidate {vetting_log.bullhorn_candidate_id}")
            return True
        else:
            logger.error(f"Failed to create vetting note for candidate {vetting_log.bullhorn_candidate_id}")
            return False

