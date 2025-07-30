#!/usr/bin/env python3

import os
import sys
from lxml import etree
from xml_processor import XMLProcessor

def fix_reference_numbers():
    """Fix reference numbers in the XML files"""
    
    print("🔧 Fixing reference numbers in XML files...")
    
    xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
    processor = XMLProcessor()
    
    for xml_file in xml_files:
        print(f"\n📝 Processing {xml_file}...")
        
        try:
            # Parse XML
            tree = etree.parse(xml_file)
            root = tree.getroot()
            
            # Track existing reference numbers to avoid duplicates
            existing_refs = set()
            
            # First pass - collect any existing valid reference numbers
            for job in root.xpath('.//job'):
                ref_elem = job.find('referencenumber')
                if ref_elem is not None and ref_elem.text:
                    ref_text = ref_elem.text.strip()
                    # Check if it's a valid reference number (not a monitor name)
                    if len(ref_text) == 10 and not any(word in ref_text.lower() for word in ['sponsored', 'ottawa', 'vms', 'clover', 'chicago', 'cleveland']):
                        existing_refs.add(ref_text)
            
            print(f"   Found {len(existing_refs)} existing valid reference numbers")
            
            # Second pass - fix reference numbers
            fixed_count = 0
            for job in root.xpath('.//job'):
                ref_elem = job.find('referencenumber')
                if ref_elem is not None:
                    ref_text = ref_elem.text.strip() if ref_elem.text else ''
                    
                    # Check if reference number needs fixing
                    if len(ref_text) != 10 or any(word in ref_text.lower() for word in ['sponsored', 'ottawa', 'vms', 'clover', 'chicago', 'cleveland']):
                        # Generate new reference number
                        new_ref = processor.generate_reference_number()
                        # Make sure it's unique
                        while new_ref in existing_refs:
                            new_ref = processor.generate_reference_number()
                        ref_elem.text = etree.CDATA(f' {new_ref} ')
                        existing_refs.add(new_ref)
                        fixed_count += 1
            
            print(f"   ✅ Fixed {fixed_count} reference numbers")
            
            # Save updated XML
            tree.write(xml_file, encoding='utf-8', xml_declaration=True, pretty_print=True)
            
            file_size = os.path.getsize(xml_file)
            print(f"   💾 Saved {xml_file} ({file_size:,} bytes)")
            
        except Exception as e:
            print(f"   ❌ Error processing {xml_file}: {str(e)}")
            return False
    
    print("\n🎯 Reference numbers fixed successfully!")
    return True

if __name__ == "__main__":
    success = fix_reference_numbers()
    sys.exit(0 if success else 1)