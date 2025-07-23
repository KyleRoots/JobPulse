import re
from collections import OrderedDict

def clean_xml_file(input_file, output_file):
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Parse jobs manually to preserve CDATA formatting
    jobs = re.findall(r'<job>.*?</job>', content, re.DOTALL)
    print(f"Found {len(jobs)} total jobs")
    
    # Extract unique jobs by ID
    unique_jobs = OrderedDict()
    
    for job in jobs:
        # Extract bhatsid
        bhatsid_match = re.search(r'<bhatsid>.*?(\d+).*?</bhatsid>', job)
        if bhatsid_match:
            job_id = bhatsid_match.group(1).strip()
            
            # Only keep the first occurrence of each job
            if job_id not in unique_jobs:
                # Fix CDATA formatting for all fields
                fixed_job = job
                
                # Fix each field to ensure proper CDATA formatting
                fields = ['title', 'company', 'date', 'referencenumber', 'bhatsid', 'url', 
                         'description', 'jobtype', 'city', 'state', 'country', 'category', 
                         'apply_email', 'remotetype', 'assignedrecruiter']
                
                for field in fields:
                    # Find the field content
                    field_pattern = f'<{field}>(.*?)</{field}>'
                    field_match = re.search(field_pattern, fixed_job, re.DOTALL)
                    
                    if field_match:
                        content_text = field_match.group(1).strip()
                        
                        # Remove existing CDATA markers if present
                        if content_text.startswith('<![CDATA[') and content_text.endswith(']]>'):
                            content_text = content_text[9:-3].strip()
                        
                        # For description, ensure HTML entities are converted
                        if field == 'description':
                            content_text = content_text.replace('&lt;', '<')
                            content_text = content_text.replace('&gt;', '>')
                            content_text = content_text.replace('&amp;', '&')
                            content_text = content_text.replace('&quot;', '"')
                            content_text = content_text.replace('&apos;', "'")
                        
                        # Replace with proper CDATA format
                        fixed_job = re.sub(
                            field_pattern,
                            f'<{field}><![CDATA[ {content_text} ]]></{field}>',
                            fixed_job,
                            flags=re.DOTALL
                        )
                
                unique_jobs[job_id] = fixed_job
    
    print(f"Kept {len(unique_jobs)} unique jobs")
    
    # Build the cleaned XML
    cleaned_xml = '''<?xml version='1.0' encoding='UTF-8'?>
<source>
  <publisher>Myticas Consulting Job Site</publisher>
  <publisherurl>https://myticas.com/</publisherurl>
'''
    
    for job in unique_jobs.values():
        # Ensure proper indentation
        indented_job = '\n'.join('  ' + line if line.strip() else line 
                                for line in job.strip().split('\n'))
        cleaned_xml += indented_job + '\n'
    
    cleaned_xml += '</source>'
    
    # Write the cleaned file
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(cleaned_xml)
    
    return len(unique_jobs)

# Clean the file
num_jobs = clean_xml_file('temp_xml_to_analyze.xml', 'myticas-job-feed-dice.xml')
print(f"\nCreated cleaned XML with {num_jobs} unique jobs")