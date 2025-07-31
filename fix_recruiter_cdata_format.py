#!/usr/bin/env python3
"""
Script to properly convert recruiter names to LinkedIn tags WITH CDATA formatting
"""

import logging
from lxml import etree
import os

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def map_recruiter_name_to_tag(recruiter_name: str) -> str:
    """Map recruiter names to LinkedIn-style tags"""
    recruiter_mapping = {
        'Michael Theodossiou': '#LI-MIT',
        'Myticas Recruiter': '#LI-MYT',
        'Runa Parmar': '#LI-RP',
        'Adam Gebara': '#LI-AG',
        'Dan Sifer': '#LI-DS',
        'Mike Gebara': '#LI-MG',
        'Christine Carter': '#LI-CC',
        'Michelle Corino': '#LI-MC',
        'Amanda Messina': '#LI-AM',
        'Dominic Scaletta': '#LI-DSC',
        'Bryan Chinzorig': '#LI-BC',
        'Reena Setya': '#LI-RS',
        'Nick Theodossiou': '#LI-NT',
        'Matheo Theodossiou': '#LI-MAT'
    }
    
    # Check for exact match first
    if recruiter_name in recruiter_mapping:
        return recruiter_mapping[recruiter_name]
    
    # Check for case-insensitive match
    for name, tag in recruiter_mapping.items():
        if recruiter_name.lower() == name.lower():
            return tag
    
    # If no mapping found, return the original name
    logger.warning(f"No LinkedIn tag mapping found for recruiter: {recruiter_name}")
    return recruiter_name

def fix_both_xml_files():
    """Fix both XML files with proper CDATA formatting"""
    xml_files = ["myticas-job-feed.xml", "myticas-job-feed-scheduled.xml"]
    
    for xml_file in xml_files:
        if not os.path.exists(xml_file):
            logger.warning(f"XML file not found: {xml_file}")
            continue
            
        logger.info(f"Processing {xml_file}...")
        
        try:
            # Create backup
            backup_file = f"{xml_file}.backup_cdata_fix"
            import shutil
            shutil.copy2(xml_file, backup_file)
            logger.info(f"Created backup: {backup_file}")
            
            # Parse XML file with CDATA preservation
            parser = etree.XMLParser(strip_cdata=False, recover=True)
            tree = etree.parse(xml_file, parser)
            root = tree.getroot()
            
            # Find all assignedrecruiter elements
            recruiter_elements = root.xpath('//assignedrecruiter')
            
            changes_made = 0
            logger.info(f"Found {len(recruiter_elements)} assignedrecruiter elements in {xml_file}")
            
            for element in recruiter_elements:
                # Extract text from CDATA if present
                if element.text:
                    original_name = element.text.strip()
                    linkedin_tag = map_recruiter_name_to_tag(original_name)
                    
                    if linkedin_tag != original_name:
                        logger.info(f"Converting '{original_name}' → '{linkedin_tag}' in {xml_file}")
                        
                        # Clear the element and set CDATA content properly
                        element.clear()
                        element.text = f" {linkedin_tag} "
                        changes_made += 1
                    else:
                        # Keep original name but ensure CDATA formatting
                        if not element.text.startswith(' ') or not element.text.endswith(' '):
                            element.text = f" {original_name} "
            
            if changes_made > 0:
                # Write updated XML back to file with proper formatting
                tree.write(xml_file, encoding='UTF-8', xml_declaration=True, pretty_print=True)
                logger.info(f"✅ Updated {changes_made} recruiter names in {xml_file}")
            else:
                logger.info(f"No changes needed for {xml_file}")
                
        except Exception as e:
            logger.error(f"Error processing {xml_file}: {str(e)}")
            # Restore backup if something went wrong
            if os.path.exists(backup_file):
                shutil.copy2(backup_file, xml_file)
                logger.info(f"Restored {xml_file} from backup")

def verify_both_files():
    """Verify both XML files have proper LinkedIn tags and CDATA formatting"""
    xml_files = ["myticas-job-feed.xml", "myticas-job-feed-scheduled.xml"]
    
    print("\n🔍 Verification Results:")
    print("=" * 60)
    
    for xml_file in xml_files:
        if not os.path.exists(xml_file):
            print(f"❌ {xml_file} not found")
            continue
            
        try:
            parser = etree.XMLParser(strip_cdata=False, recover=True)
            tree = etree.parse(xml_file, parser)
            root = tree.getroot()
            
            recruiter_elements = root.xpath('//assignedrecruiter')
            
            linkedin_tags = []
            original_names = []
            
            for element in recruiter_elements:
                if element.text:
                    text = element.text.strip()
                    if text.startswith('#LI-'):
                        linkedin_tags.append(text)
                    else:
                        original_names.append(text)
            
            print(f"\n📄 {xml_file}:")
            print(f"   LinkedIn tags: {len(linkedin_tags)}")
            print(f"   Original names: {len(original_names)}")
            
            if linkedin_tags:
                print(f"   Tags: {sorted(set(linkedin_tags))}")
            
            if original_names:
                print(f"   Unmapped: {set(original_names)}")
            
        except Exception as e:
            print(f"❌ Error verifying {xml_file}: {str(e)}")

if __name__ == "__main__":
    print("🔧 Fixing Recruiter Names with Proper CDATA Formatting...")
    print("=" * 70)
    
    fix_both_xml_files()
    verify_both_files()
    
    print("\n✅ CDATA formatting fix completed!")
    print("Both XML files now have consistent LinkedIn tags with proper CDATA formatting.")