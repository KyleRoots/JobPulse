#!/usr/bin/env python3
"""
Test script for recruiter name mapping functionality
"""

import logging
from xml_integration_service import XMLIntegrationService

# Set up logging
logging.basicConfig(level=logging.INFO)

def test_recruiter_mapping():
    """Test the recruiter name to LinkedIn tag mapping"""
    
    # Create XML integration service instance
    service = XMLIntegrationService()
    
    # Test cases
    test_cases = [
        'Michael Theodossiou',
        'Myticas Recruiter', 
        'Runa Parmar',
        'Adam Gebara',
        'Dan Sifer',
        'Mike Gebara',
        'Christine Carter',
        'Michelle Corino',
        'Amanda Messina',
        'Dominic Scaletta',
        'Bryan Chinzorig',
        'Reena Setya',
        'Unknown Recruiter'  # Test case for unmapped name
    ]
    
    print("🧪 Testing Recruiter Name to LinkedIn Tag Mapping")
    print("=" * 60)
    
    for name in test_cases:
        mapped_tag = service._map_recruiter_to_linkedin_tag(name)
        print(f"'{name}' → '{mapped_tag}'")
    
    print("\n" + "=" * 60)
    print("✅ Recruiter mapping test completed!")
    print("\n⚠️  Potential conflicts detected:")
    print("   • Michael Theodossiou: Listed with both #LI-MIT and #LI-MAT")
    print("   • Dan Sifer & Dominic Scaletta: Both map to #LI-DS")
    print("   • Myticas Recruiter & Reena Setya: Both map to #LI-RS")
    print("\nPlease clarify these conflicts for accurate mapping.")

if __name__ == "__main__":
    test_recruiter_mapping()