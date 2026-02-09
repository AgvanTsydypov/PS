"""
Season Manager for PolyStars Data Pipeline

Manages data loading seasons with historical backfill support.

SEASON STRUCTURE:
=================
- genesis: Historical data (before season system)
- season1, season2, ...: Current seasons (10 days each)

Each season tracks:
- Start date, end date
- Current day (1-10)
- Data loading status per day
- Which scripts ran successfully

Usage:
    from scripts.season_manager import SeasonManager
    
    manager = SeasonManager()
    
    # Check current season
    season_info = manager.get_current_season()
    print(f"Season: {season_info['season_name']}, Day: {season_info['day']}")
    
    # Check if need to load data
    if manager.should_load_data():
        # Load data for current day
        manager.mark_data_loaded('events')
"""

import os
import sys
from datetime import datetime, timedelta, date
from typing import Dict, Optional, List, Tuple
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Constants
SEASON_LENGTH_DAYS = 10  # Each season is 10 days
GENESIS_START_DATE = date(2024, 7, 6)  # Genesis period start
GENESIS_END_DATE = date(2026, 1, 5)  # Genesis period end (last day of historical data)


class SeasonManager:
    """
    Manages data loading seasons and tracks progress
    """
    
    def __init__(self, use_local_db: bool = True):
        """
        Initialize Season Manager
        
        Args:
            use_local_db: Use local PostgreSQL (default: True)
        """
        self.use_local_db = use_local_db
        self.connection_params = self._get_db_params()
        self._ensure_season_tables()
    
    def _get_db_params(self) -> Dict:
        """Get database connection parameters"""
        if self.use_local_db:
            return {
                'host': os.getenv('LOCAL_DB_HOST', 'localhost'),
                'port': int(os.getenv('LOCAL_DB_PORT', 5432)),
                'database': os.getenv('LOCAL_DB_NAME', 'polymarket'),
                'user': os.getenv('LOCAL_DB_USER', 'postgres'),
                'password': os.getenv('LOCAL_DB_PASSWORD', '1234')
            }
        else:
            raise NotImplementedError("Supabase not implemented yet")
    
    def _ensure_season_tables(self):
        """Create season tracking tables if they don't exist"""
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            # Table to track seasons
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS seasons (
                    id SERIAL PRIMARY KEY,
                    season_name VARCHAR(50) UNIQUE NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    season_type VARCHAR(20) NOT NULL CHECK (season_type IN ('genesis', 'regular')),
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                
                CREATE INDEX IF NOT EXISTS idx_seasons_name ON seasons(season_name);
                CREATE INDEX IF NOT EXISTS idx_seasons_dates ON seasons(start_date, end_date);
            """)
            
            # Table to track daily data loads
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS season_data_loads (
                    id SERIAL PRIMARY KEY,
                    season_name VARCHAR(50) NOT NULL,
                    load_date DATE NOT NULL,
                    day_in_season INTEGER NOT NULL,
                    
                    -- Track which scripts ran
                    events_loaded BOOLEAN DEFAULT FALSE,
                    redemptions_loaded BOOLEAN DEFAULT FALSE,
                    positions_loaded BOOLEAN DEFAULT FALSE,
                    leaderboard_loaded BOOLEAN DEFAULT FALSE,
                    
                    -- Timestamps
                    events_loaded_at TIMESTAMPTZ,
                    redemptions_loaded_at TIMESTAMPTZ,
                    positions_loaded_at TIMESTAMPTZ,
                    leaderboard_loaded_at TIMESTAMPTZ,
                    
                    -- Record counts
                    events_count INTEGER DEFAULT 0,
                    redemptions_count INTEGER DEFAULT 0,
                    positions_count INTEGER DEFAULT 0,
                    leaderboard_count INTEGER DEFAULT 0,
                    
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    
                    CONSTRAINT unique_season_date UNIQUE(season_name, load_date)
                );
                
                CREATE INDEX IF NOT EXISTS idx_season_data_loads_season ON season_data_loads(season_name);
                CREATE INDEX IF NOT EXISTS idx_season_data_loads_date ON season_data_loads(load_date DESC);
            """)
            
            conn.commit()
            
        finally:
            cursor.close()
            conn.close()
    
    def get_season_for_date(self, target_date: date) -> Dict:
        """
        Get season information for a specific date
        
        Args:
            target_date: The date to check
            
        Returns:
            Dict with season info:
            {
                'season_name': 'season1',
                'season_type': 'regular',
                'start_date': date(2026, 2, 10),
                'end_date': date(2026, 2, 19),
                'day': 5,  # Day within season (1-10)
                'days_remaining': 5
            }
        """
        # Check if date is in genesis period
        if target_date <= GENESIS_END_DATE:
            return {
                'season_name': 'genesis',
                'season_type': 'genesis',
                'start_date': GENESIS_START_DATE,
                'end_date': GENESIS_END_DATE,
                'day': None,
                'days_remaining': 0
            }
        
        # Calculate which season this date belongs to
        # Season 1 starts the day after Genesis ends
        first_season_start = GENESIS_END_DATE + timedelta(days=1)
        days_since_first_season = (target_date - first_season_start).days
        season_number = (days_since_first_season // SEASON_LENGTH_DAYS) + 1
        
        # Calculate season boundaries
        season_start = first_season_start + timedelta(days=(season_number - 1) * SEASON_LENGTH_DAYS)
        season_end = season_start + timedelta(days=SEASON_LENGTH_DAYS - 1)
        
        # Calculate day within season (1-10)
        day_in_season = (target_date - season_start).days + 1
        days_remaining = (season_end - target_date).days
        
        return {
            'season_name': f'season{season_number}',
            'season_type': 'regular',
            'start_date': season_start,
            'end_date': season_end,
            'day': day_in_season,
            'days_remaining': days_remaining
        }
    
    def get_current_season(self) -> Dict:
        """
        Get current season information
        
        Returns:
            Dict with current season info
        """
        today = date.today()
        return self.get_season_for_date(today)
    
    def _ensure_season_exists(self, season_info: Dict):
        """Ensure season record exists in database"""
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO seasons (season_name, start_date, end_date, season_type)
                VALUES (%(season_name)s, %(start_date)s, %(end_date)s, %(season_type)s)
                ON CONFLICT (season_name) DO NOTHING
            """, season_info)
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    
    def is_data_loaded_for_date(self, load_date: date, script_name: str) -> bool:
        """
        Check if data is already loaded for a specific date and script
        
        Args:
            load_date: Date to check
            script_name: Script name (events, redemptions, positions, leaderboard)
            
        Returns:
            True if data is loaded, False otherwise
        """
        season_info = self.get_season_for_date(load_date)
        
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        try:
            column_name = f'{script_name}_loaded'
            cursor.execute(f"""
                SELECT {column_name}
                FROM season_data_loads
                WHERE season_name = %s AND load_date = %s
            """, (season_info['season_name'], load_date))
            
            result = cursor.fetchone()
            if result:
                return result[column_name] or False
            return False
            
        finally:
            cursor.close()
            conn.close()
    
    def mark_data_loaded(self, script_name: str, load_date: date = None, record_count: int = 0):
        """
        Mark data as loaded for current date/season
        
        Args:
            script_name: Script name (events, redemptions, positions, leaderboard)
            load_date: Date to mark (default: today)
            record_count: Number of records loaded
        """
        if load_date is None:
            load_date = date.today()
        
        season_info = self.get_season_for_date(load_date)
        
        # Ensure season exists
        self._ensure_season_exists(season_info)
        
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            # Update or insert load record
            cursor.execute(f"""
                INSERT INTO season_data_loads (
                    season_name, load_date, day_in_season,
                    {script_name}_loaded, {script_name}_loaded_at, {script_name}_count
                )
                VALUES (%s, %s, %s, TRUE, NOW(), %s)
                ON CONFLICT (season_name, load_date) 
                DO UPDATE SET
                    {script_name}_loaded = TRUE,
                    {script_name}_loaded_at = NOW(),
                    {script_name}_count = EXCLUDED.{script_name}_count,
                    updated_at = NOW()
            """, (
                season_info['season_name'],
                load_date,
                season_info['day'] if season_info['day'] else 0,
                record_count
            ))
            
            conn.commit()
            
        finally:
            cursor.close()
            conn.close()
    
    def get_missing_days(self, season_name: str = None) -> List[Tuple[date, int]]:
        """
        Get list of days that still need data loading
        
        Args:
            season_name: Season to check (default: current season)
            
        Returns:
            List of tuples: [(date, day_in_season), ...]
        """
        if season_name is None:
            season_info = self.get_current_season()
            season_name = season_info['season_name']
        
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        try:
            # Get season dates
            cursor.execute("""
                SELECT start_date, end_date FROM seasons WHERE season_name = %s
            """, (season_name,))
            
            season = cursor.fetchone()
            if not season:
                return []
            
            # Get all dates that have been loaded
            cursor.execute("""
                SELECT load_date FROM season_data_loads
                WHERE season_name = %s
                  AND events_loaded = TRUE
                  AND redemptions_loaded = TRUE
                  AND positions_loaded = TRUE
                  AND leaderboard_loaded = TRUE
            """, (season_name,))
            
            loaded_dates = set(row['load_date'] for row in cursor.fetchall())
            
            # Calculate missing dates
            missing_dates = []
            current_date = season['start_date']
            day_num = 1
            
            while current_date <= season['end_date'] and current_date <= date.today():
                if current_date not in loaded_dates:
                    missing_dates.append((current_date, day_num))
                current_date += timedelta(days=1)
                day_num += 1
            
            return missing_dates
            
        finally:
            cursor.close()
            conn.close()
    
    def needs_historical_load(self) -> bool:
        """
        Check if historical (genesis) data needs to be loaded
        
        Returns:
            True if genesis data should be loaded, False otherwise
        """
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            # Check if genesis season exists and has any data
            cursor.execute("""
                SELECT COUNT(*) FROM season_data_loads
                WHERE season_name = 'genesis'
                  AND events_loaded = TRUE
            """)
            
            count = cursor.fetchone()[0]
            return count == 0
            
        finally:
            cursor.close()
            conn.close()
    
    def has_any_data(self) -> bool:
        """
        Check if database has ANY loaded data
        
        Returns:
            True if there's at least some data, False if completely empty
        """
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            # Check if there's any data in events table
            cursor.execute("SELECT COUNT(*) FROM events LIMIT 1")
            events_count = cursor.fetchone()[0]
            
            if events_count > 0:
                return True
            
            # Check if there's any season tracking
            cursor.execute("SELECT COUNT(*) FROM season_data_loads")
            loads_count = cursor.fetchone()[0]
            
            return loads_count > 0
            
        finally:
            cursor.close()
            conn.close()
    
    def get_last_loaded_season(self) -> Optional[Dict]:
        """
        Get information about the last loaded season
        
        Returns:
            Dict with last loaded season info or None if no data
        """
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        try:
            cursor.execute("""
                SELECT DISTINCT 
                    season_name,
                    MAX(load_date) as last_load_date,
                    COUNT(*) as days_loaded
                FROM season_data_loads
                WHERE events_loaded = TRUE
                GROUP BY season_name
                ORDER BY MAX(load_date) DESC
                LIMIT 1
            """)
            
            result = cursor.fetchone()
            if result:
                return dict(result)
            return None
            
        finally:
            cursor.close()
            conn.close()
    
    def determine_starting_point(self) -> Dict:
        """
        Automatically determine where to start loading data
        
        Returns:
            Dict with recommendation:
            {
                'action': 'load_genesis' | 'continue_current' | 'catch_up',
                'season_name': 'genesis' | 'season1' | etc,
                'reason': 'explanation',
                'missing_periods': []  # List of missing periods to load
            }
        """
        # Check if database is completely empty
        if not self.has_any_data():
            return {
                'action': 'load_genesis',
                'season_name': 'genesis',
                'reason': 'Database is empty - need to load historical data (Genesis)',
                'missing_periods': []
            }
        
        # Check if Genesis was loaded
        if self.needs_historical_load():
            return {
                'action': 'load_genesis',
                'season_name': 'genesis',
                'reason': 'No Genesis data found - need to load historical data first',
                'missing_periods': []
            }
        
        # Get current season
        current_season = self.get_current_season()
        
        # Get last loaded season
        last_loaded = self.get_last_loaded_season()
        
        if not last_loaded:
            return {
                'action': 'load_genesis',
                'season_name': 'genesis',
                'reason': 'No season data found - starting from Genesis',
                'missing_periods': []
            }
        
        # Check if we're up to date
        if last_loaded['season_name'] == current_season['season_name']:
            # Check for missing days in current season
            missing_days = self.get_missing_days(current_season['season_name'])
            
            if missing_days:
                return {
                    'action': 'catch_up',
                    'season_name': current_season['season_name'],
                    'reason': f"Current season has {len(missing_days)} missing day(s)",
                    'missing_periods': missing_days
                }
            else:
                return {
                    'action': 'continue_current',
                    'season_name': current_season['season_name'],
                    'reason': 'Up to date with current season',
                    'missing_periods': []
                }
        
        # We're behind - need to catch up
        missing_seasons = self._find_missing_seasons(
            last_loaded['season_name'],
            current_season['season_name']
        )
        
        return {
            'action': 'catch_up',
            'season_name': current_season['season_name'],
            'reason': f"Behind by {len(missing_seasons)} season(s) - need to catch up",
            'missing_periods': missing_seasons
        }
    
    def _find_missing_seasons(self, last_season_name: str, current_season_name: str) -> List[str]:
        """
        Find all missing seasons between last loaded and current
        
        Args:
            last_season_name: Last loaded season (e.g., 'season1')
            current_season_name: Current season (e.g., 'season3')
            
        Returns:
            List of missing season names (e.g., ['season2', 'season3'])
        """
        if last_season_name == 'genesis':
            last_num = 0
        else:
            last_num = int(last_season_name.replace('season', ''))
        
        if current_season_name == 'genesis':
            return []
        
        current_num = int(current_season_name.replace('season', ''))
        
        missing = []
        for num in range(last_num + 1, current_num + 1):
            missing.append(f'season{num}')
        
        return missing
    
    def should_load_data_today(self, script_name: str = None) -> bool:
        """
        Check if data should be loaded today
        
        Args:
            script_name: Optional specific script to check
            
        Returns:
            True if data should be loaded, False otherwise
        """
        today = date.today()
        
        if script_name:
            return not self.is_data_loaded_for_date(today, script_name)
        
        # Check if any script hasn't run today
        for script in ['events', 'redemptions', 'positions', 'leaderboard']:
            if not self.is_data_loaded_for_date(today, script):
                return True
        
        return False
    
    def get_season_stats(self, season_name: str = None) -> Dict:
        """
        Get statistics for a season
        
        Args:
            season_name: Season to check (default: current season)
            
        Returns:
            Dict with season statistics
        """
        if season_name is None:
            season_info = self.get_current_season()
            season_name = season_info['season_name']
        
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        try:
            cursor.execute("""
                SELECT 
                    COUNT(*) as days_loaded,
                    SUM(CASE WHEN events_loaded THEN 1 ELSE 0 END) as days_with_events,
                    SUM(CASE WHEN redemptions_loaded THEN 1 ELSE 0 END) as days_with_redemptions,
                    SUM(CASE WHEN positions_loaded THEN 1 ELSE 0 END) as days_with_positions,
                    SUM(CASE WHEN leaderboard_loaded THEN 1 ELSE 0 END) as days_with_leaderboard,
                    SUM(events_count) as total_events,
                    SUM(redemptions_count) as total_redemptions,
                    SUM(positions_count) as total_positions,
                    SUM(leaderboard_count) as total_leaderboard,
                    MIN(load_date) as first_load_date,
                    MAX(load_date) as last_load_date
                FROM season_data_loads
                WHERE season_name = %s
            """, (season_name,))
            
            return dict(cursor.fetchone())
            
        finally:
            cursor.close()
            conn.close()
    
    def print_status(self):
        """Print current season status"""
        season_info = self.get_current_season()
        
        print("=" * 70)
        print("📅 SEASON STATUS")
        print("=" * 70)
        print(f"Current Season: {season_info['season_name']}")
        print(f"Season Type: {season_info['season_type']}")
        print(f"Date Range: {season_info['start_date']} to {season_info['end_date']}")
        
        if season_info['day']:
            print(f"Current Day: {season_info['day']}/{SEASON_LENGTH_DAYS}")
            print(f"Days Remaining: {season_info['days_remaining']}")
        
        # Check if data loaded today
        today = date.today()
        print(f"\nToday's Data Status ({today}):")
        for script in ['events', 'redemptions', 'positions', 'leaderboard']:
            loaded = self.is_data_loaded_for_date(today, script)
            status = "✅ Loaded" if loaded else "⏳ Pending"
            print(f"  • {script.capitalize()}: {status}")
        
        # Show season stats
        if season_info['season_name'] != 'genesis':
            stats = self.get_season_stats()
            print(f"\nSeason Statistics:")
            print(f"  • Days with data: {stats['days_loaded']}")
            print(f"  • Total events: {stats['total_events']:,}")
            print(f"  • Total redemptions: {stats['total_redemptions']:,}")
            print(f"  • Total positions: {stats['total_positions']:,}")
            print(f"  • Total leaderboard: {stats['total_leaderboard']:,}")
        
        # Check for missing days
        missing = self.get_missing_days()
        if missing:
            print(f"\n⚠️  Missing Data for {len(missing)} day(s):")
            for date_val, day_num in missing[:5]:
                print(f"  • Day {day_num}: {date_val}")
            if len(missing) > 5:
                print(f"  ... and {len(missing) - 5} more")
        
        # Check if need historical load
        if self.needs_historical_load():
            print(f"\n🔄 Historical load needed for 'genesis' season")
        
        print("=" * 70)


def main():
    """Test the season manager"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Season Manager - Track data loading by seasons')
    parser.add_argument('--status', action='store_true', help='Show current season status')
    parser.add_argument('--check-date', type=str, help='Check season for specific date (YYYY-MM-DD)')
    parser.add_argument('--mark-loaded', type=str, help='Mark script as loaded (events, redemptions, positions, leaderboard)')
    parser.add_argument('--missing', action='store_true', help='Show missing days for current season')
    
    args = parser.parse_args()
    
    manager = SeasonManager()
    
    if args.status:
        manager.print_status()
    
    elif args.check_date:
        target_date = datetime.strptime(args.check_date, '%Y-%m-%d').date()
        season_info = manager.get_season_for_date(target_date)
        print(f"Date {target_date}:")
        print(f"  Season: {season_info['season_name']}")
        print(f"  Type: {season_info['season_type']}")
        print(f"  Day: {season_info['day']}")
    
    elif args.mark_loaded:
        script_name = args.mark_loaded
        if script_name in ['events', 'redemptions', 'positions', 'leaderboard']:
            manager.mark_data_loaded(script_name)
            print(f"✅ Marked {script_name} as loaded for today")
        else:
            print(f"❌ Invalid script name. Use: events, redemptions, positions, leaderboard")
    
    elif args.missing:
        missing = manager.get_missing_days()
        if missing:
            print(f"Missing data for {len(missing)} day(s):")
            for date_val, day_num in missing:
                print(f"  • Day {day_num}: {date_val}")
        else:
            print("✅ No missing days in current season")
    
    else:
        print("Use --help for usage information")


if __name__ == '__main__':
    main()
