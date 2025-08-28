#!/usr/bin/env python3
"""
Analyze differences between two XML uploads to identify reference number flip-flopping
"""
import re
import sys

def extract_jobs_from_xml(file_path):
    """Extract job data from XML file"""
    jobs = {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Find all job blocks
        job_blocks = re.findall(r'<job>(.*?)</job>', content, re.DOTALL)
        
        for job_block in job_blocks:
            # Extract bhatsid
            bhatsid_match = re.search(r'<bhatsid>\s*<!\[CDATA\[\s*(\d+)\s*\]\]>\s*</bhatsid>', job_block)
            # Extract reference number
            ref_match = re.search(r'<referencenumber>\s*<!\[CDATA\[\s*([A-Z0-9]+)\s*\]\]>\s*</referencenumber>', job_block)
            # Extract title
            title_match = re.search(r'<title>\s*<!\[CDATA\[\s*(.*?)\s*\]\]>\s*</title>', job_block)
            
            if bhatsid_match and ref_match and title_match:
                bhatsid = bhatsid_match.group(1)
                ref_num = ref_match.group(1)
                title = title_match.group(1)
                
                jobs[bhatsid] = {
                    'reference': ref_num,
                    'title': title
                }
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return {}
    
    return jobs

def main():
    file1 = "attached_assets/Pasted-This-XML-file-does-not-appear-to-have-any-style-information-associated-with-it-The-document-tree-is-1756398562082_1756398562082.txt"
    file2 = "attached_assets/Pasted-This-XML-file-does-not-appear-to-have-any-style-information-associated-with-it-The-document-tree-is-1756398714630_1756398714631.txt"
    
    print("🔍 ANALYZING XML REFERENCE NUMBER DIFFERENCES")
    print("=" * 60)
    
    jobs1 = extract_jobs_from_xml(file1)
    jobs2 = extract_jobs_from_xml(file2)
    
    print(f"📊 Upload 1: Found {len(jobs1)} jobs")
    print(f"📊 Upload 2: Found {len(jobs2)} jobs")
    print()
    
    # Find jobs with different reference numbers
    flip_flop_jobs = []
    missing_jobs = []
    
    for bhatsid in jobs1:
        if bhatsid in jobs2:
            if jobs1[bhatsid]['reference'] != jobs2[bhatsid]['reference']:
                flip_flop_jobs.append({
                    'bhatsid': bhatsid,
                    'title': jobs1[bhatsid]['title'],
                    'ref1': jobs1[bhatsid]['reference'],
                    'ref2': jobs2[bhatsid]['reference']
                })
        else:
            missing_jobs.append(bhatsid)
    
    # Report findings
    print("🚨 REFERENCE NUMBER FLIP-FLOPPING DETECTED:")
    print("-" * 50)
    
    if flip_flop_jobs:
        for job in flip_flop_jobs:
            print(f"Job ID {job['bhatsid']}: {job['title']}")
            print(f"  Upload 1: {job['ref1']}")
            print(f"  Upload 2: {job['ref2']}")
            print()
    else:
        print("✅ No reference number flip-flopping detected")
    
    print(f"📊 SUMMARY:")
    print(f"  - Total jobs compared: {len(set(jobs1.keys()) & set(jobs2.keys()))}")
    print(f"  - Reference number changes: {len(flip_flop_jobs)}")
    print(f"  - Jobs missing in upload 2: {len(missing_jobs)}")
    
    if flip_flop_jobs:
        print(f"\n⚠️  CRITICAL: {len(flip_flop_jobs)} jobs have unstable reference numbers!")
    
    return len(flip_flop_jobs) > 0

if __name__ == "__main__":
    has_issues = main()
    sys.exit(1 if has_issues else 0)