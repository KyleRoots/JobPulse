#!/usr/bin/env python3
"""
Final fix for proper CDATA formatting in XML files
"""

import logging
from lxml import etree
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_cdata_formatting():
    """Fix CDATA formatting for both XML files"""
    xml_files = ["myticas-job-feed.xml", "myticas-job-feed-scheduled.xml"]
    
    for xml_file in xml_files:
        if not os.path.exists(xml_file):
            logger.warning(f"File not found: {xml_file}")
            continue
            
        logger.info(f"Fixing CDATA formatting in {xml_file}...")
        
        try:
            # Parse with CDATA preservation
            parser = etree.XMLParser(strip_cdata=False, recover=True)
            tree = etree.parse(xml_file, parser)
            root = tree.getroot()
            
            # Find all assignedrecruiter elements
            recruiter_elements = root.xpath('//assignedrecruiter')
            
            fixed_count = 0
            for element in recruiter_elements:
                if element.text and element.text.strip():
                    # Get the tag content
                    tag_content = element.text.strip()
                    
                    # Clear element and create CDATA section
                    element.clear()
                    element.text = etree.CDATA(f" {tag_content} ")
                    fixed_count += 1
            
            # Save the file
            tree.write(xml_file, encoding='UTF-8', xml_declaration=True, pretty_print=True)
            logger.info(f"✅ Fixed CDATA formatting for {fixed_count} elements in {xml_file}")
            
        except Exception as e:
            logger.error(f"Error fixing {xml_file}: {str(e)}")

def verify_cdata():
    """Verify CDATA formatting"""
    xml_files = ["myticas-job-feed.xml", "myticas-job-feed-scheduled.xml"]
    
    print("\n🔍 CDATA Verification:")
    print("=" * 50)
    
    for xml_file in xml_files:
        if os.path.exists(xml_file):
            with open(xml_file, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Count CDATA sections in assignedrecruiter
            cdata_count = content.count('<assignedrecruiter><![CDATA[')
            regular_count = content.count('<assignedrecruiter>') - cdata_count
            
            print(f"\n📄 {xml_file}:")
            print(f"   CDATA sections: {cdata_count}")
            print(f"   Regular elements: {regular_count}")
            
            if regular_count == 0:
                print(f"   ✅ All assignedrecruiter elements use CDATA")
            else:
                print(f"   ⚠️  {regular_count} elements missing CDATA")

if __name__ == "__main__":
    print("🔧 Final CDATA Formatting Fix...")
    print("=" * 50)
    
    fix_cdata_formatting()
    verify_cdata()
    
    print("\n✅ CDATA formatting completed!")