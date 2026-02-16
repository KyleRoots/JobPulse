"""
One-time migration to fix legacy gap arrays in CandidateJobMatch table.
Run ONCE after deploying the normalization fixes.

Usage:
    cd /path/to/JobPulse
    python scripts/fix_legacy_gap_arrays.py

This script finds CandidateJobMatch records where gaps_identified is stored
as a JSON array string (starts with '[') and converts them to clean prose format.
"""
import sys
import os
import json
import logging

# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def fix_legacy_gaps():
    """
    Find and fix CandidateJobMatch records where gaps_identified 
    is stored as a JSON array string (starts with '[').
    """
    from app import create_app
    from models import CandidateJobMatch, db

    app = create_app()
    
    with app.app_context():
        # Query for records with array-format gaps
        matches = CandidateJobMatch.query.filter(
            CandidateJobMatch.gaps_identified.like('[%')
        ).all()
        
        logging.info(f"Found {len(matches)} records with potential array-format gaps")
        
        fixed = 0
        errors = 0
        skipped = 0
        
        for match in matches:
            try:
                gaps_list = json.loads(match.gaps_identified)
                if isinstance(gaps_list, list):
                    match.gaps_identified = ". ".join(str(item) for item in gaps_list)
                    fixed += 1
                    logging.debug(
                        f"Fixed gaps for candidate {match.bullhorn_candidate_id}, "
                        f"job {match.bullhorn_job_id}"
                    )
                else:
                    skipped += 1
            except json.JSONDecodeError as e:
                logging.warning(f"Could not parse gaps for match ID {match.id}: {e}")
                errors += 1
                continue
        
        db.session.commit()
        logging.info(f"Migration complete: {fixed} records fixed, {skipped} skipped, {errors} errors")
        return {'fixed': fixed, 'skipped': skipped, 'errors': errors, 'total': len(matches)}


if __name__ == '__main__':
    results = fix_legacy_gaps()
    print(f"\nMigration Results:")
    print(f"  Total records checked: {results['total']}")
    print(f"  Successfully fixed:    {results['fixed']}")
    print(f"  Skipped (not arrays):  {results['skipped']}")
    print(f"  Errors encountered:    {results['errors']}")
