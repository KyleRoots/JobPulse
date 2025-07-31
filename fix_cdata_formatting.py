#!/usr/bin/env python3
"""
Fix CDATA Formatting
====================
This script fixes the HTML-encoded CDATA tags in the XML file to proper CDATA format
"""

def fix_cdata_formatting():
    """Fix HTML-encoded CDATA tags to proper CDATA format"""
    
    print("🔧 FIXING CDATA FORMATTING")
    print("=" * 40)
    
    # Read the XML file
    with open('myticas-job-feed.xml', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Fix HTML-encoded CDATA tags
    content = content.replace('&lt;![CDATA[', '<![CDATA[')
    content = content.replace(']]&gt;', ']]>')
    content = content.replace('&amp;', '&')
    
    # Write back to file
    with open('myticas-job-feed.xml', 'w', encoding='utf-8') as f:
        f.write(content)
    
    # Also fix scheduled file
    try:
        with open('myticas-job-feed-scheduled.xml', 'r', encoding='utf-8') as f:
            scheduled_content = f.read()
        
        scheduled_content = scheduled_content.replace('&lt;![CDATA[', '<![CDATA[')
        scheduled_content = scheduled_content.replace(']]&gt;', ']]>')
        scheduled_content = scheduled_content.replace('&amp;', '&')
        
        with open('myticas-job-feed-scheduled.xml', 'w', encoding='utf-8') as f:
            f.write(scheduled_content)
        
        print("✅ Both XML files fixed")
    except FileNotFoundError:
        print("✅ Main XML file fixed")
    
    print("✅ CDATA formatting restored")

if __name__ == "__main__":
    fix_cdata_formatting()