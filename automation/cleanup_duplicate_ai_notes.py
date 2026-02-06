#!/usr/bin/env python3
"""
Cleanup Duplicate AI Vetting Notes

This script removes duplicate "AI Vetting - Not Recommended" notes from Bullhorn
candidate records, keeping only the original notes.

The script uses the following logic:
1. Query candidates with recent notes (within N days)
2. For each candidate, find all "AI Vetting - Not Recommended" notes
3. Keep the oldest note, plus any note created 60+ minutes after the previous one
4. Delete (soft-delete) all other duplicate notes

Usage:
    python3 automation/cleanup_duplicate_ai_notes.py --dry-run --max-candidates 100 --days 2
    python3 automation/cleanup_duplicate_ai_notes.py --execute --max-candidates 100 --days 2
"""
import os
import sys
import argparse
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run_cleanup(dry_run: bool = True, max_candidates: int = 100, days: int = 2, candidate_ids: list = None):
    """
    Run the duplicate note cleanup process.
    
    Args:
        dry_run: If True, just report what would be deleted without actually deleting
        max_candidates: Maximum number of candidates to scan
        days: How many days back to look for notes
        candidate_ids: Optional list of specific candidate IDs to check
    
    Returns:
        Summary dictionary with counts and details
    """
    from app import app, db
    from bullhorn_service import BullhornService
    from models import GlobalSettings
    
    with app.app_context():
        print(f"\n{'='*60}")
        print(f"AI VETTING NOTES CLEANUP - {'DRY RUN' if dry_run else 'EXECUTE MODE'}")
        print(f"{'='*60}\n")
        
        # Initialize Bullhorn
        print("🔄 Connecting to Bullhorn...")
        credentials = {}
        for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
            setting = GlobalSettings.query.filter_by(setting_key=key).first()
            if setting and setting.setting_value:
                credentials[key] = setting.setting_value.strip()
        
        bullhorn = BullhornService(
            client_id=credentials.get('bullhorn_client_id'),
            client_secret=credentials.get('bullhorn_client_secret'),
            username=credentials.get('bullhorn_username'),
            password=credentials.get('bullhorn_password')
        )
        
        if not bullhorn.authenticate():
            print("❌ Failed to authenticate with Bullhorn")
            return {'error': 'Authentication failed'}
        
        print("✅ Connected to Bullhorn\n")
        
        # Build the search query
        since_timestamp = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
        
        summary = {
            'candidates_scanned': 0,
            'candidates_with_duplicates': 0,
            'total_notes_found': 0,
            'notes_to_keep': 0,
            'notes_to_delete': 0,
            'notes_deleted': 0,
            'errors': [],
            'candidate_details': []
        }
        
        # If specific candidate IDs provided, query those
        if candidate_ids:
            candidate_list = candidate_ids
        else:
            # Query recently modified candidates
            print(f"🔍 Searching for candidates with notes in the last {days} days...")
            url = f"{bullhorn.base_url}search/Candidate"
            params = {
                'query': f'dateLastModified:[{since_timestamp} TO *]',
                'fields': 'id,firstName,lastName,email',
                'count': max_candidates,
                'sort': '-dateLastModified',
                'BhRestToken': bullhorn.rest_token
            }
            
            response = bullhorn.session.get(url, params=params, timeout=30)
            if response.status_code != 200:
                print(f"❌ Failed to search candidates: {response.status_code}")
                return {'error': f'Search failed: {response.status_code}'}
            
            data = response.json()
            candidates = data.get('data', [])
            candidate_list = [(c['id'], f"{c.get('firstName', '')} {c.get('lastName', '')}".strip(), c.get('email', '')) 
                             for c in candidates]
        
        print(f"📊 Found {len(candidate_list)} candidates to scan\n")
        
        # Process each candidate
        for candidate_info in candidate_list:
            if isinstance(candidate_info, tuple):
                candidate_id, candidate_name, email = candidate_info
            else:
                candidate_id = candidate_info
                candidate_name = "Unknown"
                email = ""
            
            summary['candidates_scanned'] += 1
            
            # Fetch notes for this candidate
            notes_url = f"{bullhorn.base_url}entity/Candidate/{candidate_id}/notes"
            notes_params = {
                'fields': 'id,action,dateAdded,isDeleted',
                'count': 200,
                'BhRestToken': bullhorn.rest_token
            }
            
            try:
                notes_response = bullhorn.session.get(notes_url, params=notes_params, timeout=30)
                if notes_response.status_code != 200:
                    summary['errors'].append(f"Failed to fetch notes for candidate {candidate_id}")
                    continue
                
                notes_data = notes_response.json()
                all_notes = notes_data.get('data', [])
                
                # Filter for AI vetting notes (not already deleted)
                vetting_notes = [
                    n for n in all_notes 
                    if n.get('action') == 'AI Vetting - Not Recommended' 
                    and not n.get('isDeleted', False)
                ]
                
                if not vetting_notes:
                    continue
                
                summary['total_notes_found'] += len(vetting_notes)
                
                # Sort by dateAdded (oldest first)
                vetting_notes.sort(key=lambda x: x.get('dateAdded', 0))
                
                # Identify duplicates using chaining logic
                notes_to_keep = []
                notes_to_delete = []
                
                last_kept_time = None
                for note in vetting_notes:
                    note_time = note.get('dateAdded', 0)
                    if isinstance(note_time, int):
                        note_datetime = datetime.utcfromtimestamp(note_time / 1000)
                    else:
                        continue
                    
                    if last_kept_time is None:
                        # Always keep the first note
                        notes_to_keep.append(note)
                        last_kept_time = note_datetime
                    else:
                        # Check if this note is 60+ minutes after the last kept note
                        time_diff = (note_datetime - last_kept_time).total_seconds() / 60
                        if time_diff >= 60:
                            # New chain - keep this note
                            notes_to_keep.append(note)
                            last_kept_time = note_datetime
                        else:
                            # Duplicate - mark for deletion
                            notes_to_delete.append(note)
                
                if notes_to_delete:
                    summary['candidates_with_duplicates'] += 1
                    summary['notes_to_keep'] += len(notes_to_keep)
                    summary['notes_to_delete'] += len(notes_to_delete)
                    
                    candidate_detail = {
                        'id': candidate_id,
                        'name': candidate_name,
                        'email': email,
                        'notes_to_keep': len(notes_to_keep),
                        'notes_to_delete': len(notes_to_delete)
                    }
                    summary['candidate_details'].append(candidate_detail)
                    
                    print(f"📋 {candidate_name} (ID: {candidate_id}): Keep {len(notes_to_keep)}, Delete {len(notes_to_delete)}")
                    
                    # Delete if not dry run
                    if not dry_run:
                        for note in notes_to_delete:
                            note_id = note.get('id')
                            try:
                                delete_url = f"{bullhorn.base_url}entity/Note/{note_id}"
                                delete_data = {'isDeleted': True}
                                delete_response = bullhorn.session.post(
                                    delete_url, 
                                    json=delete_data,
                                    params={'BhRestToken': bullhorn.rest_token},
                                    timeout=10
                                )
                                if delete_response.status_code == 200:
                                    summary['notes_deleted'] += 1
                                else:
                                    summary['errors'].append(f"Failed to delete note {note_id}: {delete_response.status_code}")
                            except Exception as e:
                                summary['errors'].append(f"Error deleting note {note_id}: {str(e)}")
                
            except Exception as e:
                summary['errors'].append(f"Error processing candidate {candidate_id}: {str(e)}")
        
        # Print summary
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        print(f"Candidates scanned:        {summary['candidates_scanned']}")
        print(f"Candidates with duplicates: {summary['candidates_with_duplicates']}")
        print(f"Total notes found:         {summary['total_notes_found']}")
        print(f"Notes to keep:             {summary['notes_to_keep']}")
        print(f"Notes to delete:           {summary['notes_to_delete']}")
        if not dry_run:
            print(f"Notes actually deleted:    {summary['notes_deleted']}")
        if summary['errors']:
            print(f"\nErrors: {len(summary['errors'])}")
            for error in summary['errors'][:5]:
                print(f"  - {error}")
        print(f"{'='*60}\n")
        
        return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Cleanup duplicate AI vetting notes from Bullhorn')
    parser.add_argument('--dry-run', action='store_true', help='Preview without deleting')
    parser.add_argument('--execute', action='store_true', help='Actually delete the notes')
    parser.add_argument('--days', type=int, default=2, help='Days to look back (default: 2)')
    parser.add_argument('--max-candidates', type=int, default=100, help='Max candidates to scan (default: 100)')
    parser.add_argument('--candidate-ids', type=str, help='Comma-separated candidate IDs to check')
    
    args = parser.parse_args()
    
    # Validate arguments
    if not args.dry_run and not args.execute:
        print("ERROR: Must specify --dry-run or --execute")
        sys.exit(1)
    
    if args.dry_run and args.execute:
        print("ERROR: Cannot specify both --dry-run and --execute")
        sys.exit(1)
    
    candidate_ids = None
    if args.candidate_ids:
        candidate_ids = [int(x.strip()) for x in args.candidate_ids.split(',')]
    
    run_cleanup(
        dry_run=args.dry_run,
        max_candidates=args.max_candidates,
        days=args.days,
        candidate_ids=candidate_ids
    )
