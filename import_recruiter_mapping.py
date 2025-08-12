#!/usr/bin/env python3
"""
Import recruiter mapping data from CSV file into database
"""
import csv
from app import app, db, RecruiterMapping

def import_recruiter_mapping():
    """Import recruiter mapping from CSV file"""
    csv_file = 'attached_assets/seat-report_1755023851096.csv'
    
    with app.app_context():
        # Clear existing mappings
        RecruiterMapping.query.delete()
        db.session.commit()
        print("Cleared existing recruiter mappings")
        
        # Import new mappings
        imported = 0
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2:
                    recruiter_name = row[0].strip()
                    linkedin_tag = row[1].strip()
                    
                    if recruiter_name and linkedin_tag:
                        mapping = RecruiterMapping(
                            recruiter_name=recruiter_name,
                            linkedin_tag=linkedin_tag
                        )
                        db.session.add(mapping)
                        imported += 1
                        print(f"Added: {recruiter_name} -> {linkedin_tag}")
        
        db.session.commit()
        print(f"\n✅ Successfully imported {imported} recruiter mappings")
        
        # Display all mappings
        print("\nCurrent database mappings:")
        mappings = RecruiterMapping.query.order_by(RecruiterMapping.recruiter_name).all()
        for mapping in mappings:
            print(f"  {mapping.recruiter_name}: {mapping.linkedin_tag}")

if __name__ == "__main__":
    import_recruiter_mapping()