"""
Daily Data Scheduler for PolyStars

Orchestrates daily data loading with season tracking and historical backfill support.

WORKFLOW:
=========
1. Check if historical (genesis) data needs loading
2. Check current season and day
3. Run all data fetching scripts in sequence:
   - Events (Step 1)
   - Redemptions (Step 2)
   - User Closed Positions (Step 3)
   - Trader Leaderboard (Step 4)
4. Mark data as loaded in season tracker

USAGE:
======
Manual run:
    python scripts/daily_scheduler.py --run

Status check:
    python scripts/daily_scheduler.py --status

Historical load:
    python scripts/daily_scheduler.py --historical

Force reload today:
    python scripts/daily_scheduler.py --force

Dry run (test without executing):
    python scripts/daily_scheduler.py --dry-run

Docker:
    docker exec polystars_python python scripts/daily_scheduler.py --run
"""

import os
import sys
import subprocess
import time
import argparse
from datetime import datetime, date
from typing import Dict, Optional

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.season_manager import SeasonManager
from scripts.season_context import SeasonContext


class DailyScheduler:
    """
    Orchestrates daily data loading with season tracking
    """
    
    def __init__(self, use_local_db: bool = True, dry_run: bool = False):
        """
        Initialize scheduler
        
        Args:
            use_local_db: Use local PostgreSQL (default: True)
            dry_run: Run without executing scripts (for testing)
        """
        self.season_manager = SeasonManager(use_local_db=use_local_db)
        self.use_local_db = use_local_db
        self.dry_run = dry_run
        
        # Script configurations
        self.scripts = {
            'events': {
                'name': 'Events Fetcher',
                'script': 'scripts/fetch/fetch_events_parallel_optimized.py',
                'args': ['--upload', '--local'] if use_local_db else ['--upload'],
                'estimated_time': '5-10 minutes'
            },
            'redemptions': {
                'name': 'Redemptions Fetcher',
                'script': 'scripts/fetch/fetch_redemptions.py',
                'args': ['--upload', '--local'] if use_local_db else ['--upload'],
                'estimated_time': '2-5 minutes'
            },
            'positions': {
                'name': 'User Closed Positions',
                'script': 'scripts/fetch/fetch_user_closed_positions_parallel.py',
                'args': ['--upload', '--local'] if use_local_db else ['--upload'],
                'estimated_time': '10-30 minutes'
            },
            'leaderboard': {
                'name': 'Trader Leaderboard',
                'script': 'scripts/fetch/fetch_trader_leaderboard_parallel.py',
                'args': ['--upload', '--local', '--from-db'] if use_local_db else ['--upload', '--from-db'],
                'estimated_time': '10-30 minutes'
            }
        }
    
    def _prepare_config_for_season(self, target_date: date):
        """
        Prepare fetch_events_config for the target date's season
        
        Args:
            target_date: Date to configure for
        """
        try:
            # Import config loader
            from scripts.fetch.fetch_events_config_loader import apply_season_dates
            
            # Apply season dates to config
            apply_season_dates(target_date=target_date, use_local_db=self.use_local_db)
            
        except Exception as e:
            print(f"⚠️  Could not apply season dates to config: {e}")
            print("   Continuing with default config...")
    
    def run_script(self, script_key: str) -> Dict:
        """
        Run a data fetching script
        
        Args:
            script_key: Script identifier (events, redemptions, positions, leaderboard)
            
        Returns:
            Dict with result: {'success': bool, 'duration': float, 'error': str}
        """
        script_config = self.scripts[script_key]
        
        print(f"\n{'='*70}")
        print(f"🚀 RUNNING: {script_config['name']}")
        print(f"{'='*70}")
        print(f"Script: {script_config['script']}")
        print(f"Estimated time: {script_config['estimated_time']}")
        
        if self.dry_run:
            print("🔍 DRY RUN - Skipping actual execution")
            return {'success': True, 'duration': 0, 'error': None, 'records': 0}
        
        start_time = time.time()
        
        try:
            # Build command
            cmd = ['python', script_config['script']] + script_config['args']
            print(f"Command: {' '.join(cmd)}")
            print()
            
            # Run script
            result = subprocess.run(
                cmd,
                cwd=project_root,
                capture_output=False,  # Show output in real-time
                text=True,
                check=True
            )
            
            duration = time.time() - start_time
            
            print(f"\n✅ {script_config['name']} completed successfully")
            print(f"⏱️  Duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
            
            return {
                'success': True,
                'duration': duration,
                'error': None,
                'records': 0  # TODO: Extract from script output if needed
            }
            
        except subprocess.CalledProcessError as e:
            duration = time.time() - start_time
            error_msg = f"Script exited with code {e.returncode}"
            
            print(f"\n❌ {script_config['name']} failed!")
            print(f"Error: {error_msg}")
            print(f"⏱️  Duration: {duration:.1f} seconds")
            
            return {
                'success': False,
                'duration': duration,
                'error': error_msg,
                'records': 0
            }
        
        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e)
            
            print(f"\n❌ {script_config['name']} encountered an error!")
            print(f"Error: {error_msg}")
            
            return {
                'success': False,
                'duration': duration,
                'error': error_msg,
                'records': 0
            }
    
    def check_system_state(self) -> Dict:
        """
        Check current system state and determine what needs to be loaded
        
        Returns:
            Dict with system state and recommendations
        """
        print("\n" + "="*70)
        print("🔍 CHECKING SYSTEM STATE")
        print("="*70)
        
        starting_point = self.season_manager.determine_starting_point()
        
        print(f"\nCurrent situation:")
        print(f"  • Action: {starting_point['action']}")
        print(f"  • Recommendation: {starting_point['reason']}")
        
        if starting_point['action'] == 'load_genesis':
            print(f"\n⚠️  Genesis data needs to be loaded!")
            print(f"  1. Run: docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --historical")
            print(f"  2. Or manually configure dates in fetch_events_config.py:")
            print(f"     START_DATE = datetime(2024, 7, 6)")
            print(f"     END_DATE = datetime(2026, 1, 5)")
            print(f"     MIN_VOLUME = 100_000_000")
        
        elif starting_point['action'] == 'catch_up':
            print(f"\n⚠️  System is behind!")
            if starting_point['missing_periods']:
                print(f"  Missing periods: {len(starting_point['missing_periods'])}")
                if len(starting_point['missing_periods']) <= 5:
                    for period in starting_point['missing_periods']:
                        print(f"    • {period}")
                else:
                    print(f"    • {starting_point['missing_periods'][0]}")
                    print(f"    • ... {len(starting_point['missing_periods']) - 2} more ...")
                    print(f"    • {starting_point['missing_periods'][-1]}")
            print(f"\n  💡 Tip: Run with --catch-up flag to load missing data")
        
        elif starting_point['action'] == 'continue_current':
            print(f"\n✅ System is up to date!")
            print(f"  Current season: {starting_point['season_name']}")
            print(f"  Ready for daily pipeline")
        
        print("="*70)
        
        return starting_point
    
    def run_daily_pipeline(self, target_date: date = None, force: bool = False, auto_check: bool = True) -> Dict:
        """
        Run complete daily data pipeline
        
        Args:
            target_date: Date to load data for (default: today)
            force: Force reload even if data exists
            
        Returns:
            Dict with pipeline results
        """
        if target_date is None:
            target_date = date.today()
        
        # Check system state before running (unless disabled)
        if auto_check and not force:
            starting_point = self.check_system_state()
            
            # If need Genesis, warn and ask
            if starting_point['action'] == 'load_genesis':
                print("\n❌ Cannot run daily pipeline - Genesis data not loaded!")
                print("   Run with --historical flag first")
                return {
                    'success': False,
                    'error': 'Genesis data not loaded',
                    'recommendation': starting_point
                }
            
            # If behind, warn but can continue
            if starting_point['action'] == 'catch_up' and not force:
                print("\n⚠️  Warning: System is behind current date")
                print("   This will load data for today, but you have missing periods")
                print("   Consider running with --catch-up flag first")
                print("\n   Continuing anyway in 3 seconds...")
                time.sleep(3)
        
        print("\n" + "="*70)
        print("📊 DAILY DATA PIPELINE")
        print("="*70)
        print(f"Date: {target_date}")
        print(f"Mode: {'DRY RUN' if self.dry_run else 'PRODUCTION'}")
        print(f"Database: {'Local PostgreSQL' if self.use_local_db else 'Supabase'}")
        print("="*70)
        
        # Get season info
        season_info = self.season_manager.get_season_for_date(target_date)
        print(f"\nSeason: {season_info['season_name']}")
        if season_info['day']:
            print(f"Day: {season_info['day']}/{self.season_manager.SEASON_LENGTH_DAYS}")
        
        # Check if data already loaded
        if not force:
            already_loaded = []
            for script_key in self.scripts.keys():
                if self.season_manager.is_data_loaded_for_date(target_date, script_key):
                    already_loaded.append(script_key)
            
            if already_loaded:
                print(f"\n⚠️  Data already loaded for {target_date}:")
                for script_key in already_loaded:
                    print(f"  • {self.scripts[script_key]['name']}")
                
                if len(already_loaded) == len(self.scripts):
                    print(f"\n✅ All data already loaded. Use --force to reload.")
                    return {'success': True, 'skipped': True}
        
        # ВАЖНО: Разная логика загрузки для разных типов данных
        # - Events & Markets: свежие данные (текущий день)
        # - Redemptions, Positions, Leaderboard: данные 3 дня назад
        
        # Причина: данные о redemptions и позициях могут появляться с задержкой
        # Пользователи не сразу забирают выигрыши, нужно время для окончательного расчета
        
        # Run pipeline
        results = {}
        total_duration = 0
        pipeline_start = time.time()
        
        for script_key in ['events', 'redemptions', 'positions', 'leaderboard']:
            # Check if already loaded (unless force)
            if not force and self.season_manager.is_data_loaded_for_date(target_date, script_key):
                print(f"\n⏭️  Skipping {self.scripts[script_key]['name']} (already loaded)")
                results[script_key] = {'success': True, 'skipped': True}
                continue
            
            # Установить контекст в зависимости от типа скрипта
            season_context = SeasonContext()
            
            if script_key in ['events']:
                # Events & Markets: свежие данные (текущий день)
                season_context.set_current_season(target_date, season_info, lag_days=0)
                print(f"\n📅 {self.scripts[script_key]['name']}: Using CURRENT data (today)")
                
                # Prepare config for events (if AUTO_SEASON enabled)
                self._prepare_config_for_season(target_date)
                
            else:
                # Redemptions, Positions, Leaderboard: данные 3 дня назад
                LAG_DAYS = 3
                season_context.set_current_season(target_date, lag_days=LAG_DAYS)
                print(f"\n📅 {self.scripts[script_key]['name']}: Using LAGGED data ({LAG_DAYS} days ago)")
            
            # Run script
            result = self.run_script(script_key)
            results[script_key] = result
            total_duration += result['duration']
            
            # Mark as loaded if successful
            if result['success'] and not self.dry_run:
                self.season_manager.mark_data_loaded(
                    script_name=script_key,
                    load_date=target_date,
                    record_count=result['records']
                )
                print(f"✅ Marked {script_key} as loaded in season tracker")
            
            # If script failed, decide whether to continue
            if not result['success']:
                print(f"\n⚠️  {self.scripts[script_key]['name']} failed, but continuing...")
                # TODO: Could add --stop-on-error flag
        
        pipeline_duration = time.time() - pipeline_start
        
        # Summary
        print("\n" + "="*70)
        print("📈 PIPELINE SUMMARY")
        print("="*70)
        
        success_count = sum(1 for r in results.values() if r.get('success', False))
        failed_count = sum(1 for r in results.values() if not r.get('success', False) and not r.get('skipped', False))
        skipped_count = sum(1 for r in results.values() if r.get('skipped', False))
        
        print(f"✅ Successful: {success_count}/{len(self.scripts)}")
        if failed_count > 0:
            print(f"❌ Failed: {failed_count}/{len(self.scripts)}")
        if skipped_count > 0:
            print(f"⏭️  Skipped: {skipped_count}/{len(self.scripts)}")
        
        print(f"\n⏱️  Total Duration: {pipeline_duration:.1f} seconds ({pipeline_duration/60:.1f} minutes)")
        
        print("\nScript Results:")
        for script_key, result in results.items():
            name = self.scripts[script_key]['name']
            if result.get('skipped'):
                print(f"  • {name}: ⏭️  SKIPPED")
            elif result['success']:
                print(f"  • {name}: ✅ SUCCESS ({result['duration']:.1f}s)")
            else:
                print(f"  • {name}: ❌ FAILED - {result['error']}")
        
        print("="*70)
        
        # Clear season context after pipeline completes
        season_context.clear()
        
        return {
            'success': failed_count == 0,
            'results': results,
            'duration': pipeline_duration,
            'date': target_date
        }
    
    def run_historical_load(self) -> Dict:
        """
        Run historical data load for genesis season
        
        Returns:
            Dict with load results
        """
        print("\n" + "="*70)
        print("🕰️  HISTORICAL DATA LOAD (GENESIS SEASON)")
        print("="*70)
        
        # Check if needed
        if not self.season_manager.needs_historical_load():
            print("✅ Historical data already loaded")
            return {'success': True, 'skipped': True}
        
        print("Loading historical data for genesis season...")
        print("This will load all data before season system started.")
        
        if self.dry_run:
            print("\n🔍 DRY RUN - Skipping historical load")
            return {'success': True, 'skipped': True}
        
        # TODO: Implement historical data loading logic
        # This could involve:
        # 1. Fetching older data with date ranges
        # 2. Loading from backups
        # 3. Batch processing of archived data
        
        print("\n⚠️  Historical load not yet implemented")
        print("Please manually load genesis data using individual scripts with date ranges")
        
        return {'success': False, 'error': 'Not implemented'}
    
    def print_status(self):
        """Print current status and season info"""
        self.season_manager.print_status()


