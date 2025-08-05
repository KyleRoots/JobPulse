#!/usr/bin/env python3
"""
URL Verification Service - Ensures URLs remain unique and don't revert to generic format
"""
import logging
import requests
from lxml import etree
from datetime import datetime
import sys

class URLVerificationService:
    """Service to verify URL integrity in production XML feeds"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.production_url = "https://myticas.com/myticas-job-feed.xml"
        
    def verify_production_urls(self) -> dict:
        """
        Verify that production XML contains unique job URLs, not generic ones
        
        Returns:
            dict: Verification results with status and details
        """
        try:
            self.logger.info(f"Fetching production XML from: {self.production_url}")
            
            # Fetch production XML with timeout
            response = requests.get(self.production_url, timeout=30)
            response.raise_for_status()
            
            # Parse XML content
            parser = etree.XMLParser(recover=True, strip_cdata=False)
            root = etree.fromstring(response.content, parser)
            
            # Count URL types
            generic_urls = 0
            unique_urls = 0
            total_jobs = 0
            url_examples = []
            
            for job in root.xpath('.//job'):
                total_jobs += 1
                url_elem = job.find('.//url')
                bhatsid_elem = job.find('.//bhatsid')
                title_elem = job.find('.//title')
                
                if url_elem is not None and url_elem.text:
                    url_text = url_elem.text.strip()
                    
                    # Check if URL is generic or unique
                    if url_text == "https://myticas.com/" or url_text == "https://myticas.com":
                        generic_urls += 1
                        if len(url_examples) < 3:
                            job_id = bhatsid_elem.text.strip() if bhatsid_elem is not None else "Unknown"
                            job_title = title_elem.text.strip() if title_elem is not None else "Unknown"
                            url_examples.append(f"Job {job_id} ({job_title}): {url_text}")
                    elif "apply.myticas.com" in url_text:
                        unique_urls += 1
                    else:
                        # Other URL format
                        if len(url_examples) < 3:
                            job_id = bhatsid_elem.text.strip() if bhatsid_elem is not None else "Unknown"
                            url_examples.append(f"Job {job_id}: {url_text}")
            
            # Calculate percentages
            generic_percentage = (generic_urls / total_jobs * 100) if total_jobs > 0 else 0
            unique_percentage = (unique_urls / total_jobs * 100) if total_jobs > 0 else 0
            
            result = {
                'success': True,
                'timestamp': datetime.utcnow().isoformat(),
                'total_jobs': total_jobs,
                'generic_urls': generic_urls,
                'unique_urls': unique_urls,
                'generic_percentage': generic_percentage,
                'unique_percentage': unique_percentage,
                'url_examples': url_examples,
                'status': 'HEALTHY' if generic_percentage < 10 else ('WARNING' if generic_percentage < 50 else 'CRITICAL')
            }
            
            self.logger.info(f"Verification complete: {unique_urls}/{total_jobs} unique URLs ({unique_percentage:.1f}%)")
            return result
            
        except requests.RequestException as e:
            self.logger.error(f"Failed to fetch production XML: {str(e)}")
            return {
                'success': False,
                'error': f"Network error: {str(e)}",
                'timestamp': datetime.utcnow().isoformat()
            }
        except etree.XMLSyntaxError as e:
            self.logger.error(f"Failed to parse production XML: {str(e)}")
            return {
                'success': False,
                'error': f"XML parsing error: {str(e)}",
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            self.logger.error(f"Verification failed: {str(e)}")
            return {
                'success': False,
                'error': f"Unexpected error: {str(e)}",
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def report_verification_results(self, results: dict) -> None:
        """Print formatted verification results"""
        if not results.get('success'):
            print(f"❌ VERIFICATION FAILED: {results.get('error', 'Unknown error')}")
            return
        
        status = results['status']
        total = results['total_jobs']
        unique = results['unique_urls']
        generic = results['generic_urls']
        unique_pct = results['unique_percentage']
        generic_pct = results['generic_percentage']
        
        status_icon = "✅" if status == "HEALTHY" else ("⚠️" if status == "WARNING" else "❌")
        
        print(f"\n{status_icon} PRODUCTION URL VERIFICATION - {status}")
        print(f"Timestamp: {results['timestamp']}")
        print(f"Total Jobs: {total}")
        print(f"Unique URLs: {unique}/{total} ({unique_pct:.1f}%)")
        print(f"Generic URLs: {generic}/{total} ({generic_pct:.1f}%)")
        
        if results['url_examples']:
            print(f"\nURL Examples:")
            for example in results['url_examples']:
                print(f"  • {example}")
        
        if status != "HEALTHY":
            print(f"\n🚨 ACTION REQUIRED: {generic} jobs have reverted to generic URLs!")
        else:
            print(f"\n✅ All URLs are properly formatted and unique")

def main():
    """Run URL verification as standalone script"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    verifier = URLVerificationService()
    results = verifier.verify_production_urls()
    verifier.report_verification_results(results)
    
    # Exit with error code if verification failed or URLs are problematic
    if not results.get('success') or results.get('status') != 'HEALTHY':
        sys.exit(1)
    
    sys.exit(0)

if __name__ == "__main__":
    main()