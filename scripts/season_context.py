"""
Season Context - Shared context for all fetching scripts

This module provides a centralized way to manage season information
across all data fetching scripts. It ensures that all scripts work
with the same season data.

Usage:
    from scripts.season_context import SeasonContext
    
    # In daily_scheduler
    context = SeasonContext()
    context.set_current_season(date(2026, 1, 10))
    
    # In any fetching script
    context = SeasonContext()
    if context.has_active_season():
        season_info = context.get_season_info()
        # Use season_info to filter data
"""

import os
import json
from datetime import date, datetime
from typing import Dict, Optional
from pathlib import Path


class SeasonContext:
    """
    Manages shared season context across all fetching scripts
    
    Uses a temporary JSON file to share season information between scripts
    during a single pipeline run.
    """
    
    # Context file location
    CONTEXT_FILE = Path("/tmp/polystars_season_context.json")
    
    def __init__(self):
        """Initialize season context"""
        self.context_data = self._load_context()
    
    def _load_context(self) -> Dict:
        """Load context from file"""
        if not self.CONTEXT_FILE.exists():
            return {}
        
        try:
            with open(self.CONTEXT_FILE, 'r') as f:
                data = json.load(f)
                
                # Convert date strings back to date objects
                if 'start_date' in data:
                    data['start_date'] = datetime.fromisoformat(data['start_date']).date()
                if 'end_date' in data:
                    data['end_date'] = datetime.fromisoformat(data['end_date']).date()
                if 'target_date' in data:
                    data['target_date'] = datetime.fromisoformat(data['target_date']).date()
                
                return data
        except Exception as e:
            print(f"⚠️  Warning: Could not load season context: {e}")
            return {}
    
    def _save_context(self):
        """Save context to file"""
        try:
            # Create parent directory if needed
            self.CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
            
            # Convert dates to strings for JSON
            data = self.context_data.copy()
            if 'start_date' in data:
                data['start_date'] = data['start_date'].isoformat()
            if 'end_date' in data:
                data['end_date'] = data['end_date'].isoformat()
            if 'target_date' in data:
                data['target_date'] = data['target_date'].isoformat()
            
            with open(self.CONTEXT_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"⚠️  Warning: Could not save season context: {e}")
    
    def set_current_season(self, target_date: date = None, season_info: Dict = None, lag_days: int = 0):
        """
        Set current season information with optional lag
        
        Args:
            target_date: Date to set season for (default: today)
            season_info: Pre-loaded season info (optional)
            lag_days: Number of days to look back (for delayed data loading)
        """
        if target_date is None:
            target_date = date.today()
        
        # Apply lag if specified
        lagged_date = target_date
        if lag_days > 0:
            from datetime import timedelta
            lagged_date = target_date - timedelta(days=lag_days)
        
        # Get season info if not provided
        if season_info is None:
            from scripts.season_manager import SeasonManager
            manager = SeasonManager()
            season_info = manager.get_season_for_date(lagged_date)
        
        # Store context with both current and lagged dates
        self.context_data = {
            'season_name': season_info['season_name'],
            'season_type': season_info['season_type'],
            'start_date': season_info['start_date'],
            'end_date': season_info['end_date'],
            'day': season_info.get('day'),
            'days_remaining': season_info.get('days_remaining'),
            'target_date': target_date,
            'lagged_date': lagged_date,
            'lag_days': lag_days,
            'set_at': datetime.now().isoformat()
        }
        
        self._save_context()
        
        print(f"✅ Season context set:")
        print(f"   Season: {self.context_data['season_name']}")
        print(f"   Type: {self.context_data['season_type']}")
        print(f"   Date range: {self.context_data['start_date']} to {self.context_data['end_date']}")
        if lag_days > 0:
            print(f"   Lag: {lag_days} days (loading data from {lagged_date})")
        if self.context_data['day']:
            print(f"   Day: {self.context_data['day']}/10")
    
    def has_active_season(self) -> bool:
        """Check if there's an active season context"""
        return bool(self.context_data)
    
    def get_season_info(self) -> Dict:
        """
        Get current season information
        
        Returns:
            Dict with season info or empty dict if no active season
        """
        return self.context_data.copy()
    
    def get_season_name(self) -> Optional[str]:
        """Get current season name (e.g., 'genesis', 'season1')"""
        return self.context_data.get('season_name')
    
    def get_season_type(self) -> Optional[str]:
        """Get season type ('genesis' or 'regular')"""
        return self.context_data.get('season_type')
    
    def get_date_range(self) -> tuple[Optional[date], Optional[date]]:
        """Get season date range as (start_date, end_date)"""
        return (
            self.context_data.get('start_date'),
            self.context_data.get('end_date')
        )
    
    def get_lagged_date(self) -> Optional[date]:
        """Get the lagged date (target_date - lag_days)"""
        return self.context_data.get('lagged_date')
    
    def get_lag_days(self) -> int:
        """Get the number of lag days"""
        return self.context_data.get('lag_days', 0)
    
    def is_genesis(self) -> bool:
        """Check if current season is genesis"""
        return self.context_data.get('season_type') == 'genesis'
    
    def clear(self):
        """Clear season context"""
        self.context_data = {}
        if self.CONTEXT_FILE.exists():
            self.CONTEXT_FILE.unlink()
        print("✅ Season context cleared")
    
    def print_status(self):
        """Print current context status"""
        if not self.has_active_season():
            print("❌ No active season context")
            return
        
        print("=" * 70)
        print("📋 ACTIVE SEASON CONTEXT")
        print("=" * 70)
        print(f"Season: {self.context_data['season_name']}")
        print(f"Type: {self.context_data['season_type']}")
        print(f"Date range: {self.context_data['start_date']} to {self.context_data['end_date']}")
        if self.context_data.get('day'):
            print(f"Day: {self.context_data['day']}/10")
        print(f"Set at: {self.context_data.get('set_at', 'Unknown')}")
        print("=" * 70)


def get_current_season_filter() -> Dict:
    """
    Convenience function to get current season filter parameters
    
    Returns:
        Dict with filter parameters:
        {
            'season_name': 'season1',
            'start_date': date(2026, 1, 6),
            'end_date': date(2026, 1, 15),
            'season_type': 'regular'
        }
    """
    context = SeasonContext()
    return context.get_season_info()


def should_use_season_filter() -> bool:
    """
    Check if scripts should use season-based filtering
    
    Returns:
        True if there's an active season context, False otherwise
    """
    context = SeasonContext()
    return context.has_active_season()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Season Context Manager')
    parser.add_argument('--set', type=str, help='Set season for date (YYYY-MM-DD)')
    parser.add_argument('--status', action='store_true', help='Show current context')
    parser.add_argument('--clear', action='store_true', help='Clear context')
    
    args = parser.parse_args()
    
    context = SeasonContext()
    
    if args.set:
        target_date = datetime.strptime(args.set, '%Y-%m-%d').date()
        context.set_current_season(target_date)
    
    elif args.status:
        context.print_status()
    
    elif args.clear:
        context.clear()
    
    else:
        print("Use --help for usage information")
