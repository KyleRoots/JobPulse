#!/usr/bin/env python3
"""
Direct Orphaned Job Removal - Removes orphaned jobs from local XML
This directly cleans the local XML file to match expected job count.
"""

import os
import re
import requests
from datetime import datetime

def identify_and_remove_orphans():
    """Identify orphaned jobs by comparing live vs local XML and remove them"""
    
    print("🚨 DIRECT ORPHAN REMOVAL: Identifying and removing orphaned jobs...")
    
    xml_file = 'myticas-job-feed.xml'
    if not os.path.exists(xml_file):
        print(f"❌ ERROR: {xml_file} not found!")
        return False
    
    # Read local XML
    with open(xml_file, 'r') as f:
        local_content = f.read()
    
    local_job_count = local_content.count('<job>')
    print(f"📊 Local XML has {local_job_count} jobs")
    
    # Download live XML to compare
    try:
        print("🌐 Downloading live XML from server...")
        response = requests.get('https://myticas.com/myticas-job-feed.xml', timeout=30)
        if response.status_code == 200:
            live_content = response.text
            live_job_count = live_content.count('<job>')
            print(f"📊 Live XML has {live_job_count} jobs")
        else:
            print(f"⚠️ Could not download live XML (Status: {response.status_code})")
            print("📁 Using local XML as reference")
            live_content = local_content
            live_job_count = local_job_count
    except Exception as e:
        print(f"⚠️ Error downloading live XML: {str(e)}")
        print("📁 Using local XML as reference")
        live_content = local_content
        live_job_count = local_job_count
    
    # Extract job IDs from both files
    def extract_job_ids(content):
        patterns = [
            r'<bhatsid>\s*<!\[CDATA\[\s*(.*?)\s*\]\]>\s*</bhatsid>',
            r'<bhatsid>\s*<!\[CDATA\[(.*?)\]\]>\s*</bhatsid>',
            r'<bhatsid>\s*(.*?)\s*</bhatsid>',
        ]
        
        job_ids = set()
        for pattern in patterns:
            matches = re.findall(pattern, content, re.DOTALL)
            for match in matches:
                clean_id = match.strip()
                if clean_id and clean_id.isdigit():
                    job_ids.add(clean_id)
        return job_ids
    
    local_ids = extract_job_ids(local_content)
    live_ids = extract_job_ids(live_content)
    
    print(f"🔍 Local XML has {len(local_ids)} valid job IDs")
    print(f"🔍 Live XML has {len(live_ids)} valid job IDs")
    
    # Find orphaned job IDs (in live but not in expected set)
    # For this fix, we'll assume local XML has the correct jobs
    expected_jobs = local_ids
    
    # If live has more jobs, identify the extras
    if live_job_count > local_job_count:
        print(f"🚨 CORRUPTION DETECTED: Live has {live_job_count - local_job_count} extra jobs")
        
        # Extract all job blocks from live XML
        job_blocks = re.findall(r'<job>.*?</job>', live_content, re.DOTALL)
        print(f"🔍 Found {len(job_blocks)} job blocks in live XML")
        
        orphaned_blocks = []
        for block in job_blocks:
            # Extract ID from this block
            block_id = None
            for pattern in [
                r'<bhatsid>\s*<!\[CDATA\[\s*(.*?)\s*\]\]>\s*</bhatsid>',
                r'<bhatsid>\s*<!\[CDATA\[(.*?)\]\]>\s*</bhatsid>',
                r'<bhatsid>\s*(.*?)\s*</bhatsid>',
            ]:
                matches = re.findall(pattern, block, re.DOTALL)
                if matches:
                    block_id = matches[0].strip()
                    break
            
            # If block has no valid ID or ID not in expected set, it's orphaned
            if not block_id or not block_id.isdigit() or block_id not in expected_jobs:
                orphaned_blocks.append((block, block_id))
        
        print(f"🗑️ Found {len(orphaned_blocks)} orphaned job blocks")
        
        # Remove orphaned blocks from local XML to create clean version
        backup_file = f"{xml_file}.orphan_removal_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        import shutil
        shutil.copy2(xml_file, backup_file)
        print(f"💾 Created backup: {backup_file}")
        
        # Clean local XML by removing any orphaned blocks that might exist
        cleaned_content = local_content
        removed_count = 0
        
        for orphan_block, orphan_id in orphaned_blocks:
            if orphan_block in cleaned_content:
                cleaned_content = cleaned_content.replace(orphan_block, '', 1)
                removed_count += 1
                print(f"   🗑️ Removed orphaned job {orphan_id or 'NO_ID'}")
        
        # Write cleaned XML
        with open(xml_file, 'w', encoding='utf-8') as f:
            f.write(cleaned_content)
        
        # Verify result
        final_count = cleaned_content.count('<job>')
        print(f"✅ CLEANUP COMPLETE:")
        print(f"   Original: {local_job_count} jobs")
        print(f"   Removed: {removed_count} orphaned blocks")  
        print(f"   Final: {final_count} jobs")
        
        if final_count <= local_job_count:
            print("✅ SUCCESS: Local XML cleaned of orphaned jobs")
            print("🎯 Next step: Upload this clean XML to fix live server")
            return True
        else:
            print("⚠️ WARNING: Unexpected result in cleanup")
            return False
    else:
        print("✅ No orphaned jobs detected in live XML")
        return True

if __name__ == "__main__":
    success = identify_and_remove_orphans()
    exit(0 if success else 1)