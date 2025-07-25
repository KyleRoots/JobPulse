#!/usr/bin/env python3
"""
Script to update job categories from a new Excel file
Run this whenever you need to update the job function, industry, or seniority level lists
"""

import sys
import pandas as pd
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def update_categories_from_excel(excel_file_path):
    """Update job categories from a new Excel file"""
    
    try:
        logger.info(f"Reading Excel file: {excel_file_path}")
        
        # Read the Excel file
        xl_file = pd.ExcelFile(excel_file_path)
        
        # Extract job functions
        job_functions_df = pd.read_excel(xl_file, sheet_name='Job Functions')
        job_functions = []
        for col in job_functions_df.columns:
            values = job_functions_df[col].dropna().tolist()
            job_functions.extend([str(v).strip() for v in values if str(v).strip() and str(v).strip() != 'nan'])
        
        # Extract job industries  
        job_industries_df = pd.read_excel(xl_file, sheet_name='Job Industries')
        job_industries = []
        for col in job_industries_df.columns:
            values = job_industries_df[col].dropna().tolist()
            job_industries.extend([str(v).strip() for v in values if str(v).strip() and str(v).strip() != 'nan'])
        
        # Extract seniority levels
        seniority_levels_df = pd.read_excel(xl_file, sheet_name='Seniority Levels')
        seniority_levels = []
        for col in seniority_levels_df.columns:
            if 'Unnamed' not in col and col.strip():  # Skip unnamed columns
                values = seniority_levels_df[col].dropna().tolist()
                seniority_levels.extend([str(v).strip() for v in values if str(v).strip() and str(v).strip() != 'nan'])
        
        # Save to JSON for easier access
        mapping_data = {
            'job_functions': sorted(list(set(job_functions))),
            'job_industries': sorted(list(set(job_industries))),
            'seniority_levels': sorted(list(set(seniority_levels)))
        }
        
        # Save backup of old categories
        try:
            with open('job_categories_mapping.json', 'r') as f:
                old_data = json.load(f)
            
            with open('job_categories_mapping.backup.json', 'w') as f:
                json.dump(old_data, f, indent=2)
            logger.info("Created backup of old categories")
        except:
            logger.warning("No existing categories to backup")
        
        # Save new categories
        with open('job_categories_mapping.json', 'w') as f:
            json.dump(mapping_data, f, indent=2)
        
        # Show summary of changes
        logger.info("\n=== CATEGORY UPDATE SUMMARY ===")
        logger.info(f"Job Functions: {len(mapping_data['job_functions'])} unique values")
        logger.info(f"Job Industries: {len(mapping_data['job_industries'])} unique values")
        logger.info(f"Seniority Levels: {len(mapping_data['seniority_levels'])} unique values")
        
        # Show sample values
        logger.info("\nSample Job Functions:")
        for func in mapping_data['job_functions'][:5]:
            logger.info(f"  - {func}")
        
        logger.info("\nSample Job Industries:")
        for ind in mapping_data['job_industries'][:5]:
            logger.info(f"  - {ind}")
        
        logger.info("\nAll Seniority Levels:")
        for level in mapping_data['seniority_levels']:
            logger.info(f"  - {level}")
        
        logger.info("\n✓ Categories successfully updated!")
        logger.info("The AI will now use these new categories for job classification.")
        
        return True
        
    except Exception as e:
        logger.error(f"Error updating categories: {e}")
        return False

def main():
    """Main execution function"""
    if len(sys.argv) < 2:
        print("Usage: python update_job_categories.py <path_to_excel_file>")
        print("Example: python update_job_categories.py attached_assets/Updated_Categories.xlsx")
        sys.exit(1)
    
    excel_file = sys.argv[1]
    
    if update_categories_from_excel(excel_file):
        print("\n✓ Job categories have been updated successfully!")
        print("New jobs will now be classified using the updated categories.")
    else:
        print("\n✗ Failed to update job categories. Check the error messages above.")
        sys.exit(1)

if __name__ == "__main__":
    main()