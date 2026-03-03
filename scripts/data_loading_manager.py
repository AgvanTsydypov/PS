"""
Data Loading Manager - Simplified (no seasons)

STRUCTURE:
==========
- Genesis: Historical data (2024-07-06 to 2026-01-05) with 100M filter
- Daily: Current data (after Genesis) with 5M filter

DATE LOGIC:
===========
- Events/Markets: Yesterday (completed day)
- Redemptions/Positions/Leaderboard: 3 days ago

TESTING MODE:
=============
For faster testing, configure these parameters in this file:

1. MAX_EVENTS_LIMIT - Limit total number of events:
    MAX_EVENTS_LIMIT = 100    # Quick test (~5 min)
    MAX_EVENTS_LIMIT = 1000   # Medium test (~15 min)
    MAX_EVENTS_LIMIT = None   # Full load (production)

2. MAX_VOLUME_FILTER - Exclude large events (in USD):
    MAX_VOLUME_FILTER = 150_000_000  # Skip events over 150M USD
    MAX_VOLUME_FILTER = None         # No maximum limit (production)

These filters help speed up testing by reducing data volume.

Usage:
    from scripts.data_loading_manager import DataLoadingManager
    
    manager = DataLoadingManager()
    
    # Check if Genesis needed
    if manager.needs_genesis_load():
        print("Load Genesis first")
    
    # Get today's loading dates
    dates = manager.get_loading_dates()
    # dates = {'events_date': yesterday, 'redemptions_date': 3_days_ago}
"""

import psycopg2
import psycopg2.extras
from datetime import date, timedelta
from typing import Dict, Optional
import os
from dotenv import load_dotenv

load_dotenv()

# Genesis period definition
# GENESIS_START_DATE = date(2024, 7, 6)
# GENESIS_END_DATE = date(2026, 1, 5)

# For testing - use shorter period:
GENESIS_START_DATE = date(2024, 6, 1)
GENESIS_END_DATE = date(2026, 2, 6)

GENESIS_MIN_VOLUME = 5_000  # 100M
DAILY_MIN_VOLUME = 5_000  # 5M

# Data lag configuration (in days)
# How many days to wait before loading data (allows data to finalize)
EVENTS_LAG_DAYS = 1        # Events: 1 days ago
DATA_LAG_DAYS = 4          # Redemptions/Positions/Leaderboard: 4 days after events

# OPTIONAL: Limit number of events for testing (None = unlimited)
# Set this to speed up testing with smaller datasets
# Examples:
#   - 100 events for quick test (few minutes)
#   - 1000 events for medium test (10-15 minutes)
#   - None for full load (production)
MAX_EVENTS_LIMIT = 3  # Change to number for testing, e.g., 1000

# OPTIONAL: Maximum event volume filter (in USD) - for testing
# Set this to exclude very large events that may take longer to process
# Examples:
#   - 150_000_000 (150M) to exclude events over 150M USD
#   - None for no maximum limit (production)
MAX_VOLUME_FILTER = 10_000  # Change to number for testing, e.g., 150_000_000


