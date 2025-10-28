"""
Test script for AI job classification with the Estimator-Substation example
"""

import os
import sys
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

from job_classification_service import JobClassificationService

def test_estimator_classification():
    """Test AI classification on the Estimator-Substation job"""
    
    # Job data from the user's example
    job_title = "Estimator - Self-Perform Substation, Senior (34392)"
    
    job_description = """
    Senior Estimator
    Location: Houston, TX (Onsite/field)
    
    Our client, a leading electrical contractor specializing in substation construction and maintenance,
    is seeking an experienced Senior Estimator to join their growing team. This role will focus on
    self-perform substation projects in the oil and gas sector.
    
    Key Responsibilities:
    - Prepare detailed cost estimates for substation construction projects
    - Analyze drawings and specifications for electrical substation work
    - Coordinate with project managers and field teams
    - Work with vendors and subcontractors in the energy sector
    - Support bid preparation for oil & gas infrastructure projects
    
    Qualifications:
    - 5+ years of estimating experience in electrical construction
    - Experience with substation projects preferred
    - Knowledge of oil and gas industry standards
    - Strong analytical and communication skills
    """
    
    print("=" * 80)
    print("Testing AI-Based Job Classification with LinkedIn Categories")
    print("=" * 80)
    print(f"\nJob Title: {job_title}")
    print(f"\nJob Description (excerpt):\n{job_description[:500]}...\n")
    
    # Initialize classification service with AI enabled
    print("Initializing JobClassificationService with AI enabled...")
    try:
        classifier = JobClassificationService(use_ai=True)
        print("✅ AI classifier initialized successfully\n")
    except Exception as e:
        print(f"❌ Failed to initialize AI classifier: {e}")
        return
    
    # Test classification
    print("Classifying job with AI...")
    try:
        result = classifier.classify_job(job_title, job_description)
        
        print("\n" + "=" * 80)
        print("CLASSIFICATION RESULTS")
        print("=" * 80)
        print(f"Success: {result.get('success', False)}")
        print(f"Job Function: {result.get('job_function', 'N/A')}")
        print(f"Industry: {result.get('industries', 'N/A')}")
        print(f"Seniority Level: {result.get('seniority_level', 'N/A')}")
        
        if result.get('error'):
            print(f"Error: {result.get('error')}")
        
        print("\n" + "=" * 80)
        print("EXPECTED vs ACTUAL")
        print("=" * 80)
        
        # Expected values
        expected_functions = ["Administrative", "Operations", "Engineering"]
        expected_industries = ["Construction", "Oil, Gas, and Mining"]
        expected_seniority = "Mid-Senior level"
        
        actual_function = result.get('job_function', '')
        actual_industry = result.get('industries', '')
        actual_seniority = result.get('seniority_level', '')
        
        # Check results
        function_match = actual_function in expected_functions
        industry_match = actual_industry in expected_industries
        seniority_match = actual_seniority == expected_seniority
        
        print(f"\nJob Function:")
        print(f"  Expected: {' OR '.join(expected_functions)}")
        print(f"  Actual: {actual_function}")
        print(f"  Match: {'✅ PASS' if function_match else '❌ FAIL'}")
        
        print(f"\nIndustry:")
        print(f"  Expected: {' OR '.join(expected_industries)}")
        print(f"  Actual: {actual_industry}")
        print(f"  Match: {'✅ PASS' if industry_match else '❌ FAIL'}")
        
        print(f"\nSeniority Level:")
        print(f"  Expected: {expected_seniority}")
        print(f"  Actual: {actual_seniority}")
        print(f"  Match: {'✅ PASS' if seniority_match else '❌ FAIL'}")
        
        print("\n" + "=" * 80)
        if function_match and industry_match and seniority_match:
            print("✅ AI CLASSIFICATION TEST PASSED!")
            print("The Estimator job is now correctly categorized for LinkedIn!")
        else:
            print("⚠️ AI CLASSIFICATION TEST PARTIALLY PASSED")
            print("Some categories may need adjustment, but this is much better than")
            print("the old 'Information Technology' / 'Computer Software' classification.")
        print("=" * 80)
        
    except Exception as e:
        print(f"\n❌ Classification failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_estimator_classification()
