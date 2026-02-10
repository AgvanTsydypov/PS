"""
Simplified Data Loading Manager (No Seasons)

Manages daily data loading with historical backfill support.

STRUCTURE:
==========
- Genesis: Historical data (2024-07-06 to yyyy-mm-dd) with 100M filter
- Daily: Current data (after Genesis) with 5M filter

DATE LOGIC:
===========
- Events/Markets: Yesterday (completed day)
- Redemptions/Positions/Leaderboard: 3 days ago

Usage:
    from scripts.data_loading_manager import DataLoadingManager
    
    manager = DataLoadingManager()
    
    # Check if Genesis loaded
    if manager.needs_genesis_load():
        manager.load_genesis()
    
    # Get today's loading dates
    dates = manager.get_loading_dates()
"""

import os
import psycopg2
import psycopg2.extras
from datetime import date, datetime, timedelta
from typing import Dict, Optional
from dotenv import load_dotenv

load_dotenv()

# Constants
# GENESIS_START_DATE = date(2024, 7, 6)
# GENESIS_END_DATE = date(2026, 1, 5)
GENESIS_START_DATE = date(2025, 12, 1)
GENESIS_END_DATE = date(2026, 2, 6)
GENESIS_MIN_VOLUME = 100_000_000  # 100M
DAILY_MIN_VOLUME = 50_000_000  # 50M


class DataLoadingManager:
    """Simplified data loading manager without seasons"""
    
    def __init__(self, use_local_db: bool = True):
        self.use_local_db = use_local_db
        self.connection_params = self._get_db_params()
        self._ensure_tables()
    
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
            raise NotImplementedError("Supabase not implemented")
    
    def _ensure_tables(self):
        """Create tracking tables if they don't exist"""
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS data_loads (
                    id SERIAL PRIMARY KEY,
                    load_date DATE NOT NULL UNIQUE,
                    
                    -- Track which data types loaded
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
                    
                    -- Type: 'genesis' or 'daily'
                    load_type VARCHAR(20) DEFAULT 'daily',
                    
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                
                CREATE INDEX IF NOT EXISTS idx_data_loads_date ON data_loads(load_date DESC);
                CREATE INDEX IF NOT EXISTS idx_data_loads_type ON data_loads(load_type);
            """)
            
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    
    def get_loading_dates(self, reference_date: date = None) -> Dict:
        """
        Get dates for loading different data types
        
        Args:
            reference_date: Reference date (default: today)
            
        Returns:
            {
                'events_date': date,  # Yesterday
                'redemptions_date': date,  # 3 days ago
                'reference_date': date  # Today
            }
        """
        if reference_date is None:
            reference_date = date.today()
        
        # Events/Markets: Yesterday (completed day)
        events_date = reference_date - timedelta(days=1)
        
        # Redemptions/Positions/Leaderboard: 3 days ago
        redemptions_date = reference_date - timedelta(days=3)
        
        return {
            'events_date': events_date,
            'redemptions_date': redemptions_date,
            'reference_date': reference_date
        }
    
    def needs_genesis_load(self) -> bool:
        """Check if Genesis data needs to be loaded"""
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            # Check if we have any Genesis loads
            cursor.execute("""
                SELECT COUNT(*) FROM data_loads
                WHERE load_type = 'genesis' AND events_loaded = TRUE
            """)
            
            count = cursor.fetchone()[0]
            return count == 0
        finally:
            cursor.close()
            conn.close()
    
    def has_any_data(self) -> bool:
        """Check if database has any data"""
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT COUNT(*) FROM events LIMIT 1")
            return cursor.fetchone()[0] > 0
        finally:
            cursor.close()
            conn.close()
    
    def is_data_loaded_for_date(self, load_date: date, data_type: str) -> bool:
        """
        Check if data is loaded for a specific date
        
        Args:
            load_date: Date to check
            data_type: Type (events, redemptions, positions, leaderboard)
        """
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            column = f'{data_type}_loaded'
            cursor.execute(f"""
                SELECT {column} FROM data_loads WHERE load_date = %s
            """, (load_date,))
            
            result = cursor.fetchone()
            return result and result[0]
        finally:
            cursor.close()
            conn.close()
    
    def mark_data_loaded(self, data_type: str, load_date: date, record_count: int = 0, load_type: str = 'daily'):
        """
        Mark data as loaded
        
        Args:
            data_type: Type (events, redemptions, positions, leaderboard)
            load_date: Date loaded
            record_count: Number of records
            load_type: 'genesis' or 'daily'
        """
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        
        try:
            cursor.execute(f"""
                INSERT INTO data_loads (
                    load_date, load_type,
                    {data_type}_loaded, {data_type}_loaded_at, {data_type}_count
                )
                VALUES (%s, %s, TRUE, NOW(), %s)
                ON CONFLICT (load_date) DO UPDATE SET
                    {data_type}_loaded = TRUE,
                    {data_type}_loaded_at = NOW(),
                    {data_type}_count = EXCLUDED.{data_type}_count,
                    updated_at = NOW()
            """, (load_date, load_type, record_count))
            
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    
    def get_volume_filter(self, is_genesis: bool = False) -> int:
        """Get appropriate volume filter"""
        return GENESIS_MIN_VOLUME if is_genesis else DAILY_MIN_VOLUME
    
    def get_missing_dates(self, start_from: date = None, up_to: date = None) -> list:
        """
        Get list of dates that need data loading
        
        Args:
            start_from: Start checking from this date (default: day after Genesis)
            up_to: Check up to this date (default: yesterday)
            
        Returns:
            List of dates that need data: [(date, 'events'|'redemptions'), ...]
        """
        if start_from is None:
            start_from = GENESIS_END_DATE + timedelta(days=1)
        
        if up_to is None:
            # Up to yesterday (for events) or 3 days ago (for redemptions)
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


def main():
    """Test the manager"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Data Loading Manager')
    parser.add_argument('--status', action='store_true', help='Show current status')
    parser.add_argument('--check-genesis', action='store_true', help='Check if Genesis loaded')
    parser.add_argument('--dates', action='store_true', help='Show loading dates')
    
    args = parser.parse_args()
    
    manager = DataLoadingManager()
    
    if args.status:
        manager.print_status()
    
    elif args.check_genesis:
        if manager.needs_genesis_load():
            print("❌ Genesis data NOT loaded")
            print(f"   Load data for: {GENESIS_START_DATE} to {GENESIS_END_DATE}")
            print(f"   Filter: MIN_VOLUME = {GENESIS_MIN_VOLUME:,}")
        else:
            print("✅ Genesis data loaded")
    
    elif args.dates:
        dates = manager.get_loading_dates()
        print("📅 Loading Dates:")
        print(f"  Reference (today): {dates['reference_date']}")
        print(f"  Events: {dates['events_date']}")
        print(f"  Redemptions: {dates['redemptions_date']}")
    
    else:
        print("Use --help for usage")


if __name__ == '__main__':
    main()
