#!/usr/bin/env python3
"""
Test the HTML formatting fixes to verify they work correctly
"""

import re

def test_html_cleanup():
    """Test the HTML cleanup logic"""
    
    # Sample HTML with missing closing li tags (like from Bullhorn)
    sample_html = """
    <p><strong>Main Responsibilities:</strong></p>
    <ul>
    <li>Design and development of state-of-the-art electronic devices
    <li>Demonstrated experience working with deep sub-micron silicon technologies 
    <li>Collaboration within multi-site international development teams</li>
    <li>Design of ASIC devices for optical signal processing</li>
    </ul>
    <p><strong>Required Skills:</strong></p>
    <ul>
    <li>Master's degree or Ph.D. in Electronic Engineering
    <li>Minimum of 8 years of proven experience 
    <li>Proficiency in VHDL/Verilog development</li>
    </ul>
    """
    
    print("🔧 Testing HTML cleanup logic...")
    print("Original HTML:")
    print(sample_html)
    
    # Apply the same logic as in _clean_description method
    description = sample_html
    
    # Fix missing closing </li> tags - CRITICAL HTML FORMATTING FIX
    # Simpler approach: Find all <li> that are not followed by </li> before the next <li> or </ul>
    lines = description.split('\n')
    fixed_lines = []
    
    for line in lines:
        line = line.strip()
        if line:
            # If line starts with <li> and doesn't contain </li>, add it
            if line.startswith('<li>') and '</li>' not in line:
                # Check if the next meaningful content is another <li> or </ul>
                line = line + '</li>'
            fixed_lines.append(line)
    
    description = '\n'.join(fixed_lines)
    
    # Clean up any double closing tags that might have been created
    description = re.sub(r'</li>\s*</li>', '</li>', description)
    
    # Remove excessive whitespace but preserve proper line breaks in lists
    description = ' '.join(description.split())
    
    print("\n🎯 Fixed HTML:")
    print(description)
    
    # Count tags
    li_count = description.count('<li>')
    li_close_count = description.count('</li>')
    
    print(f"\n📊 Results:")
    print(f"   <li> tags: {li_count}")
    print(f"   </li> tags: {li_close_count}")
    print(f"   Match: {'✅ YES' if li_count == li_close_count else '❌ NO'}")
    
    return li_count == li_close_count

if __name__ == "__main__":
    success = test_html_cleanup()
    if success:
        print("\n✅ HTML cleanup logic works correctly!")
    else:
        print("\n❌ HTML cleanup logic needs adjustment")