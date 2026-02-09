"""
Configuration for parallel events fetcher
Adjust these parameters to filter events

AUTO-SEASON SUPPORT:
====================
Genesis Period: 2024-07-06 to 2026-01-05 (исторические данные)
Season 1+:      2026-01-06 onwards (по 10 дней каждый сезон)

To enable automatic date filtering based on current season:
1. Set AUTO_SEASON = True
2. Script will automatically load dates from season_manager
3. Or use daily_scheduler.py which handles this automatically
"""

from datetime import datetime, timedelta

# ============================================================================
# AUTO-SEASON MODE
# ============================================================================

# Enable automatic season-based date filtering
AUTO_SEASON = False  # Set to True to use season dates automatically

# ============================================================================
# FILTERING CRITERIA
# ============================================================================

# Minimum event volume (in USD)
# NOTE: If AUTO_SEASON = True, this will be overridden:
#   - Genesis: 100M (only major historical events)
#   - Seasons: 5M (more detailed current data)
MIN_VOLUME = 100_000_000  # Default: 100M USD

# Minimum market volume (in USD) - markets below this will be filtered out
MIN_MARKET_VOLUME = 100  # Minimum volume for individual markets

# Fetch only closed events
CLOSED_ONLY = True

# Resolution status filter
RESOLUTION_STATUS = 'Resolved'  # Only resolved events

# ============================================================================
# DATE RANGE FILTERING
# ============================================================================

# Date range filtering (None = no filter)
# If AUTO_SEASON = True, these will be overridden by season dates

# Genesis period (historical data)
# START_DATE = datetime(2024, 7, 6)   # Genesis start
# END_DATE = datetime(2026, 1, 5)     # Genesis end

# Or use current date for testing
END_DATE = datetime.now()  # End date (now)
START_DATE = datetime(2024, 7, 6)  # Start from Genesis

# Or set to None to fetch all events regardless of date
# START_DATE = None
# END_DATE = None

# ============================================================================
# FETCH PARAMETERS
# ============================================================================

# Batch size for pagination
BATCH_SIZE = 100

# Maximum number of events to fetch (None = unlimited)
MAX_EVENTS = None  # Set to a number like 1000 for testing

# API Settings
REQUEST_DELAY = 0.2  # Delay between requests in seconds

# ============================================================================
# OUTPUT
# ============================================================================

# Output directory
OUTPUT_DIR = "output"

# Output filename (None = auto-generate with timestamp)
OUTPUT_FILENAME = None  # e.g., "events_20240706.json"

