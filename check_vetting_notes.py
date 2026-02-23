#!/usr/bin/env python3
"""Quick script to check vetting notes for specific candidates via Bullhorn API."""

import os
import sys
import json
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def check_candidates():
    from app import app
    
    with app.app_context():
        from models import CandidateVettingLog, db
        from bullhorn_service import BullhornService
        
        # Candidate IDs from the user's screenshot
        candidate_ids = [4587977, 4587976, 4587975, 4587974, 4587973, 4587972, 
                        4587971, 4587970, 4587969, 4587968, 4587967, 4587966,
                        4587965, 4587964, 4587963, 4587962, 4587961, 4587960]
        
        print("=" * 100)
        print("VETTING NOTES AUDIT")
        print("=" * 100)
        
        # Step 1: Check local vetting logs
        print("\n📊 STEP 1: Local Vetting Logs (CandidateVettingLog table)")
        print("-" * 100)
        print(f"{'ID':<12} {'Status':<15} {'Qualified':<12} {'Note Created':<15} {'Score':<8} {'Analyzed At':<25} {'Error'}")
        print("-" * 100)
        
        for cid in candidate_ids:
            logs = CandidateVettingLog.query.filter_by(
                bullhorn_candidate_id=cid
            ).order_by(CandidateVettingLog.created_at.desc()).all()
            
            if not logs:
                print(f"{cid:<12} {'NO LOG':<15} {'-':<12} {'-':<15} {'-':<8} {'-':<25} -")
            else:
                for log in logs[:2]:  # Show up to 2 most recent
                    score = f"{log.highest_match_score:.0f}%" if log.highest_match_score else "-"
                    analyzed = log.analyzed_at.strftime('%Y-%m-%d %H:%M') if log.analyzed_at else '-'
                    error = (log.error_message[:40] + '...') if log.error_message and len(log.error_message) > 40 else (log.error_message or '-')
                    print(f"{cid:<12} {log.status:<15} {str(log.is_qualified):<12} {str(log.note_created):<15} {score:<8} {analyzed:<25} {error}")
        
        # Step 2: Check the 6 "Awaiting Vetting" candidates
        awaiting_ids = [4587221, 4586862, 4586798, 4586967, 4586956, 4586865]
        print(f"\n\n📊 STEP 2: 'Awaiting Vetting' Candidates (stuck ones)")
        print("-" * 100)
        print(f"{'ID':<12} {'Status':<15} {'Qualified':<12} {'Note Created':<15} {'Score':<8} {'Analyzed At':<25}")
        print("-" * 100)
        for cid in awaiting_ids:
            logs = CandidateVettingLog.query.filter_by(
                bullhorn_candidate_id=cid
            ).order_by(CandidateVettingLog.created_at.desc()).all()
            if not logs:
                print(f"{cid:<12} {'NO LOG':<15} {'-':<12} {'-':<15} {'-':<8} {'-':<25}")
            else:
                for log in logs[:1]:
                    score = f"{log.highest_match_score:.0f}%" if log.highest_match_score else "-"
                    analyzed = log.analyzed_at.strftime('%Y-%m-%d %H:%M') if log.analyzed_at else '-'
                    print(f"{cid:<12} {log.status:<15} {str(log.is_qualified):<12} {str(log.note_created):<15} {score:<8} {analyzed:<25}")
        
        # Step 3: Try to check Bullhorn notes for a few key candidates
        print(f"\n\n📊 STEP 3: Bullhorn API Note Check")
        print("-" * 100)
        
        try:
            bullhorn = BullhornService()
            if bullhorn.authenticate():
                check_ids = [4587977, 4587975, 4587967, 4587960]  # Mix of recommended and not
                for cid in check_ids:
                    try:
                        notes = bullhorn.get_candidate_notes(
                            cid,
                            action_filter=[
                                "Scout Screening - Qualified",
                                "Scout Screening - Not Recommended",
                                "Scout Screening - Incomplete",
                                "AI Vetting - Qualified",
                                "AI Vetting - Not Recommended",
                                "AI Vetting - Incomplete"
                            ]
                        )
                        if notes:
                            print(f"  ✅ Candidate {cid}: {len(notes)} vetting note(s) found in Bullhorn")
                            for note in notes[:2]:
                                action = note.get('action', 'N/A')
                                date_added = note.get('dateAdded')
                                if date_added and isinstance(date_added, (int, float)):
                                    date_str = datetime.fromtimestamp(date_added/1000).strftime('%Y-%m-%d %H:%M')
                                else:
                                    date_str = str(date_added)[:25] if date_added else 'N/A'
                                print(f"      Note action: '{action}', date: {date_str}")
                        else:
                            print(f"  ❌ Candidate {cid}: NO vetting notes found in Bullhorn")
                    except Exception as e:
                        print(f"  ⚠️  Candidate {cid}: Error checking notes: {str(e)}")
            else:
                print("  ❌ Failed to authenticate with Bullhorn")
        except Exception as e:
            print(f"  ❌ Bullhorn service error: {str(e)}")
        
        # Step 4: Check VettingConfig settings
        from models import VettingConfig
        print(f"\n\n📊 STEP 4: VettingConfig Settings")
        print("-" * 60)
        configs = VettingConfig.query.all()
        for c in configs:
            print(f"  {c.setting_key}: {c.setting_value}")
        
        print("\n" + "=" * 100)
        print("AUDIT COMPLETE")
        print("=" * 100)


if __name__ == '__main__':
    check_candidates()
