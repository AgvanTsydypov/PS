"""
Configuration file for historical events fetcher
Modify these settings to customize your data collection
"""

from datetime import datetime, timedelta

# Filtering Criteria
# MIN_VOLUME = 500000  # Minimum volume in USD (e.g., 500000 = $500,000)
MIN_VOLUME = 5000000
CLOSED_ONLY = True   # Only fetch closed events
RESOLUTION_STATUS = 'resolved'  # Filter by umaResolutionStatus

# Pagination Settings
BATCH_SIZE = 100  # Number of events per API request (max: 100)
MAX_EVENTS = None  # Maximum total events to fetch (None = no limit)

# Output Settings
OUTPUT_FILENAME = None  # Custom filename (None = auto-generate with timestamp)
OUTPUT_DIR = 'json_output'  # Output directory for JSON files (relative path, no leading slash)

# API Settings
REQUEST_DELAY = 0.2  # Delay between requests in seconds (avoid rate limiting - increased for stability)

# Date Range - DEFAULT: Last 1 year from now
# Set to None to disable date filtering
END_DATE = datetime.now()  # End date (now)
# START_DATE = END_DATE - timedelta(days=365)  # Start date (1 year ago)
START_DATE = END_DATE - timedelta(days=1)

# Alternative: Custom date range
# START_DATE = datetime(2023, 1, 1)  # January 1, 2023
# END_DATE = datetime(2024, 1, 1)    # January 1, 2024

# Or set to None to fetch all events regardless of date
# START_DATE = None
# END_DATE = None

