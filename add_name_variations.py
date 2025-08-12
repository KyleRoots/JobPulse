#!/usr/bin/env python3
"""
Add name variations for recruiters to handle different name formats in Bullhorn
"""
from app import app, db, RecruiterMapping

def add_name_variations():
    """Add alternate name variations for recruiters"""
    
    variations = [
        # Add variations for existing recruiters
        ("Dan Sifer", "#LI-DS1"),  # Alternative for Daniel Sifer
        ("Christine Carter", "#LI-CC1"),  # Alternative for Chris Carter
        ("Myticas Recruiter", "#LI-RS1"),  # Generic recruiter account
        ("Amanda Messina", "#LI-AM1"),  # Without (Smith)
        ("Sarah Ferris", "#LI-SF1"),  # Without CSP
    ]
    
    with app.app_context():
        added = 0
        for name, tag in variations:
            # Check if this name already exists
            existing = RecruiterMapping.query.filter_by(recruiter_name=name).first()
            if not existing:
                mapping = RecruiterMapping(
                    recruiter_name=name,
                    linkedin_tag=tag
                )
                db.session.add(mapping)
                added += 1
                print(f"Added variation: {name} -> {tag}")
            else:
                print(f"Already exists: {name} -> {existing.linkedin_tag}")
        
        db.session.commit()
        print(f"\n✅ Added {added} name variations")
        
        # Display all mappings
        print("\nAll database mappings:")
        mappings = RecruiterMapping.query.order_by(RecruiterMapping.recruiter_name).all()
        for mapping in mappings:
            print(f"  {mapping.recruiter_name}: {mapping.linkedin_tag}")

if __name__ == "__main__":
    add_name_variations()
