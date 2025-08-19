import re

# Read the XML file
with open('myticas-job-feed-CORRECT-1755627190.xml', 'r', encoding='utf-8') as f:
    content = f.read()

# List of ALL fields that should have CDATA
cdata_fields = [
    'publisher', 'publisherurl', 'title', 'company', 'date', 
    'referencenumber', 'bhatsid', 'url', 'description', 'jobtype',
    'city', 'state', 'country', 'apply_email', 'remotetype',
    'assignedrecruiter', 'jobfunction', 'jobindustries', 'senioritylevel'
]

fixed_count = 0
for field in cdata_fields:
    # Pattern to find fields without CDATA
    pattern = f'<{field}>(?!<!\\[CDATA\\[)(.*?)</{field}>'
    
    # Find all matches
    matches = re.findall(pattern, content, re.DOTALL)
    
    if matches:
        print(f"Found {len(matches)} {field} fields without CDATA")
        # Replace with CDATA wrapped version
        content = re.sub(
            pattern,
            f'<{field}><![CDATA[\\1]]></{field}>',
            content,
            flags=re.DOTALL
        )
        fixed_count += len(matches)

# Save the fixed file
with open('myticas-job-feed-CORRECT-1755627190.xml', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"\n✅ Fixed {fixed_count} fields with missing CDATA")

# Verify the fix
with open('myticas-job-feed-CORRECT-1755627190.xml', 'r', encoding='utf-8') as f:
    content = f.read()
    cdata_count = content.count('<![CDATA[')
    print(f"✅ Total CDATA sections now: {cdata_count}")
