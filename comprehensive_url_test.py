#!/usr/bin/env python3
"""
Comprehensive URL Test - Verify URL integrity in all job change scenarios
"""
import logging
from lxml import etree
import sys

def test_url_scenarios():
    """Test URL generation across all job change scenarios"""
    
    xml_file = "myticas-job-feed.xml"
    
    try:
        with open(xml_file, 'rb') as f:
            tree = etree.parse(f)
        root = tree.getroot()
        
        total_jobs = 0
        unique_urls = 0
        generic_urls = 0
        invalid_urls = 0
        url_examples = []
        
        for job in root.xpath('.//job'):
            total_jobs += 1
            
            url_elem = job.find('.//url')
            bhatsid_elem = job.find('.//bhatsid')
            title_elem = job.find('.//title')
            
            if url_elem is not None and url_elem.text:
                url_text = url_elem.text.strip()
                job_id = bhatsid_elem.text.strip() if bhatsid_elem is not None else "Unknown"
                job_title = title_elem.text.strip() if title_elem is not None else "Unknown"
                
                if url_text == "https://myticas.com/" or url_text == "https://myticas.com":
                    generic_urls += 1
                    if len(url_examples) < 3 and "GENERIC" not in [ex.split(":")[0] for ex in url_examples]:
                        url_examples.append(f"GENERIC: Job {job_id} ({job_title})")
                elif "apply.myticas.com" in url_text and job_id in url_text:
                    unique_urls += 1
                    if len(url_examples) < 3 and "UNIQUE" not in [ex.split(":")[0] for ex in url_examples]:
                        url_examples.append(f"UNIQUE: Job {job_id} -> {url_text}")
                else:
                    invalid_urls += 1
                    if len(url_examples) < 3:
                        url_examples.append(f"INVALID: Job {job_id} -> {url_text}")
        
        print(f"🔍 COMPREHENSIVE URL VERIFICATION RESULTS")
        print(f"{'='*50}")
        print(f"Total Jobs Analyzed: {total_jobs}")
        print(f"Unique URLs: {unique_urls} ({unique_urls/total_jobs*100:.1f}%)")
        print(f"Generic URLs: {generic_urls} ({generic_urls/total_jobs*100:.1f}%)")
        print(f"Invalid URLs: {invalid_urls} ({invalid_urls/total_jobs*100:.1f}%)")
        print(f"")
        
        if url_examples:
            print(f"URL Examples:")
            for example in url_examples:
                print(f"  • {example}")
        print(f"")
        
        # Determine overall status
        status_icon = "✅" if generic_urls == 0 else ("⚠️" if generic_urls < total_jobs * 0.1 else "❌")
        status_text = "EXCELLENT" if generic_urls == 0 else ("GOOD" if generic_urls < total_jobs * 0.1 else "NEEDS ATTENTION")
        
        print(f"{status_icon} OVERALL STATUS: {status_text}")
        
        if generic_urls > 0:
            print(f"🚨 WARNING: {generic_urls} jobs have generic URLs that need fixing")
            return False
        else:
            print(f"✅ All job URLs are properly formatted and unique!")
            return True
            
    except Exception as e:
        print(f"❌ ERROR: Failed to verify URLs - {str(e)}")
        return False

if __name__ == "__main__":
    success = test_url_scenarios()
    sys.exit(0 if success else 1)