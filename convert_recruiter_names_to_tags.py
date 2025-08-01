#!/usr/bin/env python3
"""
Script to convert existing recruiter names in XML file to LinkedIn-style tags
"""

import logging
from lxml import etree
import os

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def map_recruiter_name_to_tag(recruiter_name: str) -> str:
    """Map recruiter names to LinkedIn-style tags"""
    # Define recruiter name to LinkedIn tag mapping - REVISED LIST
    recruiter_mapping = {
        'Adam Gebara': '#LI-AG',
        'Amanda Messina': '#LI-AM',
        'Bryan Chinzorig': '#LI-BC',
        'Christine Carter': '#LI-CC',
        'Dan Sifer': '#LI-DS',
        'Dominic Scaletta': '#LI-DSC',
        'Matheo Theodossiou': '#LI-MAT',
        'Michael Theodossiou': '#LI-MIT',
        'Michelle Corino': '#LI-MC',
        'Mike Gebara': '#LI-MG',
        'Myticas Recruiter': '#LI-RS',  # Now using #LI-RS
        'Nick Theodossiou': '#LI-NT',
        'Reena Setya': '#LI-RS',        # Also using #LI-RS
        'Runa Parmar': '#LI-RP'
    }
    
    # Check for exact match first
    if recruiter_name in recruiter_mapping:
        # Return format with both tag and name
        return f"{recruiter_mapping[recruiter_name]}: {recruiter_name}"
    
    # Check for case-insensitive match
    for name, tag in recruiter_mapping.items():
        if recruiter_name.lower() == name.lower():
            # Return format with both tag and name
            return f"{tag}: {name}"
    
    # If no mapping found, return the original name
    logger.warning(f"No LinkedIn tag mapping found for recruiter: {recruiter_name}")
    return recruiter_name

def convert_xml_recruiter_names():
    """Convert all recruiter names in the XML file to LinkedIn tags"""
    xml_file = "myticas-job-feed.xml"
    backup_file = f"{xml_file}.backup_before_tag_conversion"
    
    if not os.path.exists(xml_file):
        logger.error(f"XML file not found: {xml_file}")
        return False
    
    try:
        # Create backup
        import shutil
        shutil.copy2(xml_file, backup_file)
        logger.info(f"Created backup: {backup_file}")
        
        # Parse XML file
        parser = etree.XMLParser(strip_cdata=False, recover=True)
        tree = etree.parse(xml_file, parser)
        root = tree.getroot()
        
        # Find all assignedrecruiter elements
        recruiter_elements = root.xpath('//assignedrecruiter')
        
        changes_made = 0
        logger.info(f"Found {len(recruiter_elements)} assignedrecruiter elements")
        
        for element in recruiter_elements:
            if element.text:
                original_name = element.text.strip()
                linkedin_tag = map_recruiter_name_to_tag(original_name)
                
                if linkedin_tag != original_name:
                    logger.info(f"Converting '{original_name}' → '{linkedin_tag}'")
                    # Create CDATA section with proper formatting
                    element.clear()
                    element.text = f" {linkedin_tag} "
                    changes_made += 1
                else:
                    logger.info(f"No mapping for '{original_name}', keeping original")
        
        if changes_made > 0:
            # Write updated XML back to file
            tree.write(xml_file, encoding='UTF-8', xml_declaration=True, pretty_print=True)
            logger.info(f"✅ Successfully updated {changes_made} recruiter names to LinkedIn tags")
            logger.info(f"Updated XML file: {xml_file}")
        else:
            logger.info("No changes were needed")
        
        return True
        
    except Exception as e:
        logger.error(f"Error converting recruiter names: {str(e)}")
        # Restore backup if something went wrong
        if os.path.exists(backup_file):
            shutil.copy2(backup_file, xml_file)
            logger.info(f"Restored original file from backup")
        return False

def verify_conversion():
    """Verify the conversion was successful"""
    xml_file = "myticas-job-feed.xml"
    
    try:
        parser = etree.XMLParser(strip_cdata=False, recover=True)
        tree = etree.parse(xml_file, parser)
        root = tree.getroot()
        
        recruiter_elements = root.xpath('//assignedrecruiter')
        
        print("\n🔍 Verification Results:")
        print("=" * 50)
        
        linkedin_tags = []
        original_names = []
        
        for element in recruiter_elements:
            if element.text:
                text = element.text.strip()
                if text.startswith('#LI-'):
                    linkedin_tags.append(text)
                else:
                    original_names.append(text)
        
        print(f"LinkedIn tags found: {len(linkedin_tags)}")
        print(f"Original names remaining: {len(original_names)}")
        
        if linkedin_tags:
            print(f"\nLinkedIn tags: {set(linkedin_tags)}")
        
        if original_names:
            print(f"\nUnmapped names: {set(original_names)}")
        
        if original_names:
            print("\n⚠️  Some recruiter names were not converted")
        else:
            print("\n✅ All recruiter names successfully converted to LinkedIn tags")
        
        return len(original_names) == 0
        
    except Exception as e:
        logger.error(f"Error verifying conversion: {str(e)}")
        return False

if __name__ == "__main__":
    print("🔧 Converting Recruiter Names to LinkedIn Tags...")
    print("=" * 60)
    
    success = convert_xml_recruiter_names()
    
    if success:
        verify_conversion()
        print("\n🎉 Conversion completed successfully!")
    else:
        print("\n❌ Conversion failed - check logs for details")