def main():
    parser = argparse.ArgumentParser(
        description='Daily Data Scheduler - Orchestrates PolyStars data pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check system state (recommended first run)
  python scripts/daily_scheduler.py --check
  
  # Run daily pipeline
  python scripts/daily_scheduler.py --run
  
  # Check status
  python scripts/daily_scheduler.py --status
  
  # Load historical data (Genesis)
  python scripts/daily_scheduler.py --historical
  
  # Catch up missing data
  python scripts/daily_scheduler.py --catch-up
  
  # Force reload today's data
  python scripts/daily_scheduler.py --run --force
  
  # Dry run (test without executing)
  python scripts/daily_scheduler.py --run --dry-run
  
  # Use with Docker
  docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --check
  docker exec polystars_scheduler python /app/scripts/daily_scheduler.py --run
        """
    )
    
    parser.add_argument('--run', action='store_true', help='Run daily data pipeline')
    parser.add_argument('--status', action='store_true', help='Show current status')
    parser.add_argument('--check', action='store_true', help='Check system state and recommendations')
    parser.add_argument('--historical', action='store_true', help='Load historical (genesis) data')
    parser.add_argument('--catch-up', action='store_true', help='Catch up missing data')
    parser.add_argument('--force', action='store_true', help='Force reload even if data exists')
    parser.add_argument('--dry-run', action='store_true', help='Dry run (test without executing)')
    parser.add_argument('--local', action='store_true', default=True, help='Use local PostgreSQL (default)')
    parser.add_argument('--supabase', action='store_true', help='Use Supabase instead of local')
    parser.add_argument('--no-auto-check', action='store_true', help='Disable automatic system check before run')
    
    args = parser.parse_args()
    
    # Determine database
    use_local_db = not args.supabase
    
    # Create scheduler
    scheduler = DailyScheduler(use_local_db=use_local_db, dry_run=args.dry_run)
    
    if args.check:
        scheduler.check_system_state()
    
    elif args.status:
        scheduler.print_status()
    
    elif args.run:
        result = scheduler.run_daily_pipeline(
            force=args.force,
            auto_check=not args.no_auto_check
        )
        sys.exit(0 if result.get('success', False) else 1)
    
    elif args.historical:
        result = scheduler.run_historical_load()
        sys.exit(0 if result['success'] else 1)
    
    elif args.catch_up:
        print("⚠️  Catch-up mode not yet fully implemented")
        print("   For now, run missing dates manually with --run")
        scheduler.check_system_state()
    
    else:
        print("Use --help for usage information")
        print("\n🚀 Quick start (new server):")
        print("  1. Check system state:")
        print("     python scripts/daily_scheduler.py --check")
        print("")
        print("  2. If empty, load Genesis:")
        print("     python scripts/daily_scheduler.py --historical")
        print("")
        print("  3. Run daily pipeline:")
        print("     python scripts/daily_scheduler.py --run")


if __name__ == '__main__':
    main()