class DataLoadingManager:
    """Simplified data loading manager without seasons"""
    
    def __init__(self, use_local_db: bool = True):
        self.use_local_db = use_local_db
        self.connection_params = self._get_db_params()
        self._ensure_tables()
    
    def _get_db_params(self):
        """Get database connection parameters"""
        if self.use_local_db:
            # Get SSL mode from environment (require for Managed DB, prefer for local testing)
            ssl_mode = os.getenv('DB_SSLMODE', 'require')
            
            return {
                'host': os.getenv('LOCAL_DB_HOST', os.getenv('DB_HOST')),
                'port': int(os.getenv('LOCAL_DB_PORT', os.getenv('DB_PORT', 5432))),
                'database': os.getenv('LOCAL_DB_NAME', os.getenv('DB_NAME')),
                'user': os.getenv('LOCAL_DB_USER', os.getenv('DB_USER')),
                'password': os.getenv('LOCAL_DB_PASSWORD', os.getenv('DB_PASSWORD')),
                'sslmode': ssl_mode  # Configurable SSL mode
            }
        else:
            # Supabase
            raise NotImplementedError("Supabase connection not implemented")
    
    def _ensure_tables(self):
        """Ensure tracking tables exist"""
        try:
            conn = psycopg2.connect(**self.connection_params)
            cursor = conn.cursor()
            
            # Check if data_loads table exists
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'data_loads'
                )
            """)
            
            table_exists = cursor.fetchone()[0]
            
            if not table_exists:
                print("⚠️  Table 'data_loads' not found - creating...")
                
                # Create table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS data_loads (
                        id SERIAL PRIMARY KEY,
                        load_date DATE NOT NULL UNIQUE,
                        
                        events_loaded BOOLEAN DEFAULT FALSE,
                        redemptions_loaded BOOLEAN DEFAULT FALSE,
                        positions_loaded BOOLEAN DEFAULT FALSE,
                        leaderboard_loaded BOOLEAN DEFAULT FALSE,
                        
                        events_loaded_at TIMESTAMPTZ,
                        redemptions_loaded_at TIMESTAMPTZ,
                        positions_loaded_at TIMESTAMPTZ,
                        leaderboard_loaded_at TIMESTAMPTZ,
                        
                        events_count INTEGER DEFAULT 0,
                        redemptions_count INTEGER DEFAULT 0,
                        positions_count INTEGER DEFAULT 0,
                        leaderboard_count INTEGER DEFAULT 0,
                        
                        load_type VARCHAR(20) DEFAULT 'daily' CHECK (load_type IN ('genesis', 'daily')),
                        
                        events_error TEXT,
                        redemptions_error TEXT,
                        positions_error TEXT,
                        leaderboard_error TEXT,
                        
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                
                conn.commit()
                print("✅ Table 'data_loads' created")

            # Ensure season events table exists for dedicated lifecycle logs.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS season_events_log (
                    id BIGSERIAL PRIMARY KEY,
                    event_name TEXT NOT NULL,
                    season_id INTEGER,
                    details TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_season_events_log_created_at
                ON season_events_log(created_at DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_season_events_log_event_name
                ON season_events_log(event_name)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_season_events_log_season_id
                ON season_events_log(season_id)
            """)
            conn.commit()
            
            cursor.close()
            conn.close()
            
        except Exception as e:
            print(f"⚠️  Could not ensure tables: {e}")

    def get_connection(self):
        """Get a DB connection using manager settings."""
        return psycopg2.connect(**self.connection_params)

    def log_season_update(self, event_name: str, season_id: int, details: str = ""):
        """
        Log season lifecycle events into dedicated season_events_log table.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO season_events_log (event_name, season_id, details)
                VALUES (%s, %s, %s)
            """, (event_name, season_id, details or None))
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    
    def get_loading_dates(self, reference_date: date = None) -> Dict:
        """
        Calculate dates for data loading
        
        Args:
            reference_date: Reference date (default: today)
            
        Returns:
            Dict with events_date (EVENTS_LAG_DAYS ago) and redemptions_date (DATA_LAG_DAYS ago)
        """
        if reference_date is None:
            reference_date = date.today()
        
        events_date = reference_date - timedelta(days=EVENTS_LAG_DAYS)
        redemptions_date = reference_date - timedelta(days=DATA_LAG_DAYS)
        
        return {
            'events_date': events_date,
            'redemptions_date': redemptions_date,
            'reference_date': reference_date
        }
    
    def has_any_data(self) -> bool:
        """Check if any data exists in events table"""
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT COUNT(*) FROM events")
            count = cursor.fetchone()[0]
            return count > 0
        finally:
            cursor.close()
            conn.close()
    
    def needs_genesis_load(self) -> bool:
        """Check if Genesis data needs to be loaded"""
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT COUNT(*) FROM data_loads 
                WHERE load_type = 'genesis' AND events_loaded = TRUE
            """)
            count = cursor.fetchone()[0]
            return count == 0
        finally:
            cursor.close()
            conn.close()
    
    def is_data_loaded_for_date(self, load_date: date, data_type: str) -> bool:
        """
        Check if data is loaded for a specific date and type
        
        Args:
            load_date: Date to check
            data_type: Type of data (events, redemptions, positions, leaderboard)
            
        Returns:
            True if data is loaded
        """
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            column = f'{data_type}_loaded'
            cursor.execute(f"""
                SELECT {column} FROM data_loads
                WHERE load_date = %s
            """, (load_date,))
            
            result = cursor.fetchone()
            return result[0] if result else False
        finally:
            cursor.close()
            conn.close()
    
    def mark_data_loaded(self, data_type: str, load_date: date, record_count: int = 0, 
                        markets_count: int = 0, load_type: str = 'daily'):
        """
        Mark data as loaded for a specific date
        
        Args:
            data_type: Type of data (events, redemptions, positions, leaderboard)
            load_date: Date the data is for
            record_count: Number of records loaded
            markets_count: Number of markets loaded (only for events)
            load_type: Type of load (genesis, daily)
        """
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            column_loaded = f'{data_type}_loaded'
            column_loaded_at = f'{data_type}_loaded_at'
            column_count = f'{data_type}_count'
            
            # For events, also update markets_count
            if data_type == 'events' and markets_count > 0:
                cursor.execute(f"""
                    INSERT INTO data_loads (
                        load_date, {column_loaded}, {column_loaded_at}, {column_count}, 
                        markets_count, load_type
                    )
                    VALUES (%s, TRUE, NOW(), %s, %s, %s)
                    ON CONFLICT (load_date) 
                    DO UPDATE SET
                        {column_loaded} = TRUE,
                        {column_loaded_at} = NOW(),
                        {column_count} = %s,
                        markets_count = %s,
                        load_type = %s,
                        updated_at = NOW()
                """, (load_date, record_count, markets_count, load_type, 
                     record_count, markets_count, load_type))
            else:
                cursor.execute(f"""
                    INSERT INTO data_loads (
                        load_date, {column_loaded}, {column_loaded_at}, {column_count}, load_type
                    )
                    VALUES (%s, TRUE, NOW(), %s, %s)
                    ON CONFLICT (load_date) 
                    DO UPDATE SET
                        {column_loaded} = TRUE,
                        {column_loaded_at} = NOW(),
                        {column_count} = %s,
                        load_type = %s,
                        updated_at = NOW()
                """, (load_date, record_count, load_type, record_count, load_type))
            
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    
    def get_volume_filter(self, is_genesis: bool = False) -> int:
        """Get appropriate volume filter"""
        return GENESIS_MIN_VOLUME if is_genesis else DAILY_MIN_VOLUME
    
    def get_events_limit(self) -> Optional[int]:
        """
        Get events limit for testing
        
        Returns:
            Number of events to limit (None = unlimited)
        """
        return MAX_EVENTS_LIMIT
    
    def get_max_volume_filter(self) -> Optional[int]:
        """
        Get maximum volume filter for testing
        
        Returns:
            Maximum volume in USD (None = no limit)
        """
        return MAX_VOLUME_FILTER
    
    def get_table_count(self, table_name: str) -> int:
        """
        Get count of records in a table
        
        Args:
            table_name: Name of the table (events, markets, redemptions, 
                       user_closed_positions, trader_leaderboard)
        
        Returns:
            Number of records in the table
        """
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            return count
        finally:
            cursor.close()
            conn.close()
    
    def get_missing_dates(self, start_from: date = None, up_to: date = None) -> list:
        """
        Get list of dates that need data loading
        
        Args:
            start_from: Start checking from this date (default: day after Genesis)
            up_to: Check up to this date (default: yesterday)
            
        Returns:
            List of dates that need data
        """
        if start_from is None:
            start_from = GENESIS_END_DATE + timedelta(days=1)
        
        if up_to is None:
            up_to = date.today() - timedelta(days=1)
        
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            # Get all dates that have been loaded for events
            cursor.execute("""
                SELECT load_date FROM data_loads
                WHERE events_loaded = TRUE
                  AND load_date BETWEEN %s AND %s
            """, (start_from, up_to))
            
            loaded_dates = set(row[0] for row in cursor.fetchall())
            
            # Find missing dates
            missing = []
            current = start_from
            while current <= up_to:
                if current not in loaded_dates:
                    missing.append(current)
                current += timedelta(days=1)
            
            return missing
            
        finally:
            cursor.close()
            conn.close()
    
    def get_incomplete_dates(self, start_from: date = None, up_to: date = None) -> list:
        """
        Get list of dates where events are loaded but other data is missing
        
        Args:
            start_from: Start checking from this date (default: day after Genesis)
            up_to: Check up to this date (default: yesterday)
        
        Returns:
            List of (date, missing_types) tuples
            Example: [(date(2026,2,7), ['redemptions', 'positions']), ...]
        """
        if start_from is None:
            start_from = GENESIS_END_DATE + timedelta(days=1)
        
        if up_to is None:
            up_to = date.today() - timedelta(days=1)
        
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        try:
            # Find dates where events are loaded but other data types are not
            cursor.execute("""
                SELECT 
                    load_date,
                    redemptions_loaded,
                    positions_loaded,
                    leaderboard_loaded
                FROM data_loads
                WHERE events_loaded = TRUE
                  AND load_date BETWEEN %s AND %s
                  AND (redemptions_loaded = FALSE 
                    OR positions_loaded = FALSE 
                    OR leaderboard_loaded = FALSE)
                ORDER BY load_date
            """, (start_from, up_to))
            
            incomplete = []
            for row in cursor.fetchall():
                missing_types = []
                if not row['redemptions_loaded']:
                    missing_types.append('redemptions')
                if not row['positions_loaded']:
                    missing_types.append('positions')
                if not row['leaderboard_loaded']:
                    missing_types.append('leaderboard')
                
                incomplete.append((row['load_date'], missing_types))
            
            return incomplete
            
        finally:
            cursor.close()
            conn.close()
    
    def get_last_loaded_date(self, data_type: str = 'events') -> Optional[date]:
        """
        Get the last date that has data loaded
        
        Args:
            data_type: Data type to check (events, redemptions, etc)
            
        Returns:
            Last loaded date or None if no data
        """
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            column = f'{data_type}_loaded'
            cursor.execute(f"""
                SELECT MAX(load_date) FROM data_loads
                WHERE {column} = TRUE AND load_type = 'daily'
            """)
            
            result = cursor.fetchone()
            return result[0] if result[0] else None
            
        finally:
            cursor.close()
            conn.close()
    
    def print_status(self):
        """Print current status"""
        print("=" * 70)
        print("📊 DATA LOADING STATUS")
        print("=" * 70)
        
        # Check for testing mode
        testing_mode = False
        if MAX_EVENTS_LIMIT or MAX_VOLUME_FILTER:
            testing_mode = True
            print(f"\n⚠️  TESTING MODE ACTIVE:")
            if MAX_EVENTS_LIMIT:
                print(f"   • Event count limit: {MAX_EVENTS_LIMIT:,} events per run")
            if MAX_VOLUME_FILTER:
                print(f"   • Max volume filter: ${MAX_VOLUME_FILTER:,} (excludes events over this)")
            print(f"   (Set both to None for full production load)")
        
        # Check Genesis
        genesis_loaded = not self.needs_genesis_load()
        print(f"\nGenesis ({GENESIS_START_DATE} → {GENESIS_END_DATE}):")
        print(f"  Status: {'✅ Loaded' if genesis_loaded else '❌ Not loaded'}")
        if genesis_loaded:
            print(f"  Filter: {GENESIS_MIN_VOLUME:,} (100M)")
        
        # Get loading dates
        dates = self.get_loading_dates()
        print(f"\nToday's Loading Dates ({dates['reference_date']}):")
        print(f"  Events/Markets: {dates['events_date']} (yesterday)")
        print(f"  Redemptions/Positions/Leaderboard: {dates['redemptions_date']} (3 days ago)")
        
        # Check if today's data loaded
        events_date = dates['events_date']
        redemptions_date = dates['redemptions_date']
        
        print(f"\nData Status:")
        for data_type in ['events', 'redemptions', 'positions', 'leaderboard']:
            check_date = events_date if data_type == 'events' else redemptions_date
            loaded = self.is_data_loaded_for_date(check_date, data_type)
            status = "✅ Loaded" if loaded else "⏳ Pending"
            print(f"  • {data_type.capitalize()}: {status}")
        
        # Check for missing dates
        if genesis_loaded:
            last_loaded = self.get_last_loaded_date('events')
            if last_loaded:
                missing = self.get_missing_dates(
                    start_from=GENESIS_END_DATE + timedelta(days=1),
                    up_to=dates['events_date']
                )
                
                if missing:
                    print(f"\n⚠️  Missing Data:")
                    print(f"  Last loaded: {last_loaded}")
                    print(f"  Missing days: {len(missing)}")
                    if len(missing) <= 5:
                        for d in missing:
                            print(f"    • {d}")
                    else:
                        print(f"    • {missing[0]}")
                        print(f"    • ... {len(missing) - 2} more ...")
                        print(f"    • {missing[-1]}")
                    print(f"\n  💡 Run with --catch-up to load missing data")
        
        print("=" * 70)


# ============================================================================
# CLI for testing
# ============================================================================
if __name__ == '__main__':
    import sys
    
    manager = DataLoadingManager(use_local_db=True)
    
    if len(sys.argv) > 1 and sys.argv[1] == '--status':
        manager.print_status()
    else:
        print("Data Loading Manager")
        print("=" * 70)
        
        if manager.needs_genesis_load():
            print("❌ Genesis data NOT loaded")
            print(f"   Load data for: {GENESIS_START_DATE} to {GENESIS_END_DATE}")
            print(f"   Filter: MIN_VOLUME = {GENESIS_MIN_VOLUME:,}")
        else:
            print("✅ Genesis data loaded")
        
        dates = manager.get_loading_dates()
        print(f"\nToday's dates:")
        print(f"  • Events: {dates['events_date']}")
        print(f"  • Redemptions: {dates['redemptions_date']}")
        
        print("\nUsage:")
        print("  python scripts/data_loading_manager.py --status")
