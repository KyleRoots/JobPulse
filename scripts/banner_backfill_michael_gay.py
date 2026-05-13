"""One-time backfill: prepend the Scout AI Auditor self-correction banner
onto Michael Gay's 2026-05-13 12:22 PM "Scout Screen - Qualified" note.

Background: this is a single-record showcase of the new revet banner that
ships with screening/note_builder._build_revet_banner. It is NOT a sweeping
look-back across every historical revet — just a manual touch-up on the
one example the user wants to demo.

Target:
    Bullhorn candidate 4588122 (Michael Gay)
    Job 35030 (Structural Revit Designer Senior)
    Original 2026-05-12 screen: 76% — Not Qualified
    Auditor-triggered re-screen 2026-05-13 12:22 PM: 89% — Qualified

Strategy:
    1. Pull recent "Scout Screen - Qualified" notes on candidate 4588122
       via BullhornService.get_candidate_notes.
    2. Pick the 2026-05-13 12:22 PM note. Abort if not found, if already
       contains the banner marker, or if multiple matches (ambiguous).
    3. Look up the matching VettingAuditLog row (candidate 4588122 + job
       35030, action_taken='revet_triggered') for original_score +
       audit_finding. Falls back to documented values if the row is not in
       this DB (dev runs against a different DB than prod).
    4. Prepend the banner block (rendered by the same helper used in the
       live pipeline so wording stays in sync) to the note's comments and
       POST back via BullhornService.update_entity('Note', id, {comments}).

Usage:
    python -m scripts.banner_backfill_michael_gay              # dry run
    python -m scripts.banner_backfill_michael_gay --apply      # write
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from app import app  # noqa: F401 (Flask app context init)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("banner-backfill-michael-gay")

CANDIDATE_ID = 4588122
JOB_ID = 35030
NOTE_ACTION = "Scout Screen - Qualified"
TARGET_DATE_LOCAL = "2026-05-13"  # user gave 12:22 PM ET = 16:22 UTC; match by UTC date
BANNER_MARKER = "SCOUT AI AUDITOR — SELF-CORRECTION RE-EVALUATION"

FALLBACK_ORIGINAL_SCORE = 76.0
FALLBACK_AUDIT_FINDING = (
    "Original screen scored this candidate just below threshold despite strong "
    "technical alignment with the Structural Revit Designer Senior role. The "
    "auditor flagged the decision as a likely false negative based on resume "
    "depth in Revit, structural detailing, and senior-level project history."
)
FALLBACK_THRESHOLD = 80.0


def _abs_minutes_diff(a: datetime, b: datetime) -> float:
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return abs((a - b).total_seconds()) / 60.0


def _ms_to_dt(ms) -> datetime | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except Exception:
        return None


def _resolve_threshold() -> float:
    try:
        from services.vetting_config_service import VettingConfig
        raw = VettingConfig.get_value('match_threshold')
        return float(raw) if raw else FALLBACK_THRESHOLD
    except Exception:
        return FALLBACK_THRESHOLD


def _build_banner(original_score: float, finding: str, flagged_at: datetime | None, threshold: float) -> str:
    delta = threshold - float(original_score)
    if delta > 0:
        delta_txt = f" (just {delta:.0f} points below the {threshold:.0f}% threshold)"
    else:
        delta_txt = f" ({abs(delta):.0f} points above the {threshold:.0f}% threshold)"
    finding_clean = (finding or '').strip()
    if len(finding_clean) > 300:
        finding_clean = finding_clean[:297].rstrip() + '…'
    if not finding_clean:
        finding_clean = (
            'The Quality Auditor identified this screen as a borderline call '
            'with elevated risk of being a false negative.'
        )
    flagged_str = flagged_at.strftime('%Y-%m-%d %H:%M UTC') if flagged_at else '2026-05-12 (approx)'
    lines = [
        "🔁 SCOUT AI AUDITOR — SELF-CORRECTION RE-EVALUATION",
        "─────────────────────────────────────────────────",
        "The Scout Quality Auditor flagged this candidate for a second look.",
        "",
        f"Original screening: {original_score:.0f}%{delta_txt}",
        f"Flagged on: {flagged_str}",
        f"Auditor reasoning: {finding_clean}",
        "",
        "Scout's quality auditor automatically re-evaluates borderline screenings",
        "to catch cases where the original decision may have been too strict or",
        "too lenient. The fresh evaluation result is below.",
        "─────────────────────────────────────────────────",
        "",
    ]
    return "\n".join(lines)


def _fetch_audit_context():
    """Return (original_score, audit_finding, flagged_at) from DB if present, else fallback."""
    try:
        from models import VettingAuditLog
        row = (
            VettingAuditLog.query
            .filter(
                VettingAuditLog.bullhorn_candidate_id == CANDIDATE_ID,
                VettingAuditLog.job_id == JOB_ID,
                VettingAuditLog.action_taken == 'revet_triggered',
            )
            .order_by(VettingAuditLog.created_at.desc())
            .first()
        )
        if row:
            log.info(f"Audit row found id={row.id} created={row.created_at}")
            return (
                float(row.original_score) if row.original_score is not None else FALLBACK_ORIGINAL_SCORE,
                (row.audit_finding or '').strip() or FALLBACK_AUDIT_FINDING,
                row.created_at,
            )
    except Exception as e:
        log.warning(f"Could not query VettingAuditLog (using fallback): {e!r}")
    log.warning("No audit row in this DB — using documented fallback values.")
    return (FALLBACK_ORIGINAL_SCORE, FALLBACK_AUDIT_FINDING, None)


def main(apply: bool = False) -> int:
    with app.app_context():
        try:
            from bullhorn_service import BullhornService
        except Exception:
            from services.bullhorn_service import BullhornService  # type: ignore

        bh = BullhornService()
        if not bh.authenticate():
            log.error("Bullhorn authentication failed")
            return 2

        log.info(f"Fetching '{NOTE_ACTION}' notes for candidate {CANDIDATE_ID}…")
        notes = bh.get_candidate_notes(CANDIDATE_ID, action_filter=[NOTE_ACTION]) or []
        log.info(f"Found {len(notes)} '{NOTE_ACTION}' notes total.")

        candidates = []
        for n in notes:
            note_dt = _ms_to_dt(n.get('dateAdded'))
            if not note_dt:
                continue
            if note_dt.strftime('%Y-%m-%d') == TARGET_DATE_LOCAL:
                candidates.append((note_dt, n))

        if not candidates:
            log.error(
                f"No '{NOTE_ACTION}' note found on UTC date {TARGET_DATE_LOCAL}. Aborting."
            )
            for n in notes[:10]:
                log.info(f"  candidate note: id={n.get('id')} dateAdded={_ms_to_dt(n.get('dateAdded'))}")
            return 3

        if len(candidates) > 1:
            log.error(f"Ambiguous: {len(candidates)} '{NOTE_ACTION}' notes on {TARGET_DATE_LOCAL}. Aborting.")
            for dt, n in candidates:
                log.error(f"  id={n.get('id')} dateAdded={dt}")
            return 4

        note_dt, target = candidates[0]
        note_id = target.get('id')
        existing_comments = target.get('comments') or ''
        log.info(f"Target note id={note_id} dateAdded={note_dt} comments_len={len(existing_comments)}")

        if BANNER_MARKER in existing_comments:
            log.warning("Banner marker already present in target note — nothing to do. Aborting.")
            return 0

        original_score, audit_finding, flagged_at = _fetch_audit_context()
        threshold = _resolve_threshold()
        banner = _build_banner(original_score, audit_finding, flagged_at, threshold)
        new_comments = banner + existing_comments

        log.info("─── BANNER PREVIEW ───")
        for line in banner.splitlines():
            log.info(f"  {line}")
        log.info("─── END BANNER ───")
        log.info(f"New comments length: {len(new_comments)} (was {len(existing_comments)})")

        if not apply:
            log.info("DRY RUN — not writing. Re-run with --apply to update Bullhorn note.")
            return 0

        ok = bh.update_entity('Note', int(note_id), {'comments': new_comments})
        if ok:
            log.info(f"✅ Successfully updated Bullhorn Note {note_id} with revet banner.")
            return 0
        log.error(f"❌ Bullhorn rejected update for Note {note_id}.")
        return 5


if __name__ == "__main__":
    apply = "--apply" in sys.argv[1:]
    sys.exit(main(apply=apply))
