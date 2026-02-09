"""
Config Loader with Season Support

Automatically adjusts fetch_events_config based on current season.
Used by daily_scheduler to ensure correct date ranges.
"""

import os
import sys
from datetime import datetime, date

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.season_manager import SeasonManager
import scripts.fetch.fetch_events_config as config


def apply_season_dates(target_date: date = None, use_local_db: bool = True):
    """
    Apply season date ranges and volume filters to fetch_events_config
    
    Volume filtering:
    - Genesis: MIN_VOLUME = 100M (только крупные исторические события)
    - Seasons: MIN_VOLUME = 5M (более детальные текущие данные)
    
    Args:
        target_date: Date to get season for (default: today)
        use_local_db: Use local PostgreSQL (default: True)
    """
    if not config.AUTO_SEASON:
        print("⚠️  AUTO_SEASON is disabled in fetch_events_config.py")
        print("   Using manually configured dates and volume filter")
        return
    
    if target_date is None:
        target_date = date.today()
    
    # Get season info
    season_manager = SeasonManager(use_local_db=use_local_db)
    season_info = season_manager.get_season_for_date(target_date)
    
    # Apply dates to config
    config.START_DATE = datetime.combine(season_info['start_date'], datetime.min.time())
    config.END_DATE = datetime.combine(season_info['end_date'], datetime.max.time())
    
    # Apply volume filter based on season type
    if season_info['season_type'] == 'genesis':
        config.MIN_VOLUME = 100_000_000  # 100M for Genesis
        volume_desc = "$100M (historical)"
    else:
        config.MIN_VOLUME = 5_000_000  # 5M for regular seasons
        volume_desc = "$5M (current)"
    
    print(f"📅 Season-based configuration:")
    print(f"   Season: {season_info['season_name']}")
    print(f"   Type: {season_info['season_type']}")
    print(f"   Date range: {config.START_DATE.date()} to {config.END_DATE.date()}")
    print(f"   Min volume: {volume_desc}")
    
    if season_info['season_type'] == 'genesis':
        print(f"   📜 Loading historical data (Genesis)")
    else:
        print(f"   📆 Day {season_info['day']}/{season_manager.SEASON_LENGTH_DAYS}")


def get_config_summary() -> dict:
    """
    Get summary of current config settings
    
    Returns:
        Dict with config summary
    """
    return {
        'auto_season': config.AUTO_SEASON,
        'start_date': config.START_DATE.isoformat() if config.START_DATE else None,
        'end_date': config.END_DATE.isoformat() if config.END_DATE else None,
        'min_volume': config.MIN_VOLUME,
        'min_market_volume': config.MIN_MARKET_VOLUME,
        'closed_only': config.CLOSED_ONLY,
        'resolution_status': config.RESOLUTION_STATUS,
        'max_events': config.MAX_EVENTS
    }


def set_manual_dates(start_date: datetime = None, end_date: datetime = None):
    """
    Manually set date range (disables AUTO_SEASON)
    
    Args:
        start_date: Start date for filtering
        end_date: End date for filtering
    """
    config.AUTO_SEASON = False
    config.START_DATE = start_date
    config.END_DATE = end_date
    
    print(f"📅 Manual date filtering:")
    if start_date:
        print(f"   Start: {start_date.date()}")
    if end_date:
        print(f"   End: {end_date.date()}")


def reset_to_defaults():
    """Reset config to default values (no date filtering)"""
    config.AUTO_SEASON = False
    config.START_DATE = None
    config.END_DATE = None
    
    print("🔄 Config reset to defaults (no date filtering)")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Config Loader with Season Support')
    parser.add_argument('--apply-season', action='store_true', help='Apply current season dates')
    parser.add_argument('--date', type=str, help='Date to get season for (YYYY-MM-DD)')
    parser.add_argument('--summary', action='store_true', help='Show config summary')
    parser.add_argument('--enable-auto', action='store_true', help='Enable AUTO_SEASON mode')
    parser.add_argument('--disable-auto', action='store_true', help='Disable AUTO_SEASON mode')
    
    args = parser.parse_args()
    
    if args.enable_auto:
        config.AUTO_SEASON = True
        print("✅ AUTO_SEASON enabled")
    
    if args.disable_auto:
        config.AUTO_SEASON = False
        print("✅ AUTO_SEASON disabled")
    
    if args.apply_season:
        target_date = None
        if args.date:
            target_date = datetime.strptime(args.date, '%Y-%m-%d').date()
        
        apply_season_dates(target_date=target_date)
    
    if args.summary:
        summary = get_config_summary()
        print("\n📊 Current Config:")
        for key, value in summary.items():
            print(f"   • {key}: {value}")
