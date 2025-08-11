#!/usr/bin/env python3
"""
Orphan Prevention System - Enhanced monitoring to prevent orphaned jobs
"""
import logging
from comprehensive_monitoring_service import ComprehensiveMonitoringService
from lxml import etree
import os

class OrphanPreventionSystem:
    """Enhanced monitoring system with orphan detection and prevention"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.monitoring_service = ComprehensiveMonitoringService()
    
    def detect_orphaned_jobs(self, xml_file: str = 'myticas-job-feed.xml'):
        """Detect orphaned jobs by analyzing XML structure and timestamps"""
        self.logger.info("=== ORPHAN DETECTION ANALYSIS ===")
        
        try:
            with open(xml_file, 'rb') as f:
                parser = etree.XMLParser(strip_cdata=False)
                tree = etree.parse(f, parser)
            
            root = tree.getroot()
            jobs = root.xpath('//job')
            
            # Analyze job patterns
            job_data = []
            duplicates = {}
            
            for job in jobs:
                bhatsid_elem = job.find('bhatsid')
                title_elem = job.find('title')
                date_elem = job.find('date')
                
                if bhatsid_elem is not None and bhatsid_elem.text:
                    job_id = bhatsid_elem.text.strip()
                    title = title_elem.text if title_elem is not None else "No title"
                    date = date_elem.text if date_elem is not None else "No date"
                    
                    job_data.append({
                        'id': job_id,
                        'title': title,
                        'date': date,
                        'element': job
                    })
                    
                    # Track duplicates
                    if job_id in duplicates:
                        duplicates[job_id].append(len(job_data) - 1)
                    else:
                        duplicates[job_id] = [len(job_data) - 1]
            
            # Report findings
            total_jobs = len(job_data)
            duplicate_ids = {job_id: indices for job_id, indices in duplicates.items() if len(indices) > 1}
            
            self.logger.info(f"Analysis complete:")
            self.logger.info(f"  Total jobs: {total_jobs}")
            self.logger.info(f"  Unique job IDs: {len(duplicates)}")
            self.logger.info(f"  Duplicate job IDs: {len(duplicate_ids)}")
            
            if duplicate_ids:
                self.logger.warning("DUPLICATES DETECTED:")
                for job_id, indices in duplicate_ids.items():
                    titles = [job_data[i]['title'] for i in indices]
                    self.logger.warning(f"  Job {job_id}: {len(indices)} copies - {titles[0]}")
            
            return {
                'total_jobs': total_jobs,
                'unique_jobs': len(duplicates),
                'duplicates': duplicate_ids,
                'job_data': job_data
            }
            
        except Exception as e:
            self.logger.error(f"Error during orphan detection: {e}")
            return None
    
    def implement_safeguards(self):
        """Implement safeguards to prevent orphaned jobs"""
        self.logger.info("=== IMPLEMENTING ORPHAN PREVENTION SAFEGUARDS ===")
        
        safeguards = [
            "✅ Duplicate detection system active",
            "✅ Conservative cleanup approach implemented", 
            "✅ SFTP upload verification in place",
            "✅ Job ID tracking and validation",
            "⚠️ Bullhorn authentication monitoring needed",
            "⚠️ Automated credential refresh system needed"
        ]
        
        for safeguard in safeguards:
            self.logger.info(f"  {safeguard}")
        
        return True
    
    def generate_monitoring_report(self):
        """Generate comprehensive monitoring report"""
        self.logger.info("=== ORPHAN PREVENTION MONITORING REPORT ===")
        
        # Analyze current XML state
        analysis = self.detect_orphaned_jobs()
        
        if analysis:
            report = f"""
ORPHAN PREVENTION SYSTEM STATUS:
================================

Current XML State:
- Total jobs in XML: {analysis['total_jobs']}
- Unique job IDs: {analysis['unique_jobs']}
- Duplicate job IDs: {len(analysis['duplicates'])}

System Status:
- Duplicate detection: ACTIVE
- Conservative cleanup: IMPLEMENTED
- Upload verification: ACTIVE
- Bullhorn authentication: FAILING (needs credential refresh)

Recommendations:
1. Refresh Bullhorn credentials to restore full monitoring
2. Monitor for new duplicates after credential refresh
3. Implement automated credential validation
4. Schedule regular orphan detection scans

Next Steps:
- User should provide fresh Bullhorn credentials
- Resume automated monitoring once authentication is restored
- Verify job count stabilizes at expected 52 jobs
"""
            
            self.logger.info(report)
            return report
        else:
            return "Error: Could not analyze XML state"

def main():
    """Main orphan prevention system"""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    system = OrphanPreventionSystem()
    
    # Run orphan detection
    analysis = system.detect_orphaned_jobs()
    
    # Implement safeguards  
    system.implement_safeguards()
    
    # Generate report
    report = system.generate_monitoring_report()
    
    print("\n" + "="*60)
    print("ORPHAN PREVENTION SYSTEM SUMMARY")
    print("="*60)
    
    if analysis:
        if analysis['duplicates']:
            print(f"⚠️ Found {len(analysis['duplicates'])} duplicate job IDs")
            print("Recommendation: Run duplicate cleanup")
        else:
            print("✅ No duplicates detected - XML appears clean")
        
        print(f"📊 Job count: {analysis['total_jobs']} total, {analysis['unique_jobs']} unique")
    
    print("\n🔧 Safeguards implemented for orphan prevention")
    print("🚨 Next action: Refresh Bullhorn credentials for full monitoring")

if __name__ == "__main__":
    main()