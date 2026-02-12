"""
Simplified Daily Data Scheduler

Orchestrates daily data loading without seasons.

LOGIC:
======
- Genesis: Load once (2024-07-06 to 2026-01-05) with 100M filter
- Daily: Load every day with 5M filter
  - Events: EVENTS_LAG_DAYS day ago (default: 1 = yesterday)
  - Redemptions/Positions/Leaderboard: DATA_LAG_DAYS days ago (default: 3)

USAGE:
======
Check system state:
    python scripts/daily_scheduler_simple.py --check

Run daily pipeline:
    python scripts/daily_scheduler_simple.py --run

Load Genesis:
    python scripts/daily_scheduler_simple.py --historical

Docker:
    docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --run
"""

import os
import sys
import subprocess
import time
import argparse
import tempfile
from datetime import datetime, date, timedelta
from typing import Dict
from pathlib import Path

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.data_loading_manager import DataLoadingManager, GENESIS_START_DATE, GENESIS_END_DATE, DATA_LAG_DAYS, EVENTS_LAG_DAYS


class ProcessLock:
    """Manages lock file to prevent concurrent script execution"""
    
    def __init__(self, lock_name: str = "polystars_scheduler"):
        self.lock_dir = Path(tempfile.gettempdir())
        self.lock_file = self.lock_dir / f"{lock_name}.lock"
        self.is_locked_by_me = False
    
    def acquire(self, operation: str) -> bool:
        """
        Try to acquire lock
        
        Args:
            operation: Name of operation trying to acquire lock (e.g., 'catch-up', 'historical')
            
        Returns:
            True if lock acquired, False if already locked
        """
        if self.lock_file.exists():
            try:
                with open(self.lock_file, 'r') as f:
                    lock_info = f.read().strip().split('\n')
                    if len(lock_info) >= 2:
                        locked_operation = lock_info[0]
                        locked_time = lock_info[1]
                        print(f"\n❌ Cannot start: Another operation is already running!")
                        print(f"   Operation: {locked_operation}")
                        print(f"   Started at: {locked_time}")
                        print(f"   Lock file: {self.lock_file}")
                        return False
            except Exception as e:
                print(f"⚠️  Warning: Could not read lock file: {e}")
        
        # Create lock file
        try:
            with open(self.lock_file, 'w') as f:
                f.write(f"{operation}\n")
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"PID: {os.getpid()}\n")
            self.is_locked_by_me = True
            print(f"🔒 Lock acquired for '{operation}' operation")
            print(f"   Lock file: {self.lock_file}")
            return True
        except Exception as e:
            print(f"⚠️  Warning: Could not create lock file: {e}")
            return False
    
    def release(self):
        """Release lock by removing lock file"""
        if self.is_locked_by_me and self.lock_file.exists():
            try:
                self.lock_file.unlink()
                self.is_locked_by_me = False
                print(f"🔓 Lock released")
            except Exception as e:
                print(f"⚠️  Warning: Could not remove lock file: {e}")
    
    def is_locked(self) -> bool:
        """Check if lock exists"""
        return self.lock_file.exists()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class SimplifiedScheduler:
    """Simplified scheduler without seasons"""
    
    def __init__(self, use_local_db: bool = True, dry_run: bool = False):
        self.manager = DataLoadingManager(use_local_db=use_local_db)
        self.use_local_db = use_local_db
        self.dry_run = dry_run
        
        # Script configurations
        self.scripts = {
            'events': {
                'name': 'Events Fetcher',
                'script': 'scripts/fetch/fetch_events_parallel_optimized.py',
                'args': ['--upload', '--local'] if use_local_db else ['--upload']
            },
            'redemptions': {
                'name': 'Redemptions Fetcher',
                'script': 'scripts/fetch/fetch_redemptions.py',
                'args': ['--upload', '--local'] if use_local_db else ['--upload']
            },
            'positions': {
                'name': 'User Closed Positions',
                'script': 'scripts/fetch/fetch_user_closed_positions_parallel.py',
                'args': ['--upload', '--local'] if use_local_db else ['--upload']
            },
            'leaderboard': {
                'name': 'Trader Leaderboard',
                'script': 'scripts/fetch/fetch_trader_leaderboard_parallel.py',
                'args': ['--upload', '--local', '--from-db'] if use_local_db else ['--upload', '--from-db']
            }
        }
    
    def check_system_state(self):
        """Check and display system state"""
        print("\n" + "="*70)
        print("🔍 SYSTEM STATE CHECK")
        print("="*70)
        
        # Check testing mode
        events_limit = self.manager.get_events_limit()
        max_volume = self.manager.get_max_volume_filter()
        if events_limit or max_volume:
            print(f"\n⚠️  TESTING MODE ACTIVE:")
            if events_limit:
                print(f"  • MAX_EVENTS: {events_limit} (limited event count)")
            if max_volume:
                print(f"  • MAX_VOLUME: ${max_volume:,} (excludes large events)")
            print(f"  • Change in scripts/data_loading_manager.py")
        
        # Check if any data exists
        has_data = self.manager.has_any_data()
        needs_genesis = self.manager.needs_genesis_load()
        
        print(f"\nDatabase Status:")
        print(f"  • Has data: {'Yes' if has_data else 'No (Empty)'}")
        print(f"  • Genesis loaded: {'Yes ✅' if not needs_genesis else 'No ❌'}")
        
        if needs_genesis:
            print(f"\n⚠️  Genesis data needs to be loaded!")
            print(f"  Period: {GENESIS_START_DATE} to {GENESIS_END_DATE}")
            print(f"  Filter: 100M volume")
            print(f"\n  👉 Run: python scripts/daily_scheduler_simple.py --historical")
        else:
            print(f"\n✅ Genesis loaded - ready for daily operations")
            
            # Show today's dates
            dates = self.manager.get_loading_dates()
            print(f"\nToday's Loading Dates ({dates['reference_date']}):")
            print(f"  • Events: {dates['events_date']} ({EVENTS_LAG_DAYS} day{'s' if EVENTS_LAG_DAYS > 1 else ''} ago)")
            print(f"  • Redemptions: {dates['redemptions_date']} ({DATA_LAG_DAYS} days ago)")
            
            # Check if today's data loaded
            events_loaded = self.manager.is_data_loaded_for_date(dates['events_date'], 'events')
            redemptions_loaded = self.manager.is_data_loaded_for_date(dates['redemptions_date'], 'redemptions')
            
            print(f"\nToday's Status:")
            print(f"  • Events ({dates['events_date']}): {'✅ Loaded' if events_loaded else '⏳ Pending'}")
            print(f"  • Redemptions ({dates['redemptions_date']}): {'✅ Loaded' if redemptions_loaded else '⏳ Pending'}")
            
            # Check for missing dates
            last_loaded = self.manager.get_last_loaded_date('events')
            if last_loaded:
                missing = self.manager.get_missing_dates(
                    start_from=GENESIS_END_DATE + timedelta(days=1),
                    up_to=dates['events_date']
                )
                
                if missing:
                    print(f"\n⚠️  GAP DETECTED: Missing {len(missing)} day(s) of data!")
                    print(f"  Last loaded: {last_loaded}")
                    print(f"  Gap: {missing[0]} to {missing[-1]}")
                    print(f"\n  👉 Run: python scripts/daily_scheduler_simple.py --catch-up")
                    print(f"     This will load all missing days automatically")
        
        print("="*70)
    
    def configure_for_date(self, target_date: date, is_genesis: bool = False):
        """
        Configure fetch_events_config for specific date
        
        Args:
            target_date: Date to load
            is_genesis: Whether this is Genesis load
        """
        try:
            # Calculate dates
            if is_genesis:
                start_date = GENESIS_START_DATE
                end_date = GENESIS_END_DATE
            else:
                start_date = target_date
                end_date = target_date
            
            # Set environment variables (will be passed to subprocesses)
            os.environ['POLYSTARS_START_DATE'] = start_date.strftime('%Y-%m-%d')
            os.environ['POLYSTARS_END_DATE'] = end_date.strftime('%Y-%m-%d')
            os.environ['POLYSTARS_MIN_VOLUME'] = str(self.manager.get_volume_filter(is_genesis=is_genesis))
            os.environ['POLYSTARS_IS_GENESIS'] = 'true' if is_genesis else 'false'
            
            # Set MAX_EVENTS if specified (for testing)
            events_limit = self.manager.get_events_limit()
            if events_limit:
                os.environ['POLYSTARS_MAX_EVENTS'] = str(events_limit)
            elif 'POLYSTARS_MAX_EVENTS' in os.environ:
                # Clear if was set before
                del os.environ['POLYSTARS_MAX_EVENTS']
            
            # Set MAX_VOLUME if specified (for testing)
            max_volume = self.manager.get_max_volume_filter()
            if max_volume:
                os.environ['POLYSTARS_MAX_VOLUME'] = str(max_volume)
            elif 'POLYSTARS_MAX_VOLUME' in os.environ:
                # Clear if was set before
                del os.environ['POLYSTARS_MAX_VOLUME']
            
            volume_label = "100M (Genesis)" if is_genesis else "5M (Daily)"
            print(f"📅 Config set (via env vars):")
            print(f"   Date range: {start_date} to {end_date}")
            print(f"   MIN_VOLUME: {os.environ['POLYSTARS_MIN_VOLUME']} ({volume_label})")
            
            # Show testing mode warnings
            if max_volume or events_limit:
                print(f"\n   ⚠️  TESTING MODE ACTIVE:")
                if max_volume:
                    print(f"      • MAX_VOLUME: ${max_volume:,} (excludes events over this)")
                if events_limit:
                    print(f"      • MAX_EVENTS: {events_limit} events (limits total count)")
                print(f"      • Set both to None in data_loading_manager.py for production")
            
        except Exception as e:
            print(f"⚠️  Could not configure: {e}")
    
    def run_script(self, script_key: str) -> Dict:
        """Run a data fetching script and track record counts"""
        script_config = self.scripts[script_key]
        
        print(f"\n{'='*70}")
        print(f"🚀 RUNNING: {script_config['name']}")
        print(f"{'='*70}")
        
        if self.dry_run:
            print("🔍 DRY RUN - Skipping")
            return {'success': True, 'duration': 0, 'records': 0, 'markets': 0}
        
        # Map script keys to table names
        table_map = {
            'events': 'events',
            'redemptions': 'redemptions',
            'positions': 'user_closed_positions',
            'leaderboard': 'trader_leaderboard'
        }
        
        # Get count BEFORE running script
        table_name = table_map.get(script_key)
        count_before = self.manager.get_table_count(table_name) if table_name else 0
        markets_before = self.manager.get_table_count('markets') if script_key == 'events' else 0
        
        start_time = time.time()
        
        try:
            # Use the same Python interpreter that's running this script
            cmd = [sys.executable, script_config['script']] + script_config['args']
            result = subprocess.run(cmd, cwd=project_root, check=True)
            
            duration = time.time() - start_time
            
            # Get count AFTER running script
            count_after = self.manager.get_table_count(table_name) if table_name else 0
            markets_after = self.manager.get_table_count('markets') if script_key == 'events' else 0
            
            # Calculate actual loaded records
            records = count_after - count_before
            markets = markets_after - markets_before
            
            if script_key == 'events':
                print(f"\n✅ Completed ({duration:.1f}s) - {records:,} events, {markets:,} markets")
            else:
                print(f"\n✅ Completed ({duration:.1f}s) - {records:,} records")
            
            return {'success': True, 'duration': duration, 'records': records, 'markets': markets}
            
        except subprocess.CalledProcessError as e:
            duration = time.time() - start_time
            print(f"\n❌ Failed (code {e.returncode})")
            return {'success': False, 'duration': duration, 'error': str(e), 'records': 0, 'markets': 0}
    
    def run_daily_pipeline(self, force: bool = False) -> Dict:
        """Run daily data pipeline"""
        
        print("\n" + "="*70)
        print("📊 DAILY DATA PIPELINE")
        print("="*70)
        print(f"Mode: {'DRY RUN' if self.dry_run else 'PRODUCTION'}")
        print(f"Database: {'Local PostgreSQL' if self.use_local_db else 'Supabase'}")
        
        # Check if another long-running operation is in progress
        lock = ProcessLock()
        if lock.is_locked():
            print("\n⚠️  Another operation (--catch-up or --historical) is running")
            print("   Daily pipeline will be skipped to avoid conflicts")
            print("   This is normal - cron will retry on next schedule")
            return {'success': False, 'error': 'Another operation in progress', 'skipped': True}
        
        # Check Genesis first
        if self.manager.needs_genesis_load() and not force:
            print("\n❌ Cannot run - Genesis data not loaded!")
            print("   Run with --historical flag first")
            return {'success': False, 'error': 'Genesis not loaded'}
        
        # Get loading dates
        dates = self.manager.get_loading_dates()
        events_date = dates['events_date']
        redemptions_date = dates['redemptions_date']
        
        print(f"\nLoading Dates:")
        print(f"  • Events: {events_date} ({EVENTS_LAG_DAYS} day{'s' if EVENTS_LAG_DAYS > 1 else ''} ago)")
        print(f"  • Redemptions: {redemptions_date} ({DATA_LAG_DAYS} days ago)")
        print("="*70)
        
        results = {}
        
        # STEP 1: Events (yesterday's data)
        print(f"\n📅 Events: Loading for {events_date}")
        if not force and self.manager.is_data_loaded_for_date(events_date, 'events'):
            print(f"⏭️  Already loaded")
            results['events'] = {'success': True, 'skipped': True}
        else:
            self.configure_for_date(events_date, is_genesis=False)
            results['events'] = self.run_script('events')
            if results['events']['success'] and not self.dry_run:
                self.manager.mark_data_loaded('events', events_date, 
                                            record_count=results['events']['records'],
                                            markets_count=results['events']['markets'],
                                            load_type='daily')
        
        # STEP 2-4: Redemptions, Positions, Leaderboard (DATA_LAG_DAYS ago)
        # ℹ️ For DAILY pipeline: lag allows data to finalize
        # (For CATCH-UP of historical data, see run_catch_up - no lag needed there)
        print(f"\n📅 Redemptions/Positions/Leaderboard: Loading for {redemptions_date}")
        
        # ⚠️ ВАЖНО: Проверка на Genesis период (защита от дублей)
        if redemptions_date <= GENESIS_END_DATE:
            print(f"\n⏭️  Skipping redemptions/positions/leaderboard for {redemptions_date}")
            print(f"   Reason: Date is within Genesis period (already loaded)")
            print(f"   Genesis end: {GENESIS_END_DATE}")
            for script_key in ['redemptions', 'positions', 'leaderboard']:
                results[script_key] = {'success': True, 'skipped': True, 'reason': 'genesis_period'}
        else:
            for script_key in ['redemptions', 'positions', 'leaderboard']:
                if not force and self.manager.is_data_loaded_for_date(redemptions_date, script_key):
                    print(f"⏭️  {self.scripts[script_key]['name']}: Already loaded")
                    results[script_key] = {'success': True, 'skipped': True}
                else:
                    # Configure for redemptions date
                    if script_key == 'redemptions':
                        self.configure_for_date(redemptions_date, is_genesis=False)
                    
                    results[script_key] = self.run_script(script_key)
                    if results[script_key]['success'] and not self.dry_run:
                        self.manager.mark_data_loaded(script_key, redemptions_date,
                                                    record_count=results[script_key]['records'],
                                                    load_type='daily')
        
        # STEP 5: Auto-fix incomplete days (events loaded but redemptions missing)
        # This handles the first DATA_LAG_DAYS days after Genesis where daily pipeline skipped redemptions
        print(f"\n🔍 Checking for incomplete days...")
        incomplete = self.manager.get_incomplete_dates(
            start_from=GENESIS_END_DATE + timedelta(days=1),
            up_to=date.today() - timedelta(days=1)
        )
        
        fixed_count = 0
        skipped_count = 0
        
        if incomplete:
            print(f"\n⚠️  Found {len(incomplete)} day(s) with incomplete data!")
            print(f"   (Events loaded but redemptions/positions/leaderboard missing)")
            
            for incomplete_date, missing_types in incomplete:
                # Check if data is ready (DATA_LAG_DAYS lag for finalization)
                today = date.today()
                days_since = (today - incomplete_date).days
                
                # Only auto-fix if events ended >= DATA_LAG_DAYS ago
                if days_since < DATA_LAG_DAYS:
                    print(f"\n⏳ Skipping {incomplete_date} (ended {days_since} day(s) ago)")
                    print(f"   Missing: {', '.join(missing_types)}")
                    print(f"   Will be available on: {incomplete_date + timedelta(days=DATA_LAG_DAYS)}")
                    skipped_count += 1
                    continue
                
                print(f"\n📅 Auto-fixing: {incomplete_date} (ended {days_since} days ago)")
                print(f"   Missing: {', '.join(missing_types)}")
                
                # Configure for this date (no lag - historical data is finalized)
                self.configure_for_date(incomplete_date, is_genesis=False)
                
                for script_key in missing_types:
                    result = self.run_script(script_key)
                    if result['success'] and not self.dry_run:
                        # For events, also pass markets_count
                        if script_key == 'events':
                            self.manager.mark_data_loaded(script_key, incomplete_date,
                                                        record_count=result['records'],
                                                        markets_count=result['markets'],
                                                        load_type='daily')
                            print(f"   ✅ {self.scripts[script_key]['name']} loaded ({result['records']:,} events, {result['markets']:,} markets)")
                        else:
                            self.manager.mark_data_loaded(script_key, incomplete_date,
                                                        record_count=result['records'],
                                                        load_type='daily')
                            print(f"   ✅ {self.scripts[script_key]['name']} loaded ({result['records']:,} records)")
                    else:
                        print(f"   ⚠️  {self.scripts[script_key]['name']} failed")
                
                fixed_count += 1
            
            if fixed_count == 0 and skipped_count > 0:
                print(f"\n   ⏳ {skipped_count} day(s) skipped (waiting for {DATA_LAG_DAYS}-day lag)")
        else:
            print("   ✅ No incomplete days found")
        
        # Summary
        print("\n" + "="*70)
        print("📊 PIPELINE SUMMARY")
        print("="*70)
        success_count = sum(1 for r in results.values() if r.get('success'))
        skipped_count_main = sum(1 for r in results.values() if r.get('skipped'))
        print(f"✅ Successful: {success_count}/{len(results)}")
        if skipped_count_main > 0:
            print(f"⏭️  Skipped: {skipped_count_main} (already loaded or Genesis period)")
        if incomplete:
                if fixed_count > 0:
                    print(f"🔧 Auto-fixed: {fixed_count} incomplete day(s)")
                if skipped_count > 0:
                    print(f"⏳ Waiting for lag: {skipped_count} day(s) (need {DATA_LAG_DAYS}-day finalization)")
        print("="*70)
        
        return {'success': all(r.get('success') for r in results.values()), 'results': results}
    
    def run_genesis_load(self) -> Dict:
        """Load Genesis (historical) data"""
        
        print("\n" + "="*70)
        print("🕰️  GENESIS DATA LOAD")
        print("="*70)
        print(f"Period: {GENESIS_START_DATE} to {GENESIS_END_DATE}")
        print(f"Filter: 100M volume")
        
        # Show testing mode warnings
        events_limit = self.manager.get_events_limit()
        max_volume = self.manager.get_max_volume_filter()
        if events_limit or max_volume:
            print(f"\n⚠️  TESTING MODE:")
            if events_limit:
                print(f"   • MAX_EVENTS: {events_limit} events (will load only first {events_limit})")
            if max_volume:
                print(f"   • MAX_VOLUME: ${max_volume:,} (excludes events over this)")
            print(f"   • Set to None in data_loading_manager.py for full load")
        
        if not self.manager.needs_genesis_load():
            print("\n✅ Genesis already loaded")
            return {'success': True, 'skipped': True}
        
        if self.dry_run:
            print("\n🔍 DRY RUN - Skipping")
            return {'success': True}
        
        # Acquire lock to prevent concurrent runs
        lock = ProcessLock()
        if not lock.acquire('historical'):
            return {'success': False, 'error': 'Could not acquire lock'}
        
        try:
            print("="*70)
            
            # Configure for Genesis
            self.configure_for_date(GENESIS_START_DATE, is_genesis=True)
            
            # Run all scripts for Genesis period
            results = {}
            
            for script_key in ['events', 'redemptions', 'positions', 'leaderboard']:
                results[script_key] = self.run_script(script_key)
                if results[script_key]['success']:
                    # For events, also pass markets_count
                    if script_key == 'events':
                        self.manager.mark_data_loaded(script_key, GENESIS_START_DATE,
                                                    record_count=results[script_key]['records'],
                                                    markets_count=results[script_key]['markets'],
                                                    load_type='genesis')
                    else:
                        self.manager.mark_data_loaded(script_key, GENESIS_START_DATE,
                                                    record_count=results[script_key]['records'],
                                                    load_type='genesis')
            
            # Summary
            print("\n" + "="*70)
            print("📊 GENESIS LOAD SUMMARY")
            print("="*70)
            success_count = sum(1 for r in results.values() if r.get('success'))
            print(f"✅ Successful: {success_count}/{len(results)}")
            print("="*70)
            
            return {'success': all(r.get('success') for r in results.values()), 'results': results}
        
        finally:
            # Always release lock
            lock.release()
    
    def run_catch_up(self) -> Dict:
        """
        Automatically load all missing data between Genesis and now
        
        Returns:
            Dict with catch-up results
        """
        print("\n" + "="*70)
        print("🔄 CATCH-UP MODE: Loading missing data")
        print("="*70)
        
        # Check Genesis first
        if self.manager.needs_genesis_load():
            print("\n❌ Genesis not loaded - run --historical first!")
            return {'success': False, 'error': 'Genesis not loaded'}
        
        # Get target date for events (respecting lag)
        events_target_date = date.today() - timedelta(days=EVENTS_LAG_DAYS)
        
        print(f"\n📅 Lag configuration:")
        print(f"   Events lag: {EVENTS_LAG_DAYS} days (loading up to {events_target_date})")
        print(f"   Data lag: {DATA_LAG_DAYS} days (redemptions/positions/leaderboard)")
        
        # Find missing dates
        missing_dates = self.manager.get_missing_dates(
            start_from=GENESIS_END_DATE + timedelta(days=1),
            up_to=events_target_date
        )
        
        if not missing_dates:
            print("\n✅ No missing data - system is up to date!")
            return {'success': True, 'missing': 0}
        
        print(f"\nFound {len(missing_dates)} missing day(s):")
        print(f"  From: {missing_dates[0]}")
        print(f"  To: {missing_dates[-1]}")
        print(f"  Total: {len(missing_dates)} days")
        
        if self.dry_run:
            print("\n🔍 DRY RUN - Would load these dates")
            for d in missing_dates[:10]:
                print(f"  • {d}")
            if len(missing_dates) > 10:
                print(f"  • ... and {len(missing_dates) - 10} more")
            return {'success': True, 'missing': len(missing_dates)}
        
        # Estimate time
        estimated_minutes = len(missing_dates) * 5  # ~5 min per day average
        print(f"\n⏱️  Estimated time: ~{estimated_minutes} minutes ({estimated_minutes/60:.1f} hours)")
        print(f"   (about 5 minutes per day)")
        
        # Acquire lock to prevent concurrent runs
        lock = ProcessLock()
        if not lock.acquire('catch-up'):
            return {'success': False, 'error': 'Could not acquire lock'}
        
        try:
            print("\n" + "="*70)
            print("Starting catch-up process...")
            print("="*70)
            
            # Load each missing date
            results = {}
            start_time = time.time()
            
            for i, missing_date in enumerate(missing_dates, 1):
                print(f"\n{'='*70}")
                print(f"📅 Day {i}/{len(missing_dates)}: Loading {missing_date}")
                print(f"{'='*70}")
                
                # STEP 1: Events for this date
                print(f"\n1️⃣ Events for {missing_date}")
                self.configure_for_date(missing_date, is_genesis=False)
                result_events = self.run_script('events')
                
                if result_events['success']:
                    self.manager.mark_data_loaded('events', missing_date,
                                                record_count=result_events['records'],
                                                markets_count=result_events['markets'],
                                                load_type='daily')
                    print(f"✅ Events loaded for {missing_date} ({result_events['records']:,} events, {result_events['markets']:,} markets)")
                else:
                    print(f"❌ Events failed for {missing_date}")
                    results[str(missing_date)] = {'success': False, 'step': 'events'}
                    continue  # Skip other steps if events failed
                
                # STEP 2-4: Redemptions, Positions, Leaderboard
                # Check if data is ready (DATA_LAG_DAYS lag for finalization)
                today = date.today()
                days_since_event = (today - missing_date).days
                
                # Only load redemptions if:
                # 1. Event ended >= DATA_LAG_DAYS ago (data is finalized)
                # 2. Event is after Genesis period (avoid duplicates)
                if missing_date <= GENESIS_END_DATE:
                    print(f"\n⏭️  Skipping redemptions/positions/leaderboard for {missing_date}")
                    print(f"   Reason: Date is within Genesis period (already loaded)")
                elif days_since_event < DATA_LAG_DAYS:
                    print(f"\n⏳ Skipping redemptions/positions/leaderboard for {missing_date}")
                    print(f"   Reason: Event ended only {days_since_event} day(s) ago (need {DATA_LAG_DAYS} days)")
                    print(f"   Will be available on: {missing_date + timedelta(days=DATA_LAG_DAYS)}")
                else:
                    print(f"\n2️⃣ Redemptions/Positions/Leaderboard for {missing_date}")
                    print(f"   ℹ️  Event ended {days_since_event} days ago - data is finalized")
                    
                    # Configure for this date
                    self.configure_for_date(missing_date, is_genesis=False)
                    
                    for script_key in ['redemptions', 'positions', 'leaderboard']:
                        # Check if already loaded
                        if self.manager.is_data_loaded_for_date(missing_date, script_key):
                            print(f"  ⏭️  {self.scripts[script_key]['name']}: Already loaded")
                            continue
                        
                        result = self.run_script(script_key)
                        if result['success']:
                            self.manager.mark_data_loaded(script_key, missing_date,
                                                        record_count=result['records'],
                                                        load_type='daily')
                        else:
                            print(f"  ⚠️  {self.scripts[script_key]['name']} failed (continuing...)")
                
                results[str(missing_date)] = {'success': True}
                
                # Progress
                elapsed = time.time() - start_time
                avg_time_per_day = elapsed / i
                remaining_days = len(missing_dates) - i
                estimated_remaining = remaining_days * avg_time_per_day
                
                print(f"\n📊 Progress: {i}/{len(missing_dates)} days")
                print(f"⏱️  Elapsed: {elapsed/60:.1f} min | Remaining: ~{estimated_remaining/60:.1f} min")
        
            # Summary
            total_time = time.time() - start_time
            print("\n" + "="*70)
            print("📊 CATCH-UP SUMMARY")
            print("="*70)
            print(f"✅ Loaded: {len(missing_dates)} days")
            print(f"⏱️  Total time: {total_time/60:.1f} minutes ({total_time/3600:.1f} hours)")
            print(f"⚡ Average: {total_time/len(missing_dates)/60:.1f} minutes per day")
            print("="*70)
            
            return {'success': True, 'days_loaded': len(missing_dates), 'duration': total_time}
        
        finally:
            # Always release lock
            lock.release()


