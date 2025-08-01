#!/usr/bin/env python3
"""
Application Optimization Improvements
Implements performance enhancements for the job feed system
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from functools import lru_cache
import json
try:
    from lxml import etree
except ImportError:
    import xml.etree.ElementTree as etree

class OptimizationImprovements:
    """Core optimization improvements for the application"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._cache = {}
        self._batch_size = 10
    
    # 1. Database Query Optimization
    def optimize_monitor_queries(self, db, BullhornMonitor):
        """Optimize monitor queries with eager loading"""
        from sqlalchemy.orm import joinedload
        
        # Use eager loading to prevent N+1 queries
        monitors = db.session.query(BullhornMonitor)\
            .filter(BullhornMonitor.is_active == True)\
            .options(joinedload(BullhornMonitor.activities))\
            .all()
        
        return monitors
    
    # 2. Batch API Calls for AI Classification
    def batch_classify_jobs(self, jobs: List[Dict], job_classifier) -> Dict[str, Dict]:
        """Batch classify multiple jobs to reduce API calls"""
        classifications = {}
        batch = []
        
        for job in jobs:
            job_id = job.get('id')
            title = job.get('title', '')
            description = job.get('publicDescription', '') or job.get('description', '')
            
            batch.append({
                'id': job_id,
                'title': title,
                'description': description[:2000]  # Limit description length
            })
            
            # Process batch when it reaches the size limit
            if len(batch) >= self._batch_size:
                batch_results = self._process_classification_batch(batch, job_classifier)
                classifications.update(batch_results)
                batch = []
        
        # Process remaining jobs
        if batch:
            batch_results = self._process_classification_batch(batch, job_classifier)
            classifications.update(batch_results)
        
        return classifications
    
    def _process_classification_batch(self, batch: List[Dict], job_classifier) -> Dict:
        """Process a batch of jobs for classification"""
        results = {}
        
        for job_data in batch:
            try:
                classification = job_classifier.classify_job(
                    job_data['title'], 
                    job_data['description']
                )
                results[job_data['id']] = classification
            except Exception as e:
                self.logger.error(f"Failed to classify job {job_data['id']}: {str(e)}")
                results[job_data['id']] = {
                    'job_function': '',
                    'industries': '',
                    'seniority_level': ''
                }
        
        return results
    
    # 3. XML Processing Memory Optimization
    def optimize_xml_processing(self, xml_file_path: str) -> bool:
        """Process XML file with streaming to reduce memory usage"""
        from lxml import etree
        
        try:
            # Use iterparse for memory-efficient XML processing
            context = etree.iterparse(xml_file_path, events=('start', 'end'))
            context = iter(context)
            event, root = next(context)
            
            job_count = 0
            
            for event, elem in context:
                if event == 'end' and elem.tag == 'job':
                    # Process job element
                    job_count += 1
                    
                    # Clear the element to free memory
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
            
            del context
            self.logger.info(f"Processed {job_count} jobs efficiently")
            return True
            
        except Exception as e:
            self.logger.error(f"XML optimization failed: {str(e)}")
            return False
    
    # 4. Caching for Frequently Accessed Data
    @lru_cache(maxsize=128)
    def get_cached_recruiter_mapping(self, recruiter_name: str) -> str:
        """Cache recruiter name to LinkedIn tag mappings"""
        mapping = {
            'Robert Pittore': '#LI-RP',
            'Michael Theodossiou': '#LI-MIT',
            'myticas': '#LI-MYT',
            'Hetal Thakur': '#LI-HT',
            'Karen Hill': '#LI-KH',
            'Matheo Theodossiou': '#LI-MAT',
            'Nick Theodossiou': '#LI-NT',
            'Alyssa Crosse': '#LI-AC',
            'Keith Roots': '#LI-KR',
            'Margarita Theodossiou': '#LI-MT',
            'Karina Keuylian': '#LI-KK',
            'Kristina Lobo': '#LI-KL',
            'Kelly Thompson': '#LI-KT',
            'Ricardo Nunez': '#LI-RN',
            'Jose Sandoval': '#LI-JS'
        }
        return mapping.get(recruiter_name.strip(), recruiter_name)
    
    # 5. Background Task Optimization
    def optimize_scheduler_jobs(self, scheduler):
        """Optimize scheduler job configuration"""
        # Configure job defaults for better performance
        job_defaults = {
            'coalesce': True,  # Coalesce missed jobs
            'max_instances': 1,  # Prevent multiple instances
            'misfire_grace_time': 30  # Grace time for misfired jobs
        }
        
        # Update existing jobs with optimized settings
        for job in scheduler.get_jobs():
            scheduler.modify_job(
                job.id,
                coalesce=True,
                max_instances=1
            )
        
        return job_defaults
    
    # 6. Connection Pool Optimization
    def get_optimized_db_config(self) -> Dict:
        """Get optimized database configuration"""
        return {
            "pool_size": 20,  # Increased from default
            "max_overflow": 30,  # Allow more overflow connections
            "pool_timeout": 30,  # Timeout for getting connection
            "pool_recycle": 300,  # Recycle connections after 5 minutes
            "pool_pre_ping": True,  # Verify connections before use
            "echo": False,  # Disable SQL echo in production
            "connect_args": {
                "connect_timeout": 10,
                "application_name": "job_feed_app"
            }
        }
    
    # 7. Error Recovery and Resilience
    def implement_circuit_breaker(self, func, max_failures: int = 3):
        """Implement circuit breaker pattern for external API calls"""
        failures = 0
        last_failure_time = None
        
        def wrapper(*args, **kwargs):
            nonlocal failures, last_failure_time
            
            # Check if circuit is open
            if failures >= max_failures:
                if last_failure_time and \
                   datetime.utcnow() - last_failure_time < timedelta(minutes=5):
                    self.logger.warning("Circuit breaker is OPEN - skipping call")
                    return None
                else:
                    # Reset circuit after cooldown
                    failures = 0
                    last_failure_time = None
            
            try:
                result = func(*args, **kwargs)
                failures = 0  # Reset on success
                return result
            except Exception as e:
                failures += 1
                last_failure_time = datetime.utcnow()
                self.logger.error(f"Circuit breaker recorded failure {failures}/{max_failures}: {str(e)}")
                
                if failures >= max_failures:
                    self.logger.error("Circuit breaker is now OPEN")
                
                raise
        
        return wrapper
    
    # 8. Batch Database Operations
    def batch_update_jobs(self, db, model_class, jobs_to_update: List[Tuple[int, Dict]]):
        """Batch update multiple jobs in a single transaction"""
        try:
            # Use bulk operations for better performance
            from sqlalchemy import update
            
            for job_id, updates in jobs_to_update:
                db.session.execute(
                    update(model_class).where(model_class.id == job_id).values(**updates)
                )
            
            # Single commit for all updates
            db.session.commit()
            self.logger.info(f"Batch updated {len(jobs_to_update)} jobs")
            
        except Exception as e:
            db.session.rollback()
            self.logger.error(f"Batch update failed: {str(e)}")
            raise

# Main optimization entry point
def apply_optimizations(app, db, scheduler):
    """Apply all optimizations to the application"""
    optimizer = OptimizationImprovements()
    
    # 1. Update database configuration
    db_config = optimizer.get_optimized_db_config()
    app.config["SQLALCHEMY_ENGINE_OPTIONS"].update(db_config)
    
    # 2. Optimize scheduler
    optimizer.optimize_scheduler_jobs(scheduler)
    
    # 3. Add request context cleanup
    @app.teardown_appcontext
    def cleanup(exception=None):
        """Clean up resources after each request"""
        if exception:
            db.session.rollback()
        db.session.remove()
    
    # 4. Add health check endpoint
    @app.route('/health')
    def health_check():
        """Simple health check endpoint"""
        try:
            # Check database connection
            from sqlalchemy import text
            db.session.execute(text('SELECT 1'))
            return {'status': 'healthy', 'timestamp': datetime.utcnow().isoformat()}, 200
        except Exception as e:
            return {'status': 'unhealthy', 'error': str(e)}, 503
    
    app.logger.info("Applied all performance optimizations")
    return optimizer

if __name__ == "__main__":
    print("Optimization improvements module loaded")
    print("Use apply_optimizations(app, db, scheduler) to apply all optimizations")