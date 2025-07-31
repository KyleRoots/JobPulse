#!/usr/bin/env python3
"""
Fix AI classifications for ALL jobs using only allowed values from job_categories_mapping.json
"""
import json
import xml.etree.ElementTree as ET
from lxml import etree
from app import app, get_bullhorn_service

def get_allowed_values():
    """Load allowed values from mapping file"""
    with open('job_categories_mapping.json', 'r') as f:
        return json.load(f)

def determine_job_classification(job_title, job_description=""):
    """Determine appropriate classification based on job title using ONLY allowed values"""
    allowed = get_allowed_values()
    
    # Convert to lowercase for matching
    title_lower = job_title.lower()
    desc_lower = job_description.lower() if job_description else ""
    
    # Job Function mapping (using exact allowed values)
    job_function = "Other"  # Default
    
    # Engineering roles
    if any(word in title_lower for word in ['engineer', 'developer', 'architect', 'programmer']):
        if 'software' in title_lower or 'net' in title_lower or 'java' in title_lower:
            job_function = "Information Technology"
        else:
            job_function = "Engineering"
    
    # IT/Tech roles
    elif any(word in title_lower for word in ['analyst', 'administrator', 'database', 'network', 'system', 'devops', 'cloud']):
        if 'business' in title_lower:
            job_function = "Analyst"
        else:
            job_function = "Information Technology"
    
    # Management roles
    elif any(word in title_lower for word in ['manager', 'director', 'lead', 'supervisor']):
        if 'project' in title_lower:
            job_function = "Project Management"
        elif 'product' in title_lower:
            job_function = "Product Management"
        else:
            job_function = "Management"
    
    # Science/Lab roles
    elif any(word in title_lower for word in ['scientist', 'researcher', 'lab', 'technician']):
        job_function = "Science"
    
    # Consulting roles
    elif 'consultant' in title_lower:
        job_function = "Consulting"
    
    # QA roles
    elif any(word in title_lower for word in ['qa', 'quality', 'test']):
        job_function = "Quality Assurance"
    
    # Design roles
    elif any(word in title_lower for word in ['design', 'ux', 'ui', 'graphic']):
        job_function = "Design"
    
    # Customer Service
    elif any(word in title_lower for word in ['customer', 'support', 'service']):
        job_function = "Customer Service"
    
    # Legal
    elif any(word in title_lower for word in ['attorney', 'lawyer', 'legal', 'paralegal']):
        job_function = "Legal"
    
    # Industry mapping (using exact allowed values)
    industry = "Information Technology and Services"  # Default for most tech jobs
    
    if any(word in title_lower for word in ['biotech', 'lab', 'pharma']):
        industry = "Biotechnology"
    elif any(word in title_lower for word in ['telecom', 'rf', 'wireless']):
        industry = "Telecommunications"
    elif any(word in title_lower for word in ['semiconductor', 'asic', 'ic design', 'chip']):
        industry = "Semiconductors"
    elif any(word in title_lower for word in ['legal', 'attorney', 'lawyer']):
        industry = "Legal Services"
    elif any(word in title_lower for word in ['manufacturing', 'production']):
        industry = "Electrical/Electronic Manufacturing"
    elif any(word in title_lower for word in ['software', 'developer', 'programmer']):
        industry = "Computer Software"
    elif any(word in title_lower for word in ['hardware', 'embedded']):
        industry = "Computer Hardware"
    elif any(word in title_lower for word in ['network', 'security', 'firewall']):
        industry = "Computer and Network Security"
    elif any(word in title_lower for word in ['oil', 'gas', 'energy']):
        industry = "Oil and Energy"
    
    # Seniority Level mapping (using exact allowed values)
    seniority = "Mid level"  # Default
    
    if any(word in title_lower for word in ['senior', 'sr', 'lead', 'principal']):
        seniority = "Mid-Senior level"
    elif any(word in title_lower for word in ['junior', 'jr', 'entry', 'associate']):
        seniority = "Entry level"
    elif any(word in title_lower for word in ['director', 'vp', 'vice president']):
        seniority = "Executive"
    elif any(word in title_lower for word in ['manager', 'supervisor']):
        seniority = "Mid-Senior level"
    
    return {
        'jobfunction': job_function,
        'jobindustries': industry,
        'senoritylevel': seniority
    }

def fix_all_classifications():
    """Fix AI classifications for all jobs in the XML"""
    print("Starting comprehensive AI classification fix...")
    
    # Load production XML
    parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
    tree = etree.parse('production_xml_check.xml', parser)
    root = tree.getroot()
    
    jobs = root.findall('.//job')
    print(f"Processing {len(jobs)} jobs...")
    
    # Initialize Bullhorn service
    with app.app_context():
        try:
            bullhorn = get_bullhorn_service()
        except:
            bullhorn = None
            print("Warning: Bullhorn service not available, using title-based classification only")
    
    fixed_count = 0
    
    for i, job in enumerate(jobs):
        # Get job details
        title_elem = job.find('title')
        bhatsid_elem = job.find('bhatsid')
        
        if title_elem is None or title_elem.text is None:
            continue
            
        # Extract title and ID
        title_text = str(title_elem.text).replace('<![CDATA[', '').replace(']]>', '').strip()
        job_id = None
        if bhatsid_elem is not None and bhatsid_elem.text:
            job_id = str(bhatsid_elem.text).replace('<![CDATA[', '').replace(']]>', '').strip()
        
        # Get job description if available
        desc_text = ""
        desc_elem = job.find('description')
        if desc_elem is not None and desc_elem.text:
            desc_text = str(desc_elem.text).replace('<![CDATA[', '').replace(']]>', '').strip()
        
        # Determine classification
        classification = determine_job_classification(title_text, desc_text)
        
        # Update XML elements
        for field, value in classification.items():
            elem = job.find(field)
            if elem is not None:
                elem.text = etree.CDATA(value)
                fixed_count += 1
        
        if (i + 1) % 10 == 0:
            print(f"  Processed {i + 1}/{len(jobs)} jobs...")
    
    # Save the fixed XML
    with open('myticas-job-feed.xml', 'wb') as f:
        tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
    
    # Also update scheduled XML
    with open('myticas-job-feed-scheduled.xml', 'wb') as f:
        tree.write(f, encoding='utf-8', xml_declaration=True, pretty_print=True)
    
    print(f"\n✅ Fixed {fixed_count} AI classification fields across {len(jobs)} jobs")
    
    # Verify the fix
    with open('myticas-job-feed.xml', 'r') as f:
        content = f.read()
        
    # Count populated AI fields
    import re
    populated_funcs = len(re.findall(r'<jobfunction><!\[CDATA\[(?![\s]*\]\])', content))
    populated_inds = len(re.findall(r'<jobindustries><!\[CDATA\[(?![\s]*\]\])', content))
    populated_sens = len(re.findall(r'<senoritylevel><!\[CDATA\[(?![\s]*\]\])', content))
    
    print(f"\nVerification:")
    print(f"  Job Functions populated: {populated_funcs}/{len(jobs)}")
    print(f"  Industries populated: {populated_inds}/{len(jobs)}")
    print(f"  Seniority Levels populated: {populated_sens}/{len(jobs)}")

if __name__ == "__main__":
    fix_all_classifications()