def main():
    parser = argparse.ArgumentParser(
        description='Simplified Daily Data Scheduler',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check system state
  python scripts/daily_scheduler_simple.py --check
  
  # Run daily pipeline
  python scripts/daily_scheduler_simple.py --run
  
  # Load Genesis (historical data)
  python scripts/daily_scheduler_simple.py --historical
  
  # Force reload
  python scripts/daily_scheduler_simple.py --run --force
  
  # Dry run
  python scripts/daily_scheduler_simple.py --run --dry-run
  
  # Docker
  docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --run
        """
    )
    
    parser.add_argument('--run', action='store_true', help='Run daily pipeline')
    parser.add_argument('--check', action='store_true', help='Check system state')
    parser.add_argument('--historical', action='store_true', help='Load Genesis data')
    parser.add_argument('--catch-up', action='store_true', help='Load all missing data automatically')
    parser.add_argument('--force', action='store_true', help='Force reload')
    parser.add_argument('--dry-run', action='store_true', help='Dry run (test)')
    parser.add_argument('--local', action='store_true', default=True, help='Use local PostgreSQL')
    parser.add_argument('--supabase', action='store_true', help='Use Supabase')
    
    args = parser.parse_args()
    
    use_local_db = not args.supabase
    scheduler = SimplifiedScheduler(use_local_db=use_local_db, dry_run=args.dry_run)
    
    if args.check:
        scheduler.check_system_state()
    
    elif args.run:
        result = scheduler.run_daily_pipeline(force=args.force)
        sys.exit(0 if result['success'] else 1)
    
    elif args.historical:
        result = scheduler.run_genesis_load()
        sys.exit(0 if result['success'] else 1)
    
    elif args.catch_up:
        result = scheduler.run_catch_up()
        sys.exit(0 if result['success'] else 1)
    
    else:
        print("Use --help for usage")
        print("\n🚀 Quick start (new server):")
        print("  1. Check: python scripts/daily_scheduler_simple.py --check")
        print("  2. Genesis: python scripts/daily_scheduler_simple.py --historical")
        print("  3. Catch-up: python scripts/daily_scheduler_simple.py --catch-up")
        print("  4. Daily: python scripts/daily_scheduler_simple.py --run")


if __name__ == '__main__':
    main()
