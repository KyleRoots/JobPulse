#!/usr/bin/env python3
"""Test script to verify HTML and CDATA fixes"""

import sys
from incremental_monitoring_service import IncrementalMonitoringService
from xml_integration_service import XMLIntegrationService
from lxml import etree

def test_html_fix():
    """Test the HTML fixing functionality"""
    print("Testing HTML fix...")
    
    service = IncrementalMonitoringService()
    
    # Test case with orphaned </li> tags
    broken_html = """<ul>
<li>First item
<li>Second item  
<li>Third item
</ul>"""
    
    fixed_html = service._fix_unclosed_html_tags(broken_html)
    print(f"Original HTML:\n{broken_html}")
    print(f"\nFixed HTML:\n{fixed_html}")
    
    # Check that no orphaned </li> tags exist
    if "</li><li>" in fixed_html and not fixed_html.startswith("</li>"):
        print("✅ HTML fix successful - no orphaned </li> tags")
    else:
        print("❌ HTML fix may have issues")
    
    return fixed_html

def test_cdata_wrapping():
    """Test that all fields are wrapped in CDATA"""
    print("\nTesting CDATA wrapping...")
    
    service = IncrementalMonitoringService()
    
    # Create a test job data
    test_job = {
        'title': 'Test Job Title',
        'company': 'Test Company',
        'date': 'January 01, 2025',
        'referencenumber': 'TEST123REF',
        'bhatsid': '12345',
        'url': 'https://example.com/job/12345',
        'description': '<p>Test description with <strong>HTML</strong></p>',
        'jobtype': 'Full-time',
        'city': 'Test City',
        'state': 'TS',
        'country': 'United States',
        'category': '',
        'apply_email': 'apply@example.com',
        'remotetype': 'Remote',
        'assignedrecruiter': 'John Doe',
        'jobfunction': 'Engineering',
        'jobindustries': 'Technology',
        'senioritylevel': 'Senior'
    }
    
    # Create job element
    job_element = service._create_job_element(test_job)
    
    # Convert to string to check
    xml_string = etree.tostring(job_element, encoding='unicode', pretty_print=True)
    print(f"Generated XML:\n{xml_string}")
    
    # Check that all fields are wrapped in CDATA
    cdata_count = xml_string.count('<![CDATA[')
    field_count = len(test_job)
    
    if cdata_count == field_count:
        print(f"✅ All {field_count} fields are wrapped in CDATA")
    else:
        print(f"❌ Only {cdata_count} of {field_count} fields are wrapped in CDATA")
    
    return xml_string

def test_xml_integration_service():
    """Test the XML integration service HTML cleaning"""
    print("\nTesting XML Integration Service HTML cleaning...")
    
    service = XMLIntegrationService()
    
    # Test case with broken HTML
    broken_html = """<ul>
<li>First item
<li>Second item  
<li>Third item
</ul>"""
    
    fixed_html = service._clean_description(broken_html)
    print(f"Fixed HTML from XML Integration Service:\n{fixed_html}")
    
    if "</li><li>" in fixed_html and not "</li><li>" == fixed_html[:8]:
        print("✅ XML Integration Service HTML fix successful")
    else:
        print("❌ XML Integration Service HTML fix may have issues")
    
    return fixed_html

if __name__ == "__main__":
    print("=" * 60)
    print("XML and HTML Fixes Test")
    print("=" * 60)
    
    try:
        # Test HTML fixing
        fixed_html = test_html_fix()
        
        # Test CDATA wrapping
        xml_output = test_cdata_wrapping()
        
        # Test XML integration service
        xml_integration_html = test_xml_integration_service()
        
        print("\n" + "=" * 60)
        print("Test Summary:")
        print("✅ All tests completed")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)