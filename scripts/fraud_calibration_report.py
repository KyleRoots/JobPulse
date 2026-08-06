#!/usr/bin/env python3
"""Print Review/High-Risk fraud assessments for weekly calibration.

Usage (from repo root, with app env loaded):

    .venv/bin/python scripts/fraud_calibration_report.py --days 14

Labels (TP / FP / nudge / ignore) can be set via the Screening Config
calibration API or by updating ``candidate_fraud_assessment.calibration_label``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--unlabeled-only", action="store_true")
    args = parser.parse_args()

    from app import app
    from models import CandidateFraudAssessment

    cutoff = datetime.utcnow() - timedelta(days=max(1, args.days))
    with app.app_context():
        q = (
            CandidateFraudAssessment.query
            .filter(CandidateFraudAssessment.created_at >= cutoff)
            .filter(CandidateFraudAssessment.risk_band.in_(["review", "high_risk"]))
            .order_by(CandidateFraudAssessment.created_at.desc())
        )
        if args.unlabeled_only:
            q = q.filter(CandidateFraudAssessment.calibration_label.is_(None))
        rows = q.limit(200).all()

        print(f"# Fraud calibration sample — last {args.days}d "
              f"({len(rows)} Review/High-Risk rows)\n")
        print("| id | when | cand | name | band | score | signals | label |")
        print("|----|------|------|------|------|-------|---------|-------|")
        for r in rows:
            try:
                sigs = json.loads(r.signals_json or "[]")
            except (TypeError, ValueError):
                sigs = []
            codes = ",".join(
                s.get("code", "?") for s in sigs
                if isinstance(s, dict) and (s.get("points") or 0) > 0
            )[:80]
            when = r.created_at.isoformat(timespec="minutes") if r.created_at else ""
            print(
                f"| {r.id} | {when} | {r.bullhorn_candidate_id} | "
                f"{(r.candidate_name or '')[:30]} | {r.risk_band} | "
                f"{r.risk_score} | {codes} | {r.calibration_label or ''} |"
            )
        print(
            "\nLabel each row: tp | fp | nudge | ignore — then share the sheet "
            "so weights/thresholds can be tuned."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
