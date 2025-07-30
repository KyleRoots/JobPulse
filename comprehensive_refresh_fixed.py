#!/usr/bin/env python3

import os
import sys
import json
from datetime import datetime
from lxml import etree
from bullhorn_service import BullhornService
from xml_integration_service import XMLIntegrationService
from job_classification_service import JobClassificationService
from ftp_service import FTPService
from app import app, db

def comprehensive_xml_refresh():
    """Perform a comprehensive refresh of the XML file with all Bullhorn jobs"""
    
    with app.app_context():
        try:
            print("🚀 Starting Comprehensive XML Refresh...")
            print(f"   Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
            
            # Initialize services
            bullhorn_service = BullhornService()
            xml_service = XMLIntegrationService()
            classifier = JobClassificationService()
            
            # Get all active monitors directly from database
            from sqlalchemy import text
            result = db.session.execute(text("""
                SELECT id, name, tearsheet_id, tearsheet_name 
                FROM bullhorn_monitor 
                WHERE is_active = true
                ORDER BY name
            """))
            monitors = result.fetchall()
            
            print(f"\n📊 Found {len(monitors)} active monitors")
            
            # Collect all jobs from all monitors
            all_jobs = {}
            total_jobs = 0
            
            for monitor in monitors:
                monitor_id, monitor_name, tearsheet_id, tearsheet_name = monitor
                print(f"\n🔍 Processing monitor: {monitor_name}")
                
                try:
                    # Get jobs from tearsheet
                    if tearsheet_id:
                        jobs = bullhorn_service.get_tearsheet_jobs(tearsheet_id)
                    else:
                        print(f"   ⚠️  Monitor has no tearsheet_id, skipping...")
                        continue
                    
                    print(f"   ✅ Retrieved {len(jobs)} jobs")
                    
                    # Add jobs to collection (de-duplicate by job ID)
                    for job in jobs:
                        job_id = str(job.get('id'))
                        if job_id not in all_jobs:
                            # Associate job with monitor name for company mapping
                            job['_monitor_name'] = monitor_name
                            all_jobs[job_id] = job
                            total_jobs += 1
                    
                except Exception as e:
                    print(f"   ❌ Error processing monitor: {str(e)}")
                    continue
            
            print(f"\n📈 Total unique jobs collected: {total_jobs}")
            
            # Create new XML structure
            print("\n📝 Creating new XML structure...")
            root = etree.Element('source')
            etree.SubElement(root, 'publisher').text = etree.CDATA(' Myticas Consulting Job Site ')
            etree.SubElement(root, 'publisherurl').text = etree.CDATA(' https://myticas.com/ ')
            
            # Track progress
            processed = 0
            ai_errors = 0
            
            print("\n🤖 Processing jobs with AI classifications...")
            
            # Sort jobs by date (newest first)
            sorted_jobs = sorted(all_jobs.values(), 
                               key=lambda x: x.get('dateAdded', 0), 
                               reverse=True)
            
            for job_data in sorted_jobs:
                processed += 1
                job_id = job_data.get('id')
                title = job_data.get('title', '')
                
                if processed % 10 == 0:
                    print(f"   Progress: {processed}/{total_jobs} jobs processed...")
                
                try:
                    # Get AI classifications
                    description = job_data.get('description', '')
                    classifications = classifier.classify_job(title, description)
                    
                    # Map job to XML format
                    monitor_name = job_data.get('_monitor_name', '')
                    xml_job = xml_service.map_bullhorn_job_to_xml(job_data, monitor_name)
                    
                    # Add AI classifications
                    xml_job.update(classifications)
                    
                    # Create job element
                    job_elem = etree.SubElement(root, 'job')
                    
                    # Add all fields in proper order
                    fields = [
                        'title', 'company', 'date', 'referencenumber', 'bhatsid', 'url',
                        'description', 'jobtype', 'city', 'state', 'country', 'category',
                        'apply_email', 'remotetype', 'assignedrecruiter', 'jobfunction',
                        'jobindustries', 'senoritylevel'
                    ]
                    
                    for field in fields:
                        field_elem = etree.SubElement(job_elem, field)
                        value = xml_job.get(field, '')
                        field_elem.text = etree.CDATA(f' {value} ')
                    
                except Exception as e:
                    print(f"   ⚠️  AI classification error for job {job_id}: {str(e)}")
                    ai_errors += 1
                    # Continue with job but without AI classifications
                    try:
                        monitor_name = job_data.get('_monitor_name', '')
                        xml_job = xml_service.map_bullhorn_job_to_xml(job_data, monitor_name)
                        
                        job_elem = etree.SubElement(root, 'job')
                        
                        # Add basic fields without AI
                        basic_fields = [
                            'title', 'company', 'date', 'referencenumber', 'bhatsid', 'url',
                            'description', 'jobtype', 'city', 'state', 'country', 'category',
                            'apply_email', 'remotetype', 'assignedrecruiter'
                        ]
                        
                        for field in basic_fields:
                            field_elem = etree.SubElement(job_elem, field)
                            value = xml_job.get(field, '')
                            field_elem.text = etree.CDATA(f' {value} ')
                        
                        # Add empty AI fields
                        for ai_field in ['jobfunction', 'jobindustries', 'senoritylevel']:
                            field_elem = etree.SubElement(job_elem, ai_field)
                            field_elem.text = etree.CDATA(' ')
                    except Exception as map_error:
                        print(f"   ❌ Failed to map job {job_id}: {str(map_error)}")
                        continue
            
            print(f"\n✅ Processing complete!")
            print(f"   Jobs processed: {processed}")
            print(f"   AI errors: {ai_errors}")
            
            # Save XML files
            print("\n💾 Saving XML files...")
            
            xml_files = ['myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml']
            
            for xml_file in xml_files:
                try:
                    tree = etree.ElementTree(root)
                    tree.write(xml_file, encoding='utf-8', xml_declaration=True, pretty_print=True)
                    
                    file_size = os.path.getsize(xml_file)
                    print(f"   ✅ Saved {xml_file} ({file_size:,} bytes)")
                    
                except Exception as save_error:
                    print(f"   ❌ Error saving {xml_file}: {str(save_error)}")
                    return False
            
            # Upload to SFTP
            print("\n📤 Uploading to SFTP server...")
            
            try:
                # Get SFTP settings from database
                from sqlalchemy import text
                result = db.session.execute(text("""
                    SELECT key, value 
                    FROM global_setting 
                    WHERE category = 'sftp'
                """))
                
                sftp_config = {}
                for row in result:
                    sftp_config[row[0]] = row[1]
                
                if all(k in sftp_config for k in ['hostname', 'username', 'password']):
                    ftp_service = FTPService(
                        hostname=sftp_config['hostname'],
                        username=sftp_config['username'],
                        password=sftp_config['password'],
                        port=int(sftp_config.get('port', 2222))
                    )
                    
                    upload_success = ftp_service.upload_file('myticas-job-feed.xml', 'myticas-job-feed.xml')
                    
                    if upload_success:
                        print("   ✅ Successfully uploaded to SFTP server")
                    else:
                        print("   ❌ SFTP upload failed")
                else:
                    print("   ⚠️  SFTP settings not configured")
                    
            except Exception as ftp_error:
                print(f"   ❌ SFTP error: {str(ftp_error)}")
            
            # Log activity
            db.session.execute(text("""
                INSERT INTO bullhorn_activity (activity_type, details, created_at)
                VALUES (:activity_type, :details, :created_at)
            """), {
                'activity_type': 'xml_refresh_completed',
                'details': f'Comprehensive XML refresh completed. {total_jobs} jobs processed.',
                'created_at': datetime.utcnow()
            })
            db.session.commit()
            
            print("\n🎯 Comprehensive XML refresh completed successfully!")
            print(f"   Total jobs in XML: {total_jobs}")
            print(f"   Files updated: myticas-job-feed.xml, myticas-job-feed-scheduled.xml")
            print(f"   Ready for review!")
            
            return True
            
        except Exception as e:
            print(f"\n❌ Critical error during refresh: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    success = comprehensive_xml_refresh()
    sys.exit(0 if success else 1)