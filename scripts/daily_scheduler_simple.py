"""
Simplified Daily Data Scheduler

Orchestrates daily data loading without seasons.

LOGIC:
======
- Genesis: Load once (2024-07-06 to 2026-01-05) with 100M filter
- Daily: Load every day with 5M filter
  - Events: Yesterday (completed day)
  - Redemptions/Positions/Leaderboard: 3 days ago

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
from datetime import datetime, date, timedelta
from typing import Dict

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.data_loading_manager import DataLoadingManager, GENESIS_START_DATE, GENESIS_END_DATE


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
            print(f"  • Events: {dates['events_date']} (yesterday)")
            print(f"  • Redemptions: {dates['redemptions_date']} (3 days ago)")
            
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
            import scripts.fetch.fetch_events_config as config
            
            # Set dates
            config.START_DATE = datetime.combine(target_date, datetime.min.time())
            config.END_DATE = datetime.combine(target_date, datetime.max.time())
            
            # Set volume filter
            config.MIN_VOLUME = self.manager.get_volume_filter(is_genesis=is_genesis)
            
            # For Genesis, load entire period
            if is_genesis:
                config.START_DATE = datetime.combine(GENESIS_START_DATE, datetime.min.time())
                config.END_DATE = datetime.combine(GENESIS_END_DATE, datetime.max.time())
            
            volume_label = "100M (Genesis)" if is_genesis else "5M (Daily)"
            print(f"📅 Config set:")
            print(f"   Date range: {config.START_DATE.date()} to {config.END_DATE.date()}")
            print(f"   MIN_VOLUME: {config.MIN_VOLUME:,} ({volume_label})")
            
        except Exception as e:
            print(f"⚠️  Could not configure: {e}")
    
    def run_script(self, script_key: str) -> Dict:
        """Run a data fetching script"""
        script_config = self.scripts[script_key]
        
        print(f"\n{'='*70}")
        print(f"🚀 RUNNING: {script_config['name']}")
        print(f"{'='*70}")
        
        if self.dry_run:
            print("🔍 DRY RUN - Skipping")
            return {'success': True, 'duration': 0, 'records': 0}
        
        start_time = time.time()
        
        try:
            cmd = ['python', script_config['script']] + script_config['args']
            result = subprocess.run(cmd, cwd=project_root, check=True)
            
            duration = time.time() - start_time
            print(f"\n✅ Completed ({duration:.1f}s)")
            
            return {'success': True, 'duration': duration, 'records': 0}
            
        except subprocess.CalledProcessError as e:
            duration = time.time() - start_time
            print(f"\n❌ Failed (code {e.returncode})")
            return {'success': False, 'duration': duration, 'error': str(e)}
    
    def run_daily_pipeline(self, force: bool = False) -> Dict:
        """Run daily data pipeline"""
        
        print("\n" + "="*70)
        print("📊 DAILY DATA PIPELINE")
        print("="*70)
        print(f"Mode: {'DRY RUN' if self.dry_run else 'PRODUCTION'}")
        print(f"Database: {'Local PostgreSQL' if self.use_local_db else 'Supabase'}")
        
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
        print(f"  • Events: {events_date} (yesterday)")
        print(f"  • Redemptions: {redemptions_date} (3 days ago)")
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
                self.manager.mark_data_loaded('events', events_date, load_type='daily')
        
        # STEP 2-4: Redemptions, Positions, Leaderboard (3 days ago)
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
                        self.manager.mark_data_loaded(script_key, redemptions_date, load_type='daily')
        
        # Summary
        print("\n" + "="*70)
        print("📊 PIPELINE SUMMARY")
        print("="*70)
        success_count = sum(1 for r in results.values() if r.get('success'))
        skipped_count = sum(1 for r in results.values() if r.get('skipped'))
        print(f"✅ Successful: {success_count}/{len(results)}")
        if skipped_count > 0:
            print(f"⏭️  Skipped: {skipped_count} (already loaded or Genesis period)")
        print("="*70)
        
        return {'success': all(r.get('success') for r in results.values()), 'results': results}
    
    def run_genesis_load(self) -> Dict:
        """Load Genesis (historical) data"""
        
        print("\n" + "="*70)
        print("🕰️  GENESIS DATA LOAD")
        print("="*70)
        print(f"Period: {GENESIS_START_DATE} to {GENESIS_END_DATE}")
        print(f"Filter: 100M volume")
        
        if not self.manager.needs_genesis_load():
            print("\n✅ Genesis already loaded")
            return {'success': True, 'skipped': True}
        
        if self.dry_run:
            print("\n🔍 DRY RUN - Skipping")
            return {'success': True}
        
        print("="*70)
        
        # Configure for Genesis
        self.configure_for_date(GENESIS_START_DATE, is_genesis=True)
        
        # Run all scripts for Genesis period
        results = {}
        
        for script_key in ['events', 'redemptions', 'positions', 'leaderboard']:
            results[script_key] = self.run_script(script_key)
            if results[script_key]['success']:
                self.manager.mark_data_loaded(script_key, GENESIS_START_DATE, load_type='genesis')
        
        # Summary
        print("\n" + "="*70)
        print("📊 GENESIS LOAD SUMMARY")
        print("="*70)
        success_count = sum(1 for r in results.values() if r.get('success'))
        print(f"✅ Successful: {success_count}/{len(results)}")
        print("="*70)
        
        return {'success': all(r.get('success') for r in results.values()), 'results': results}
    
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
        
        # Get yesterday's date (target for events)
        yesterday = date.today() - timedelta(days=1)
        
        # Find missing dates
        missing_dates = self.manager.get_missing_dates(
            start_from=GENESIS_END_DATE + timedelta(days=1),
            up_to=yesterday
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
                self.manager.mark_data_loaded('events', missing_date, load_type='daily')
                print(f"✅ Events loaded for {missing_date}")
            else:
                print(f"❌ Events failed for {missing_date}")
                results[str(missing_date)] = {'success': False, 'step': 'events'}
                continue  # Skip other steps if events failed
            
            # STEP 2-4: Redemptions, Positions, Leaderboard (for date - 3 days)
            redemptions_date = missing_date - timedelta(days=3)
            
            # Only load redemptions if date is after Genesis
            if redemptions_date > GENESIS_END_DATE:
                print(f"\n2️⃣ Redemptions/Positions/Leaderboard for {redemptions_date} (3 days lag)")
                
                # Configure for redemptions date
                self.configure_for_date(redemptions_date, is_genesis=False)
                
                for script_key in ['redemptions', 'positions', 'leaderboard']:
                    # Check if already loaded
                    if self.manager.is_data_loaded_for_date(redemptions_date, script_key):
                        print(f"  ⏭️  {self.scripts[script_key]['name']}: Already loaded")
                        continue
                    
                    result = self.run_script(script_key)
                    if result['success']:
                        self.manager.mark_data_loaded(script_key, redemptions_date, load_type='daily')
                    else:
                        print(f"  ⚠️  {self.scripts[script_key]['name']} failed (continuing...)")
            else:
                print(f"\n⏭️  Skipping redemptions for {redemptions_date} (within Genesis period)")
            
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
