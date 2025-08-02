#!/usr/bin/env python3
"""
Intelligent File Consolidation and Cleanup Service
Manages temporary files, backups, logs, and optimizes storage usage
"""

import os
import glob
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path
import json
import gzip
from typing import List, Dict, Tuple
import hashlib

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FileConsolidationService:
    """Intelligent file management and cleanup service"""
    
    def __init__(self, base_directory='.'):
        self.base_dir = Path(base_directory)
        self.config = {
            'max_backup_age_days': 7,
            'max_temp_age_hours': 24,
            'max_log_age_days': 30,
            'compress_old_files': True,
            'max_duplicate_xml_files': 3,
            'archive_directory': 'archive_consolidated',
        }
        
        # File patterns to manage
        self.file_patterns = {
            'xml_backups': ['*.xml.backup.*', 'myticas-job-feed-*.xml'],
            'temp_files': ['*.tmp', '*.temp', '*-temp.xml', '*-fixed.xml'],
            'log_files': ['*.log', '*.log.*'],
            'upload_files': ['uploads/*'],
            'duplicate_xml': ['myticas-job-feed-rebuilt.xml', 'myticas-job-feed-temp.xml'],
        }
    
    def ensure_directories(self):
        """Create necessary directories"""
        archive_dir = self.base_dir / self.config['archive_directory']
        archive_dir.mkdir(exist_ok=True)
        
        for subdir in ['backups', 'logs', 'temp', 'xml_versions']:
            (archive_dir / subdir).mkdir(exist_ok=True)
    
    def get_file_info(self, filepath: Path) -> Dict:
        """Get comprehensive file information"""
        try:
            stat = filepath.stat()
            return {
                'path': str(filepath),
                'size': stat.st_size,
                'modified': datetime.fromtimestamp(stat.st_mtime),
                'created': datetime.fromtimestamp(stat.st_ctime),
                'age_hours': (datetime.now() - datetime.fromtimestamp(stat.st_mtime)).total_seconds() / 3600,
                'extension': filepath.suffix,
                'is_compressed': filepath.suffix == '.gz'
            }
        except Exception as e:
            logger.error(f"Error getting file info for {filepath}: {e}")
            return {}
    
    def find_duplicate_files(self, pattern: str) -> List[Tuple[str, List[Path]]]:
        """Find duplicate files based on content hash"""
        files = list(self.base_dir.glob(pattern))
        hash_groups = {}
        
        for file_path in files:
            try:
                with open(file_path, 'rb') as f:
                    content_hash = hashlib.md5(f.read()).hexdigest()
                
                if content_hash not in hash_groups:
                    hash_groups[content_hash] = []
                hash_groups[content_hash].append(file_path)
                
            except Exception as e:
                logger.warning(f"Could not hash {file_path}: {e}")
        
        # Return groups with duplicates
        return [(hash_val, paths) for hash_val, paths in hash_groups.items() if len(paths) > 1]
    
    def compress_file(self, filepath: Path) -> Path:
        """Compress a file using gzip"""
        compressed_path = filepath.with_suffix(filepath.suffix + '.gz')
        
        try:
            with open(filepath, 'rb') as f_in:
                with gzip.open(compressed_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            
            # Remove original file after successful compression
            filepath.unlink()
            logger.info(f"Compressed {filepath} -> {compressed_path}")
            return compressed_path
            
        except Exception as e:
            logger.error(f"Failed to compress {filepath}: {e}")
            if compressed_path.exists():
                compressed_path.unlink()
            return filepath
    
    def cleanup_xml_backups(self) -> Dict[str, int]:
        """Clean up old XML backup files"""
        results = {'removed': 0, 'compressed': 0, 'archived': 0}
        cutoff_date = datetime.now() - timedelta(days=self.config['max_backup_age_days'])
        
        # Find all backup files
        backup_patterns = ['*.xml.backup.*', 'myticas-job-feed-*.xml']
        backup_files = []
        
        for pattern in backup_patterns:
            backup_files.extend(self.base_dir.glob(pattern))
        
        # Exclude main XML files
        main_files = {'myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml'}
        backup_files = [f for f in backup_files if f.name not in main_files]
        
        for backup_file in backup_files:
            file_info = self.get_file_info(backup_file)
            if not file_info:
                continue
            
            if file_info['modified'] < cutoff_date:
                if file_info['age_hours'] > 168:  # 1 week
                    # Very old files - remove completely
                    backup_file.unlink()
                    results['removed'] += 1
                    logger.info(f"Removed old backup: {backup_file}")
                else:
                    # Archive and compress
                    archive_path = self.base_dir / self.config['archive_directory'] / 'backups' / backup_file.name
                    shutil.move(str(backup_file), str(archive_path))
                    if self.config['compress_old_files']:
                        self.compress_file(archive_path)
                        results['compressed'] += 1
                    results['archived'] += 1
                    logger.info(f"Archived backup: {backup_file}")
        
        return results
    
    def cleanup_temp_files(self) -> Dict[str, int]:
        """Clean up temporary files"""
        results = {'removed': 0, 'total_size_mb': 0}
        cutoff_date = datetime.now() - timedelta(hours=self.config['max_temp_age_hours'])
        
        temp_patterns = ['*.tmp', '*.temp', '*-temp.xml', '*-fixed.xml', '*-cdata.xml']
        
        for pattern in temp_patterns:
            for temp_file in self.base_dir.glob(pattern):
                file_info = self.get_file_info(temp_file)
                if not file_info:
                    continue
                
                if file_info['modified'] < cutoff_date:
                    size_mb = file_info['size'] / (1024 * 1024)
                    temp_file.unlink()
                    results['removed'] += 1
                    results['total_size_mb'] += size_mb
                    logger.info(f"Removed temp file: {temp_file} ({size_mb:.2f}MB)")
        
        return results
    
    def cleanup_duplicate_xml_files(self) -> Dict[str, int]:
        """Remove duplicate XML files and keep only the most recent"""
        results = {'removed': 0, 'kept': 0}
        
        # Find duplicate XML files based on content
        xml_patterns = ['myticas-job-feed*.xml']
        
        for pattern in xml_patterns:
            duplicates = self.find_duplicate_files(pattern)
            
            for content_hash, file_paths in duplicates:
                if len(file_paths) <= 1:
                    continue
                
                # Sort by modification time (newest first)
                file_paths.sort(key=lambda f: self.get_file_info(f)['modified'], reverse=True)
                
                # Keep the newest file and main files
                files_to_keep = []
                main_files = {'myticas-job-feed.xml', 'myticas-job-feed-scheduled.xml'}
                
                for file_path in file_paths:
                    if file_path.name in main_files:
                        files_to_keep.append(file_path)
                    elif len(files_to_keep) < self.config['max_duplicate_xml_files']:
                        files_to_keep.append(file_path)
                
                # Remove the rest
                for file_path in file_paths:
                    if file_path not in files_to_keep:
                        file_path.unlink()
                        results['removed'] += 1
                        logger.info(f"Removed duplicate XML: {file_path}")
                
                results['kept'] += len(files_to_keep)
        
        return results
    
    def cleanup_upload_directory(self) -> Dict[str, int]:
        """Clean up old uploaded files"""
        results = {'removed': 0, 'total_size_mb': 0}
        uploads_dir = self.base_dir / 'uploads'
        
        if not uploads_dir.exists():
            return results
        
        cutoff_date = datetime.now() - timedelta(days=1)  # Remove uploads older than 1 day
        
        for upload_file in uploads_dir.glob('*'):
            if upload_file.is_file():
                file_info = self.get_file_info(upload_file)
                if not file_info:
                    continue
                
                if file_info['modified'] < cutoff_date:
                    size_mb = file_info['size'] / (1024 * 1024)
                    upload_file.unlink()
                    results['removed'] += 1
                    results['total_size_mb'] += size_mb
                    logger.info(f"Removed upload: {upload_file} ({size_mb:.2f}MB)")
        
        return results
    
    def generate_cleanup_report(self) -> Dict:
        """Generate comprehensive cleanup report"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'total_files_scanned': 0,
            'total_size_mb': 0,
            'categories': {}
        }
        
        # Scan all files for overview
        all_files = list(self.base_dir.rglob('*'))
        all_files = [f for f in all_files if f.is_file()]
        
        report['total_files_scanned'] = len(all_files)
        
        total_size = sum(f.stat().st_size for f in all_files if f.exists())
        report['total_size_mb'] = total_size / (1024 * 1024)
        
        # Categorize files
        categories = {
            'xml_files': 0,
            'backup_files': 0,
            'temp_files': 0,
            'log_files': 0,
            'python_files': 0,
            'other_files': 0
        }
        
        for file_path in all_files:
            if file_path.suffix == '.xml':
                categories['xml_files'] += 1
            elif 'backup' in file_path.name or file_path.suffix in ['.bak', '.backup']:
                categories['backup_files'] += 1
            elif file_path.suffix in ['.tmp', '.temp'] or 'temp' in file_path.name:
                categories['temp_files'] += 1
            elif file_path.suffix in ['.log']:
                categories['log_files'] += 1
            elif file_path.suffix == '.py':
                categories['python_files'] += 1
            else:
                categories['other_files'] += 1
        
        report['categories'] = categories
        return report
    
    def run_full_cleanup(self) -> Dict:
        """Run complete file consolidation and cleanup"""
        logger.info("=== STARTING INTELLIGENT FILE CONSOLIDATION ===")
        
        self.ensure_directories()
        
        cleanup_results = {
            'started_at': datetime.now().isoformat(),
            'xml_backups': {},
            'temp_files': {},
            'duplicate_xml': {},
            'upload_files': {},
            'summary': {}
        }
        
        # Run cleanup operations
        try:
            cleanup_results['xml_backups'] = self.cleanup_xml_backups()
            cleanup_results['temp_files'] = self.cleanup_temp_files()
            cleanup_results['duplicate_xml'] = self.cleanup_duplicate_xml_files()
            cleanup_results['upload_files'] = self.cleanup_upload_directory()
            
            # Generate summary
            total_removed = sum([
                cleanup_results['xml_backups'].get('removed', 0),
                cleanup_results['temp_files'].get('removed', 0),
                cleanup_results['duplicate_xml'].get('removed', 0),
                cleanup_results['upload_files'].get('removed', 0)
            ])
            
            total_size_freed = sum([
                cleanup_results['temp_files'].get('total_size_mb', 0),
                cleanup_results['upload_files'].get('total_size_mb', 0)
            ])
            
            cleanup_results['summary'] = {
                'total_files_removed': total_removed,
                'total_size_freed_mb': round(total_size_freed, 2),
                'xml_backups_archived': cleanup_results['xml_backups'].get('archived', 0),
                'files_compressed': cleanup_results['xml_backups'].get('compressed', 0),
                'duplicate_xml_kept': cleanup_results['duplicate_xml'].get('kept', 0)
            }
            
            cleanup_results['completed_at'] = datetime.now().isoformat()
            
            logger.info("=== FILE CONSOLIDATION COMPLETE ===")
            logger.info(f"✅ Removed {total_removed} files")
            logger.info(f"✅ Freed {total_size_freed:.2f}MB storage")
            logger.info(f"✅ Archived {cleanup_results['xml_backups'].get('archived', 0)} backups")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            cleanup_results['error'] = str(e)
        
        return cleanup_results
    
    def schedule_cleanup_report(self) -> str:
        """Generate a report for scheduled cleanup"""
        report = self.generate_cleanup_report()
        report_file = self.base_dir / f"cleanup_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"Cleanup report saved: {report_file}")
        return str(report_file)

def main():
    """Command line interface"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Intelligent File Consolidation Service')
    parser.add_argument('--cleanup', action='store_true', help='Run full cleanup')
    parser.add_argument('--report', action='store_true', help='Generate cleanup report')
    parser.add_argument('--xml-backups', action='store_true', help='Clean XML backups only')
    parser.add_argument('--temp-files', action='store_true', help='Clean temp files only')
    parser.add_argument('--duplicates', action='store_true', help='Remove duplicate XML files only')
    
    args = parser.parse_args()
    
    consolidation = FileConsolidationService()
    
    if args.cleanup or not any([args.report, args.xml_backups, args.temp_files, args.duplicates]):
        results = consolidation.run_full_cleanup()
        print(json.dumps(results, indent=2))
    
    elif args.report:
        report_file = consolidation.schedule_cleanup_report()
        print(f"Report generated: {report_file}")
    
    elif args.xml_backups:
        results = consolidation.cleanup_xml_backups()
        print(f"XML backups cleanup: {results}")
    
    elif args.temp_files:
        results = consolidation.cleanup_temp_files()
        print(f"Temp files cleanup: {results}")
    
    elif args.duplicates:
        results = consolidation.cleanup_duplicate_xml_files()
        print(f"Duplicate XML cleanup: {results}")

if __name__ == "__main__":
    main()