#!/usr/bin/env python3
"""
Update recruiter tags to new format: #LI-XX: Name
Preserves CDATA formatting while updating content
"""

import logging
from lxml import etree
import os
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define the revised recruiter mapping
RECRUITER_MAPPING = {
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
    'Myticas Recruiter': '#LI-RS',
    'Nick Theodossiou': '#LI-NT',
    'Reena Setya': '#LI-RS',
    'Runa Parmar': '#LI-RP'
}

# Create reverse mapping from tag to name
TAG_TO_NAME = {tag: name for name, tag in RECRUITER_MAPPING.items()}

def update_recruiter_tags(xml_file):
    """Update recruiter tags to new format in XML file"""
    logger.info(f"Processing {xml_file}...")
    
    # Create backup
    backup_file = f"{xml_file}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    import shutil
    shutil.copy2(xml_file, backup_file)
    logger.info(f"Created backup: {backup_file}")
    
    # Parse XML with CDATA preservation
    parser = etree.XMLParser(strip_cdata=False, recover=True)
    tree = etree.parse(xml_file, parser)
    root = tree.getroot()
    
    # Find all assignedrecruiter elements
    recruiters = root.xpath('.//assignedrecruiter')
    updates = 0
    
    for elem in recruiters:
        if elem.text:
            current_text = elem.text.strip()
            
            # Check if it's already in new format
            if ': ' in current_text:
                logger.info(f"Already in new format: {current_text}")
                continue
            
            # Handle special case for #LI-MYT which needs to be changed to #LI-RS
            if current_text == '#LI-MYT':
                # This was Myticas Recruiter, now should be #LI-RS
                new_text = '#LI-RS: Myticas Recruiter'
                elem.text = f' {new_text} '
                logger.info(f"Updated {current_text} → {new_text}")
                updates += 1
                continue
            
            # Look up the name for this tag
            if current_text in TAG_TO_NAME:
                name = TAG_TO_NAME[current_text]
                new_text = f'{current_text}: {name}'
                elem.text = f' {new_text} '
                logger.info(f"Updated {current_text} → {new_text}")
                updates += 1
            else:
                logger.warning(f"Unknown tag: {current_text}")
    
    if updates > 0:
        # Write the updated XML with CDATA preservation
        xml_content = etree.tostring(tree, encoding='unicode', pretty_print=True)
        
        # Ensure CDATA sections are preserved
        xml_content = xml_content.replace('&lt;', '<').replace('&gt;', '>')
        xml_content = xml_content.replace('&amp;', '&')
        
        with open(xml_file, 'w', encoding='utf-8') as f:
            f.write(xml_content)
        
        logger.info(f"Updated {updates} recruiter tags in {xml_file}")
    else:
        logger.info(f"No updates needed for {xml_file}")
    
    return updates

def main():
    """Update recruiter tags in both XML files"""
    xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    total_updates = 0
    
    for xml_file in xml_files:
        if os.path.exists(xml_file):
            updates = update_recruiter_tags(xml_file)
            total_updates += updates
        else:
            logger.warning(f"File not found: {xml_file}")
    
    logger.info(f"Total updates across all files: {total_updates}")
    
    if total_updates > 0:
        logger.info("Remember to upload the updated files to SFTP!")

if __name__ == "__main__":
    main()