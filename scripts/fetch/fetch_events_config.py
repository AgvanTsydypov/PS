"""
Configuration for parallel events fetcher

SIMPLIFIED LOGIC:
=================
- Genesis: 2024-07-06 to 2026-01-05 with 100M filter
- Daily: After Genesis with 5M filter

The daily_scheduler_simple.py will automatically configure:
- Dates based on load type (yesterday for events)
- MIN_VOLUME based on load type (100M for Genesis, 5M for daily)

You can also configure manually for testing.
"""

from datetime import datetime, timedelta

# ============================================================================
# AUTO-CONFIG MODE
# ============================================================================

# Enable automatic configuration by scheduler (recommended)
AUTO_CONFIG = False  # Set to True if using daily_scheduler_simple.py

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

# Date range filtering
# If AUTO_CONFIG = True, these will be set by scheduler:
#   - Events: Yesterday's date
#   - Genesis: Full Genesis period (2024-07-06 to 2026-01-05)

# Manual configuration (for testing):
START_DATE = datetime(2024, 7, 6)  # Genesis start or specific date
END_DATE = datetime.now()  # Current or specific date

# Or set to None for no date filtering
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

