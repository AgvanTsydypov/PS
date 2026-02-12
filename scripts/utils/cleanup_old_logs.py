"""
Cleanup Old Logs and Output Files

Automatically removes old log files and temporary output files
to prevent disk space issues.

USAGE:
======
Run manually:
    python scripts/utils/cleanup_old_logs.py

Run with custom retention:
    python scripts/utils/cleanup_old_logs.py --keep-days 7

Dry run (see what would be deleted):
    python scripts/utils/cleanup_old_logs.py --dry-run

Docker:
    docker exec polystars_scheduler python /app/scripts/utils/cleanup_old_logs.py
"""

import os
import sys
import argparse
import glob
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class LogCleaner:
    """Manages cleanup of old log and output files"""
    
    def __init__(self, keep_days: int = 14, dry_run: bool = False):
        """
        Args:
            keep_days: Number of days to keep files (default: 14)
            dry_run: If True, only show what would be deleted
        """
        self.keep_days = keep_days
        self.dry_run = dry_run
        self.cutoff_date = datetime.now() - timedelta(days=keep_days)
        
        # Directories and patterns to clean
        self.cleanup_patterns = [
            # Log files from fetch scripts
            ('logs', 'redemptions_fetch_*.log'),
            ('logs', 'events_fetch_*.log'),
            ('logs', 'positions_fetch_*.log'),
            ('logs', 'leaderboard_fetch_*.log'),
            
            # Failed markets/redemptions JSON files
            ('output', 'failed_markets_*.json'),
            ('output', 'failed_redemptions_*.json'),
            
            # Old event snapshots (optional - uncomment if needed)
            # ('data/json_output', 'polymarket_events_*.json'),
        ]
    
    def get_file_age_days(self, filepath: str) -> float:
        """Get file age in days"""
        mtime = os.path.getmtime(filepath)
        file_date = datetime.fromtimestamp(mtime)
        age = datetime.now() - file_date
        return age.total_seconds() / 86400  # Convert to days
    
    def find_old_files(self) -> List[Tuple[str, float, int]]:
        """
        Find all files older than cutoff date
        
        Returns:
            List of (filepath, age_days, size_bytes) tuples
        """
        old_files = []
        
        for directory, pattern in self.cleanup_patterns:
            full_pattern = os.path.join(project_root, directory, pattern)
            
            for filepath in glob.glob(full_pattern):
                try:
                    age_days = self.get_file_age_days(filepath)
                    
                    if age_days > self.keep_days:
                        size_bytes = os.path.getsize(filepath)
                        old_files.append((filepath, age_days, size_bytes))
                
                except Exception as e:
                    print(f"⚠️  Error checking {filepath}: {e}")
        
        # Sort by age (oldest first)
        old_files.sort(key=lambda x: x[1], reverse=True)
        return old_files
    
    def format_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f}{unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f}TB"
    
    def cleanup(self):
        """Run cleanup process"""
        print("\n" + "="*70)
        print("🧹 LOG AND OUTPUT FILE CLEANUP")
        print("="*70)
        print(f"Retention period: {self.keep_days} days")
        print(f"Cutoff date: {self.cutoff_date.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Mode: {'DRY RUN (no files will be deleted)' if self.dry_run else 'PRODUCTION'}")
        print("="*70)
        
        # Find old files
        print("\n🔍 Scanning for old files...")
        old_files = self.find_old_files()
        
        if not old_files:
            print("\n✅ No old files found - everything is clean!")
            return
        
        # Group by directory for better output
        files_by_dir = {}
        total_size = 0
        
        for filepath, age_days, size_bytes in old_files:
            directory = os.path.dirname(filepath)
            if directory not in files_by_dir:
                files_by_dir[directory] = []
            files_by_dir[directory].append((filepath, age_days, size_bytes))
            total_size += size_bytes
        
        # Display summary
        print(f"\n📊 Found {len(old_files)} file(s) to cleanup")
        print(f"   Total size: {self.format_size(total_size)}")
        print()
        
        # Display by directory
        for directory, files in files_by_dir.items():
            dir_size = sum(size for _, _, size in files)
            print(f"\n📁 {directory}/")
            print(f"   Files: {len(files)}, Size: {self.format_size(dir_size)}")
            
            # Show first 5 files from this directory
            for filepath, age_days, size_bytes in files[:5]:
                filename = os.path.basename(filepath)
                print(f"   • {filename}")
                print(f"     Age: {age_days:.1f} days, Size: {self.format_size(size_bytes)}")
            
            if len(files) > 5:
                print(f"   ... and {len(files) - 5} more file(s)")
        
        # Delete files
        if self.dry_run:
            print("\n" + "="*70)
            print("🔍 DRY RUN - No files were deleted")
            print("   Run without --dry-run to actually delete files")
            print("="*70)
            return
        
        print("\n" + "="*70)
        print("🗑️  Deleting files...")
        print("="*70)
        
        deleted_count = 0
        deleted_size = 0
        failed_count = 0
        
        for filepath, age_days, size_bytes in old_files:
            try:
                os.remove(filepath)
                deleted_count += 1
                deleted_size += size_bytes
                print(f"✅ Deleted: {os.path.basename(filepath)}")
            except Exception as e:
                failed_count += 1
                print(f"❌ Failed to delete {os.path.basename(filepath)}: {e}")
        
        # Final summary
        print("\n" + "="*70)
        print("📊 CLEANUP SUMMARY")
        print("="*70)
        print(f"✅ Deleted: {deleted_count} file(s)")
        print(f"💾 Space freed: {self.format_size(deleted_size)}")
        if failed_count > 0:
            print(f"❌ Failed: {failed_count} file(s)")
        print("="*70)


def main():
    parser = argparse.ArgumentParser(
        description='Cleanup old log and output files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default: Keep last 14 days
  python scripts/utils/cleanup_old_logs.py
  
  # Keep only last 7 days
  python scripts/utils/cleanup_old_logs.py --keep-days 7
  
  # Dry run (see what would be deleted)
  python scripts/utils/cleanup_old_logs.py --dry-run
  
  # Docker
  docker exec polystars_scheduler python /app/scripts/utils/cleanup_old_logs.py
        """
    )
    
    parser.add_argument('--keep-days', type=int, default=14,
                      help='Number of days to keep files (default: 14)')
    parser.add_argument('--dry-run', action='store_true',
                      help='Show what would be deleted without actually deleting')
    
    args = parser.parse_args()
    
    cleaner = LogCleaner(keep_days=args.keep_days, dry_run=args.dry_run)
    cleaner.cleanup()


if __name__ == '__main__':
    main()
