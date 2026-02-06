"""
Fetch Redemptions for All Markets from Polymarket Events
Automatically scans latest events file and fetches redemptions for each market

БЫСТРЫЙ ЗАПУСК:
===============
1. Только fetch (без загрузки):
   python fetch_redemptions.py

2. Fetch + загрузка в Supabase:
   python fetch_redemptions.py --upload

3. Fetch + загрузка в локальную PostgreSQL (БЕЗ ЛИМИТОВ):
   python fetch_redemptions.py --upload --local

4. Использовать конкретный файл событий:
   python fetch_redemptions.py --file data/json_output/polymarket_events_optimized_20260113_020726.json

5. НОВОЕ! Повторная обработка упорных маркетов (retry failed markets):
   python fetch_redemptions.py --retry-failed --upload
   (использует последний файл failed_markets_*.json из output/)

6. Повторная обработка конкретного файла failed markets:
   python fetch_redemptions.py --retry-failed --file output/failed_markets_20260205_204749.json --upload

7. Справка:
   python fetch_redemptions.py --help

ТРЕБОВАНИЯ:
===========
- Python 3.8+
- pip install -r requirements.txt
- Файл .env с настройками (см. env.template)
- Для --local: PostgreSQL установлен и настроен

ЛОГИ:
=====
- Сохраняются в: logs/redemptions_fetch_YYYYMMDD_HHMMSS.log
- Просмотр: python view_logs.py

Features:
- Умный Rate Limiter для Goldsky API:
  * Token Bucket алгоритм - точно 9 RPS
  * Lock не держится во время sleep - полная параллельность
  * Автоматическое распределение временных слотов
- 🔒 EXCLUSIVE MODE для тяжелых маркетов (>$300M):
  * Останавливает ВСЕ другие маркеты
  * ПОСЛЕДОВАТЕЛЬНАЯ обработка: ждет ответа от предыдущего запроса
  * 🎯 УМНАЯ АДАПТАЦИЯ ЗАДЕРЖКИ (приоритет над уменьшением batch size):
    - 7 уровней задержки: 2s → 4s → 6s → 8s → 10s → 15s → 20s
    - При timeout: СНАЧАЛА увеличивает задержку (до 10 попыток)
    - Только после 10 неудач: уменьшает batch size
    - При 5+ успехах подряд: уменьшает задержку обратно (восстановление)
  * Результат: стабильная обработка крупных маркетов БЕЗ потери производительности
- Intelligent Immediate Retry System:
  * 5 попыток с экспоненциальной задержкой (3s → 6s → 12s → 24s → 48s)
  * Retry СРАЗУ при ошибке, не откладывая на потом
  * Отдельная обработка GraphQL timeouts и network errors
  * Goldsky API "отдыхает" между попытками
- 🎯 SMART ADAPTIVE PAGINATION (Умная адаптивная пагинация):
  * Начинает с 1000 redemptions за запрос (максимум)
  * Sliding window tracking: отслеживает последние 10 запросов
  * Pattern detection: если 70%+ timeout'ов → автоуменьшение batch size
  * Critical mode: если 90%+ timeout'ов → увеличивает retry delay (до 2x)
  * Прогрессивное уменьшение: 1000 → 500 → 250 → 125 → 100
  * Динамическое восстановление: постепенно снижает delay при успехе
  * Результат: быстрые маркеты на максимуме | проблемные находят оптимум
- Параллельная обработка маркетов (20-30 concurrent)
- Отслеживание неудачных/неполных маркетов
- Повторная обработка упорных случаев в конце (если immediate retry не помог)
- Сохранение финально неудавшихся данных в JSON для ручного анализа
- Real-time логирование всех операций с временными метками
- Поддержка Supabase и локальной PostgreSQL
- Параллельная загрузка в БД (до 100 concurrent uploads)
"""

import requests
import json
import time
import os
import glob
import asyncio
import aiohttp
import sys
from datetime import datetime
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor
from collections import deque

# ==========================================
# LOGGING SETUP
# ==========================================
class DualLogger:
    """Logger that writes to both console and file in real-time"""
    def __init__(self, log_file):
        self.terminal = sys.stdout
        self.log_file = open(log_file, 'a', encoding='utf-8')
        
    def write(self, message):
        self.terminal.write(message)
        self.terminal.flush()  # Force immediate output to terminal
        self.log_file.write(message)
        self.log_file.flush()  # Force immediate write to file
        
    def flush(self):
        self.terminal.flush()
        self.log_file.flush()
    
    def close(self):
        self.log_file.close()

# Global logger instance
_logger = None

def setup_logging():
    """Setup dual logging to console and file"""
    global _logger
    
    # Create logs directory if it doesn't exist
    log_dir = 'logs'
    os.makedirs(log_dir, exist_ok=True)
    
    # Create log file with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(log_dir, f'redemptions_fetch_{timestamp}.log')
    
    # Setup dual logger
    _logger = DualLogger(log_file)
    sys.stdout = _logger
    
    print(f"📝 Logging to: {log_file}")
    print(f"   All output will be saved to this file in real-time")
    print()
    
    return log_file

def cleanup_logging():
    """Cleanup logging and restore stdout"""
    global _logger
    if _logger:
        sys.stdout = _logger.terminal
        _logger.close()
        _logger = None

# ==========================================
# RATE LIMITER FOR GOLDSKY API
# ==========================================
class GoldskyRateLimiter:
    """
    Smart rate limiter for Goldsky API using Token Bucket algorithm
    Allows up to MAX_RPS requests per second
    CRITICAL: Does NOT hold lock during sleep to allow parallel processing
    """
    def __init__(self, max_rps: float = 9.0):
        self.max_rps = max_rps
        # Add 1% safety margin to ensure we never exceed rate limit
        self.min_interval = (1.0 / max_rps) * 1.01
        self.last_request_time = 0.0  # Start time, first request goes immediately
        self.lock = asyncio.Lock()
        self.total_requests = 0
        self.total_wait_time = 0.0
        
    async def acquire(self):
        """
        Wait if necessary to respect rate limit, then allow request
        Uses simple interval-based approach to ensure min_interval between requests
        Lock is released BEFORE sleeping to enable parallel processing
        """
        # Acquire lock to get our time slot
        async with self.lock:
            now = time.time()
            
            # Calculate next available slot
            # If last_request_time is in the future, we need to wait
            # If it's in the past, we can go now
            next_available = max(self.last_request_time, now)
            wait_time = next_available - now
            
            # Reserve the next slot for future requests
            # Each request reserves a slot min_interval after the previous
            self.last_request_time = next_available + self.min_interval
            self.total_requests += 1
        
        # CRITICAL: Lock is released here!
        # Now sleep OUTSIDE the lock so other coroutines can reserve their slots
        if wait_time > 0.001:  # Only sleep if wait time is significant
            self.total_wait_time += wait_time
            await asyncio.sleep(wait_time)
    
    def get_stats(self):
        """Get rate limiter statistics"""
        return {
            'total_requests': self.total_requests,
            'total_wait_time': self.total_wait_time
        }

# ==========================================
# CONFIGURATION
# ==========================================
GRAPH_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/activity-subgraph/0.0.4/gn"

# Filter settings
FILTER_CLOSED_ONLY = True  # Only fetch redemptions for closed markets
MIN_VOLUME = 0  # Minimum market volume to process ($100k+, значительно уменьшит количество)
MAX_MARKETS = None  # Limit number of markets to process (None = all)

# ==========================================
# GOLDSKY API RATE LIMITING
# ==========================================
# Goldsky allows 10 RPS max, we use 9.0 for safety margin
GOLDSKY_MAX_RPS = 9.0  # Safe rate: 9 requests per second

# ==========================================
# PARALLEL PROCESSING SETTINGS
# ==========================================
# Balance between parallelism and rate limiting
# Rate limiter serializes API calls (9 RPS max)
# But we want some parallelism for markets with multiple pagination requests

# Settings for Supabase (cloud)
# Moderate parallelism - rate limiter handles API throttling
MAX_CONCURRENT_MARKETS_CLOUD = 20  # Reasonable parallelism
BATCH_SIZE_CLOUD = 100  # Large batches for efficiency
BATCH_DELAY_CLOUD = 0  # NO delay between batches (rate limiter handles it)

# Settings for local PostgreSQL
# Can be more aggressive with local DB (faster uploads)
MAX_CONCURRENT_MARKETS_LOCAL = 30  # Higher parallelism
BATCH_SIZE_LOCAL = 150  # Larger batches
BATCH_DELAY_LOCAL = 0  # NO delay between batches

# Active settings (will be set based on database type)
MAX_CONCURRENT_MARKETS = MAX_CONCURRENT_MARKETS_CLOUD  # Default
BATCH_SIZE = BATCH_SIZE_CLOUD
BATCH_DELAY = BATCH_DELAY_CLOUD

REQUEST_TIMEOUT = 180  # Timeout for each request in seconds (increased for large markets like Trump)

# Immediate retry settings (при сбое на месте)
IMMEDIATE_RETRIES = 5  # Количество немедленных retry при ошибке
INITIAL_RETRY_DELAY = 3  # Начальная задержка (секунды)
MAX_RETRY_DELAY = 60  # Максимальная задержка (секунды)
# Exponential backoff: 3s, 6s, 12s, 24s, 48s, 60s (cap)

# Adaptive pagination settings (адаптивная пагинация для проблемных маркетов)
INITIAL_BATCH_SIZE = 1000  # Начальный размер батча
MIN_BATCH_SIZE = 100  # Минимальный размер батча (агрессивное уменьшение для проблемных маркетов)
BATCH_SIZE_REDUCTION_FACTOR = 2  # Делитель при уменьшении (1000 → 500 → 250 → 125 → 100)

# Smart pattern detection (умное отслеживание паттернов)
TIMEOUT_WINDOW_SIZE = 10  # Отслеживаем последние 10 запросов
TIMEOUT_THRESHOLD_FOR_REDUCTION = 0.7  # 70%+ timeout'ов → уменьшить batch (менее агрессивно)
TIMEOUT_THRESHOLD_CRITICAL = 0.9  # 90%+ timeout'ов → увеличить retry delay (очень редко)

# Legacy retry settings (для совместимости с другими местами в коде)
MAX_RETRIES = IMMEDIATE_RETRIES
RETRY_DELAY = INITIAL_RETRY_DELAY

# Database upload settings
# БД может быть ОЧЕНЬ быстрой - не ограничиваем!
MAX_CONCURRENT_DB_UPLOADS = 100  # Без ограничений - БД справится!

# ==========================================
# FILE UTILITIES
# ==========================================
def find_latest_events_file() -> Optional[str]:
    """Find the latest polymarket_events_optimized_*.json file"""
    json_dir = 'data/json_output'
    pattern = os.path.join(json_dir, 'polymarket_events_optimized_*.json')
    files = glob.glob(pattern)
    
    if not files:
        print(f"❌ No events files found in {json_dir}/")
        return None
    
    # Sort by modification time, get latest
    latest_file = max(files, key=os.path.getmtime)
    return latest_file


def find_latest_failed_markets_file() -> Optional[str]:
    """Find the latest failed_markets_*.json file"""
    output_dir = 'output'
    pattern = os.path.join(output_dir, 'failed_markets_*.json')
    files = glob.glob(pattern)
    
    if not files:
        print(f"❌ No failed markets files found in {output_dir}/")
        return None
    
    # Sort by modification time, get latest
    latest_file = max(files, key=os.path.getmtime)
    return latest_file


def load_failed_markets_file(filepath: str) -> List[Dict]:
    """Load failed markets data from JSON file and convert to market format"""
    print(f"📂 Loading failed markets from: {filepath}")
    with open(filepath, 'r', encoding='utf-8') as f:
        failed_markets = json.load(f)
    
    print(f"✅ Loaded {len(failed_markets)} failed markets")
    
    # Convert to market format
    markets = []
    for fm in failed_markets:
        market_info = {
            'market_id': None,  # Not available in failed markets file
            'condition_id': fm['condition_id'],
            'question': fm['question'],
            'event_id': fm['event_id'],
            'event_title': 'Unknown Event',  # Not available in failed markets file
            'closed': True,  # Assume closed since they were being processed
            'volume': float(fm.get('volume', 0) or 0),
        }
        markets.append(market_info)
    
    return markets


def get_markets_from_db(use_local_db: bool = False, limit: Optional[int] = None) -> List[Dict]:
    """
    Load markets data from database
    
    Args:
        use_local_db: Use local PostgreSQL instead of Supabase
        limit: Optional limit on number of markets
    
    Returns:
        List of market dicts with condition_id, question, event info, volume, etc.
    """
    print("=" * 70)
    print("📊 QUERYING DATABASE FOR MARKETS")
    print("=" * 70)
    
    # Initialize database connection
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    
    from db.supabase_uploader import SupabaseUploader
    uploader = SupabaseUploader(use_local_db=use_local_db)
    
    if use_local_db:
        # Use local PostgreSQL
        import psycopg2
        import psycopg2.extras
        
        print(f"🟢 Connecting to local PostgreSQL...")
        print(f"   Database: {uploader.connection_params['database']}")
        print(f"   Host: {uploader.connection_params['host']}:{uploader.connection_params['port']}")
        print()
        
        conn = psycopg2.connect(**uploader.connection_params)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        try:
            # Load SQL query from file
            sql_file = "sql/queries/get_markets_for_redemptions.sql"
            if not os.path.exists(sql_file):
                raise FileNotFoundError(f"SQL file not found: {sql_file}")
            
            print(f"📄 Using SQL query from: {sql_file}")
            
            with open(sql_file, 'r', encoding='utf-8') as f:
                sql_query = f.read()
            
            # Apply additional filters dynamically
            filters = []
            params = []
            
            if FILTER_CLOSED_ONLY:
                filters.append("m.closed = TRUE")
                print(f"🔍 Filter: Closed markets only")
            
            if MIN_VOLUME > 0:
                filters.append("""
                    COALESCE(
                        CASE 
                            WHEN m.volume IS NOT NULL AND m.volume <> '' 
                            THEN m.volume::numeric 
                            ELSE NULL 
                        END,
                        m.volume_num,
                        0
                    ) >= %s
                """)
                params.append(MIN_VOLUME)
                print(f"🔍 Filter: Min volume ${MIN_VOLUME:,.0f}")
            
            if filters:
                sql_query += " AND " + " AND ".join(filters)
            
            # Order by volume descending (cast to numeric for proper sorting)
            sql_query += """
                ORDER BY COALESCE(
                    CASE 
                        WHEN m.volume IS NOT NULL AND m.volume <> '' 
                        THEN m.volume::numeric 
                        ELSE NULL 
                    END,
                    m.volume_num,
                    0
                ) DESC
            """
            
            if limit:
                sql_query += f" LIMIT {limit}"
                print(f"🔍 Limit: {limit:,} markets")
            
            print()
            print(f"🔍 Executing query...")
            cursor.execute(sql_query, params)
            
            # Fetch all results
            rows = cursor.fetchall()
            
            print(f"✅ Found {len(rows):,} markets in database")
            print()
            
            # Convert to list of dicts
            markets = []
            for row in rows:
                market_info = {
                    'market_id': row['market_id'],
                    'condition_id': row['condition_id'],
                    'question': row['question'] or 'Unknown Question',
                    'event_id': row['event_id'],
                    'event_title': row['event_title'] or 'Unknown Event',
                    'closed': row['closed'],
                    'volume': float(row['volume'] or 0),
                }
                markets.append(market_info)
            
            return markets
            
        finally:
            cursor.close()
            conn.close()
    
    else:
        # Supabase mode
        print(f"🔵 Connecting to Supabase...")
        print()
        
        try:
            # Build query
            query = uploader.client.table('markets').select(
                'id, condition_id, question, event_id, events(title), closed, volume, volume_num'
            )
            
            # Apply filters
            if FILTER_CLOSED_ONLY:
                query = query.eq('closed', True)
                print(f"🔍 Filter: Closed markets only")
            
            # Filter out NULL condition_ids
            query = query.not_.is_('condition_id', 'null')
            
            # Order by volume
            query = query.order('volume', desc=True)
            
            if limit:
                query = query.limit(limit)
                print(f"🔍 Limit: {limit:,} markets")
            
            print()
            print(f"🔍 Executing query...")
            response = query.execute()
            
            rows = response.data
            print(f"✅ Found {len(rows):,} markets in database")
            print()
            
            # Convert to market format
            markets = []
            for row in rows:
                # Get volume (handle different field names)
                volume = float(row.get('volume') or row.get('volume_num') or 0)
                
                # Skip if below minimum volume
                if MIN_VOLUME > 0 and volume < MIN_VOLUME:
                    continue
                
                # Get event title from joined data
                event_title = 'Unknown Event'
                if row.get('events') and isinstance(row['events'], dict):
                    event_title = row['events'].get('title', 'Unknown Event')
                
                market_info = {
                    'market_id': row['id'],
                    'condition_id': row['condition_id'],
                    'question': row.get('question') or 'Unknown Question',
                    'event_id': row['event_id'],
                    'event_title': event_title,
                    'closed': row.get('closed', False),
                    'volume': volume,
                }
                markets.append(market_info)
            
            return markets
            
        except Exception as e:
            print(f"❌ Error querying Supabase: {type(e).__name__}")
            print(f"   {str(e)}")
            raise


def load_events_file(filepath: str) -> Dict:
    """Load events data from JSON file (LEGACY - prefer get_markets_from_db)"""
    print(f"📂 Loading events from: {filepath}")
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Handle different structures
    if 'events' in data:
        events = data['events']
    elif isinstance(data, list):
        events = data
    else:
        events = [data]
    
    print(f"✅ Loaded {len(events)} events")
    return events


def extract_markets(events: List[Dict]) -> List[Dict]:
    """Extract all markets from events with metadata"""
    markets = []
    
    for event in events:
        event_id = event.get('id')
        event_title = event.get('title', 'Unknown Event')
        closed = event.get('closed', False)
        volume = float(event.get('volume', 0) or 0)
        
        # Get markets from event
        event_markets = event.get('markets', [])
        
        for market in event_markets:
            market_info = {
                'market_id': market.get('id'),
                'condition_id': market.get('conditionId') or market.get('condition_id'),
                'question': market.get('question', 'Unknown Question'),
                'event_id': event_id,
                'event_title': event_title,
                'closed': market.get('closed', closed),
                'volume': float(market.get('volume', 0) or 0),
            }
            
            # Apply filters
            if FILTER_CLOSED_ONLY and not market_info['closed']:
                continue
            
            if market_info['volume'] < MIN_VOLUME:
                continue
            
            if market_info['condition_id']:  # Only add if has condition_id
                markets.append(market_info)
    
    return markets


# ==========================================
# REDEMPTIONS FETCHING (ASYNC)
# ==========================================
async def fetch_redemptions_for_market_async(
    session: aiohttp.ClientSession, 
    condition_id: str, 
    market_info: Dict,
    semaphore: asyncio.Semaphore,
    rate_limiter: GoldskyRateLimiter,
    use_local_db: bool = False
) -> tuple[List[Dict], Dict]:
    """
    Fetch all redemptions for a specific market (async version)
    
    Returns:
        tuple: (redemptions_list, status_dict)
        status_dict = {
            'success': bool,
            'complete': bool,  # False if stopped due to error/timeout
            'error': str or None,
            'requests_made': int
        }
    """
    async with semaphore:  # Limit concurrent market processing (memory/resource control)
        # NO initial delay - rate limiter will handle API throttling automatically!
        
        all_redemptions = []
        last_id = "0x00"
        request_count = 0
        current_batch_size = INITIAL_BATCH_SIZE  # Start with 1000, adaptively reduce on errors
        batch_size_reduced = False  # Track if we've reduced batch size
        
        # Check if this is a very large market (>$300M) - needs special handling
        # These markets get EXCLUSIVE ACCESS and SEQUENTIAL request processing
        market_volume = float(market_info.get('volume', 0) or 0)
        is_very_large_market = market_volume > 300_000_000  # $300M+ volume (matches exclusive mode threshold)
        
        # Pattern tracking for smart adaptation
        timeout_history = deque(maxlen=TIMEOUT_WINDOW_SIZE)  # Track last N requests: True=timeout, False=success
        retry_delay_multiplier = 1.0  # Start normal, can increase in critical situations
        last_request_duration = 0.0  # Track duration of last successful request for adaptive delay
        
        # 🎯 NEW: Smart delay adaptation for very large markets
        # Strategy: First increase delay (10 attempts), THEN reduce batch size
        if is_very_large_market:
            # Delay levels: 2s → 4s → 6s → 8s → 10s → 15s → 20s
            delay_levels = [2.0, 4.0, 6.0, 8.0, 10.0, 15.0, 20.0]
            current_delay_level = 0  # Start at level 0 (2s)
            delay_increase_attempts = 0  # Track how many times we increased delay
            consecutive_successes = 0  # Track successful requests in a row
            print(f"      🎯 Smart delay adaptation enabled (start: {delay_levels[current_delay_level]:.0f}s)", flush=True)
        
        status = {
            'success': True,
            'complete': True,
            'error': None,
            'requests_made': 0,
            'adaptive_pagination_used': False
        }
        
        def analyze_timeout_pattern():
            """Analyze recent timeout pattern and return adaptation decisions"""
            if len(timeout_history) < 5:  # Need at least 5 samples
                return {'should_reduce': False, 'is_critical': False, 'timeout_rate': 0.0}
            
            timeout_count = sum(timeout_history)
            timeout_rate = timeout_count / len(timeout_history)
            
            return {
                'should_reduce': timeout_rate >= TIMEOUT_THRESHOLD_FOR_REDUCTION and current_batch_size > MIN_BATCH_SIZE,
                'is_critical': timeout_rate >= TIMEOUT_THRESHOLD_CRITICAL,
                'timeout_rate': timeout_rate,
                'recent_timeouts': timeout_count
            }
        
        while True:
            request_count += 1
            
            # 🎯 IMPORTANT: Apply delay BEFORE making request (not after)
            # This ensures EVERY request (except first) has proper spacing
            if is_very_large_market and request_count == 1:
                # First request for very large market - no delay but log it
                print(f"      📡 Sending initial request (batch size: {current_batch_size})...", flush=True)
            elif is_very_large_market and request_count > 1:  # Apply delay before 2nd, 3rd, etc requests
                # 🎯 SMART DELAY ADAPTATION: Use current delay level
                adaptive_delay = delay_levels[current_delay_level]
                
                # Show delay info based on level
                if current_delay_level > 0:
                    print(f"      ⏳ Waiting {adaptive_delay:.0f}s (delay level {current_delay_level+1}/{len(delay_levels)})...", flush=True)
                
                await asyncio.sleep(adaptive_delay)
            elif batch_size_reduced and current_batch_size <= 250 and request_count > 1:
                # Smart adaptive delay for problematic markets (batch size reduced)
                if last_request_duration > 30:
                    adaptive_delay = 2.5
                elif last_request_duration > 15:
                    adaptive_delay = 2.0
                elif last_request_duration > 5:
                    adaptive_delay = 1.5
                else:
                    adaptive_delay = 1.0
                
                await asyncio.sleep(adaptive_delay)
            
            # Build query with current batch size
            query = f"""
            query ($condId: Bytes!, $lastId: ID!) {{
              redemptions(
                where: {{ condition: $condId, id_gt: $lastId }}
                first: {current_batch_size}
                orderBy: id
                orderDirection: asc
              ) {{
                id
                redeemer
                payout
                timestamp
              }}
            }}
            """
            
            variables = {
                "condId": condition_id,
                "lastId": last_id
            }
            
            # Retry logic for each request with exponential backoff
            retry_count = 0
            request_success = False
            graphql_error_retries = 0  # Track GraphQL-specific retries separately
            total_attempts = 0  # Total attempts including all error types
            
            while total_attempts <= IMMEDIATE_RETRIES and not request_success:
                total_attempts += 1
                try:
                    # Use rate limiter BEFORE each API request
                    await rate_limiter.acquire()
                    
                    # Track request duration for adaptive delay
                    request_start_time = time.time()
                    
                    async with session.post(
                        GRAPH_URL,
                        json={'query': query, 'variables': variables},
                        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                    ) as response:
                        data = await response.json()
                        
                        # Calculate request duration
                        request_duration = time.time() - request_start_time
                        
                        if data.get('errors'):
                            # Log GraphQL errors in real-time
                            error_details = data.get('errors')
                            error_msg = str(error_details)
                            
                            # Check if this is a retryable error (timeout, etc.)
                            is_timeout = 'timeout' in error_msg.lower() or 'canceling statement' in error_msg.lower()
                            
                            if is_timeout and total_attempts <= IMMEDIATE_RETRIES:
                                graphql_error_retries += 1
                                timeout_history.append(True)  # Record timeout
                                
                                # 🎯 NEW: Different strategy for very large markets (>$300M)
                                if is_very_large_market:
                                    # STRATEGY: First increase delay (up to 10 attempts), THEN reduce batch size
                                    
                                    # Reset consecutive successes (we had a failure)
                                    consecutive_successes = 0
                                    
                                    # Check if we can increase delay level
                                    can_increase_delay = (current_delay_level < len(delay_levels) - 1 and 
                                                         delay_increase_attempts < 10)
                                    
                                    if can_increase_delay:
                                        # Increase delay level instead of reducing batch size
                                        old_level = current_delay_level
                                        current_delay_level += 1
                                        delay_increase_attempts += 1
                                        
                                        print(f"\n      ⚠️  GraphQL Timeout for {condition_id[:20]}... (attempt {total_attempts}/{IMMEDIATE_RETRIES + 1})")
                                        print(f"         Error: {error_msg[:150]}")
                                        print(f"         📈 SMART DELAY: Increasing delay level {old_level+1} → {current_delay_level+1} ({delay_levels[old_level]:.0f}s → {delay_levels[current_delay_level]:.0f}s)")
                                        print(f"         📊 Delay increase attempts: {delay_increase_attempts}/10 (batch size unchanged: {current_batch_size})")
                                        print(f"         🔄 Retrying with longer delay...", flush=True)
                                        
                                        # Short pause before retry
                                        await asyncio.sleep(2)
                                        continue  # Retry with increased delay
                                    else:
                                        # Exhausted delay increases (10 attempts) - now reduce batch size
                                        if current_batch_size > MIN_BATCH_SIZE:
                                            new_batch_size = max(current_batch_size // BATCH_SIZE_REDUCTION_FACTOR, MIN_BATCH_SIZE)
                                            
                                            print(f"\n      ⚠️  GraphQL Timeout for {condition_id[:20]}... (attempt {total_attempts}/{IMMEDIATE_RETRIES + 1})")
                                            print(f"         Error: {error_msg[:150]}")
                                            print(f"         📉 BATCH REDUCTION: Max delay reached, reducing batch size {current_batch_size} → {new_batch_size}")
                                            print(f"         📊 Delay increases exhausted (10/10), max delay level: {current_delay_level+1}/{len(delay_levels)}")
                                            print(f"         🔄 Retrying with smaller batch...", flush=True)
                                            
                                            current_batch_size = new_batch_size
                                            batch_size_reduced = True
                                            status['adaptive_pagination_used'] = True
                                            
                                            # Reset delay adaptation counters for new batch size
                                            delay_increase_attempts = 0
                                            current_delay_level = 0  # Start from minimum delay with new batch size
                                            
                                            await asyncio.sleep(2)
                                            continue  # Retry with new batch size
                                        else:
                                            # Can't reduce batch size further - normal retry
                                            base_delay = INITIAL_RETRY_DELAY * (2 ** (graphql_error_retries - 1))
                                            retry_delay = min(base_delay, MAX_RETRY_DELAY)
                                            
                                            print(f"\n      ⚠️  GraphQL Timeout for {condition_id[:20]}... (attempt {total_attempts}/{IMMEDIATE_RETRIES + 1})")
                                            print(f"         Error: {error_msg[:150]}")
                                            print(f"         Current batch size: {current_batch_size} (minimum)")
                                            print(f"         🔄 Retrying in {retry_delay:.0f}s...", flush=True)
                                            
                                            await asyncio.sleep(retry_delay)
                                            continue
                                else:
                                    # OLD STRATEGY: For smaller markets, use original logic
                                    # SMART PATTERN ANALYSIS
                                    pattern = analyze_timeout_pattern()
                                    
                                    # ADAPTIVE PAGINATION with pattern detection
                                    should_reduce_batch = False
                                    
                                    # Strategy 1: Immediate reduction after 2 consecutive failures
                                    if current_batch_size > MIN_BATCH_SIZE and graphql_error_retries >= 2:
                                        should_reduce_batch = True
                                        reduction_reason = f"2 consecutive timeouts"
                                    
                                    # Strategy 2: Pattern-based reduction (only if 70%+ timeout rate AND min 5 samples)
                                    elif pattern['should_reduce'] and len(timeout_history) >= 5 and current_batch_size > MIN_BATCH_SIZE:
                                        should_reduce_batch = True
                                        reduction_reason = f"high timeout rate ({pattern['timeout_rate']*100:.0f}%)"
                                    
                                    # Strategy 3: Critical situation - increase retry delay
                                    if pattern['is_critical'] and len(timeout_history) >= 7:
                                        new_multiplier = min(retry_delay_multiplier * 1.2, 2.0)
                                        if new_multiplier > retry_delay_multiplier:
                                            retry_delay_multiplier = new_multiplier
                                            print(f"\n      🚨 CRITICAL: {pattern['recent_timeouts']}/{len(timeout_history)} recent timeouts!")
                                            print(f"         Increasing retry delay multiplier: {retry_delay_multiplier:.1f}x")
                                    
                                    if should_reduce_batch:
                                        # Reduce batch size
                                        new_batch_size = max(current_batch_size // BATCH_SIZE_REDUCTION_FACTOR, MIN_BATCH_SIZE)
                                        
                                        print(f"\n      ⚠️  GraphQL Timeout for {condition_id[:20]}... (attempt {total_attempts}/{IMMEDIATE_RETRIES + 1})")
                                        print(f"         Error: {error_msg[:150]}")
                                        print(f"         📉 ADAPTIVE: Reducing batch size {current_batch_size} → {new_batch_size} ({reduction_reason})")
                                        if pattern['timeout_rate'] > 0:
                                            print(f"         📊 Pattern: {pattern['recent_timeouts']}/{len(timeout_history)} recent requests had timeouts")
                                        print(f"         🔄 Retrying with smaller batch...", flush=True)
                                        
                                        current_batch_size = new_batch_size
                                        batch_size_reduced = True
                                        status['adaptive_pagination_used'] = True
                                        
                                        # Reset retry counter for new batch size attempt
                                        graphql_error_retries = 0
                                        if not pattern['is_critical']:
                                            retry_delay_multiplier = 1.0
                                        
                                        await asyncio.sleep(2)
                                        continue
                                    else:
                                        # Normal exponential backoff retry
                                        base_delay = INITIAL_RETRY_DELAY * (2 ** (graphql_error_retries - 1))
                                        retry_delay = min(base_delay * retry_delay_multiplier, MAX_RETRY_DELAY)
                                        
                                        print(f"\n      ⚠️  GraphQL Timeout for {condition_id[:20]}... (attempt {total_attempts}/{IMMEDIATE_RETRIES + 1})")
                                        print(f"         Error: {error_msg[:150]}")
                                        if batch_size_reduced:
                                            print(f"         Current batch size: {current_batch_size}")
                                        if pattern['timeout_rate'] > 0.3:
                                            print(f"         📊 Pattern: {pattern['recent_timeouts']}/{len(timeout_history)} recent timeouts ({pattern['timeout_rate']*100:.0f}%)")
                                        if retry_delay_multiplier > 1.0:
                                            print(f"         🔄 Retrying in {retry_delay:.0f}s (×{retry_delay_multiplier:.1f} critical backoff)...", flush=True)
                                        else:
                                            print(f"         🔄 Retrying in {retry_delay:.0f}s (exponential backoff)...", flush=True)
                                        
                                        await asyncio.sleep(retry_delay)
                                        continue
                            else:
                                # Non-retryable error or exhausted retries
                                if is_timeout:
                                    print(f"\n      ❌ GraphQL Timeout for {condition_id[:20]}... (failed after {graphql_error_retries} retries)")
                                    if batch_size_reduced:
                                        print(f"         Final batch size: {current_batch_size}")
                                else:
                                    print(f"\n      ❌ GraphQL Error for {condition_id[:20]}... (non-retryable)")
                                print(f"         Error: {error_msg[:200]}")
                                
                                # Mark as incomplete
                                status['complete'] = False
                                status['error'] = f"GraphQL Error after {graphql_error_retries} retries: {error_msg[:300]}"
                                status['requests_made'] = request_count
                                break
                        
                        batch = data.get('data', {}).get('redemptions', [])
                        
                        # Record success in timeout history
                        if graphql_error_retries > 0:
                            # This was a successful retry after timeout(s)
                            timeout_history.append(False)  # Record as success
                        else:
                            # First-time success, no timeout
                            timeout_history.append(False)
                        
                        # 🎯 NEW: Track consecutive successes for very large markets
                        if is_very_large_market:
                            consecutive_successes += 1
                            
                            # After 5+ consecutive successes, decrease delay level (if possible)
                            if consecutive_successes >= 5 and current_delay_level > 0:
                                old_level = current_delay_level
                                current_delay_level -= 1
                                consecutive_successes = 0  # Reset counter
                                
                                print(f"      ✅ 5+ successes! Decreasing delay level {old_level+1} → {current_delay_level+1} ({delay_levels[old_level]:.0f}s → {delay_levels[current_delay_level]:.0f}s)", flush=True)
                        
                        # Show success message if we recovered from GraphQL errors
                        if graphql_error_retries > 0:
                            if batch_size_reduced:
                                pattern = analyze_timeout_pattern()
                                if pattern['timeout_rate'] > 0.3:
                                    print(f"      ✅ Recovered! (batch: {current_batch_size}, pattern: {pattern['recent_timeouts']}/{len(timeout_history)} timeouts)", flush=True)
                                else:
                                    print(f"      ✅ Recovered after {graphql_error_retries} GraphQL retries with batch size {current_batch_size}!", flush=True)
                            else:
                                print(f"      ✅ Recovered after {graphql_error_retries} GraphQL retries!", flush=True)
                            graphql_error_retries = 0  # Reset counter
                            
                            # Gradually reduce retry delay multiplier on success
                            if retry_delay_multiplier > 1.0:
                                retry_delay_multiplier = max(retry_delay_multiplier * 0.8, 1.0)
                        
                        if not batch:
                            # Show final stats if batch size was reduced
                            if batch_size_reduced and len(all_redemptions) > 0:
                                print(f"      ℹ️  Completed with adaptive batch size: {current_batch_size} (started with {INITIAL_BATCH_SIZE})", flush=True)
                            break
                        
                        # Process batch
                        for redemption in batch:
                            raw_id = redemption.get('id', "")
                            tx_hash = raw_id.split('-')[0] if '-' in raw_id else raw_id
                            amount = float(redemption['payout']) / 1e6
                            
                            all_redemptions.append({
                                "transaction_hash": tx_hash,
                                "condition_id": condition_id,
                                "event_id": market_info['event_id'],
                                "market_id": market_info['market_id'],
                                "market_question": market_info['question'],
                                "event_title": market_info['event_title'],
                                "redeemer_address": redemption['redeemer'],
                                "payout_usdc": amount,
                                "timestamp_unix": redemption['timestamp'],
                                "timestamp_human": time.strftime('%Y-%m-%d %H:%M:%S', 
                                                                 time.localtime(int(redemption['timestamp'])))
                            })
                        
                        last_id = batch[-1]['id']
                        request_success = True
                        last_request_duration = request_duration  # Save for adaptive delay
                        
                        # Show success message if we recovered from network errors
                        if retry_count > 0:
                            print(f"      ✅ Recovered after {retry_count} network retries!", flush=True)
                        
                        # Show progress for large markets (every 3 batches)
                        if request_count > 1 and request_count % 3 == 0:
                            progress_msg = f"      ... fetched {len(all_redemptions):,} redemptions ({request_count} requests)"
                            if batch_size_reduced:
                                progress_msg += f" [batch: {current_batch_size}]"
                            print(progress_msg, flush=True)
                        
                        # Safety limit per market (increased for large markets)
                        if len(all_redemptions) > 500000:  # Увеличили до 500k
                            print(f"      ⚠️  Reached safety limit of 500k redemptions")
                            break
                        
                        # Delay is now applied at the START of next iteration (before request)
                        # See delay logic at the beginning of while loop
                            
                except asyncio.TimeoutError:
                    retry_count += 1
                    timeout_history.append(True)  # Record timeout
                    
                    # Apply dynamic retry delay multiplier
                    base_delay = INITIAL_RETRY_DELAY * (2 ** (retry_count - 1))
                    retry_delay = min(base_delay * retry_delay_multiplier, MAX_RETRY_DELAY)
                    
                    print(f"\n      ⚠️  Request timeout (attempt {total_attempts}/{IMMEDIATE_RETRIES + 1})")
                    print(f"         Condition: {condition_id[:30]}...")
                    print(f"         Request: {request_count}, Last ID: {last_id[:20]}...")
                    
                    if total_attempts > IMMEDIATE_RETRIES:
                        print(f"      ❌ Failed after {total_attempts - 1} total attempts - giving up")
                        status['success'] = False
                        status['complete'] = False
                        status['error'] = f"Timeout after {total_attempts - 1} attempts at request {request_count}"
                        status['requests_made'] = request_count
                        break
                    
                    if retry_delay_multiplier > 1.0:
                        print(f"         🔄 Retrying in {retry_delay:.0f}s (×{retry_delay_multiplier:.1f} critical backoff)...", flush=True)
                    else:
                        print(f"         🔄 Retrying in {retry_delay:.0f}s (exponential backoff)...", flush=True)
                    await asyncio.sleep(retry_delay)
                except Exception as e:
                    retry_count += 1
                    timeout_history.append(True)  # Record error as timeout-equivalent
                    
                    # Apply dynamic retry delay multiplier
                    base_delay = INITIAL_RETRY_DELAY * (2 ** (retry_count - 1))
                    retry_delay = min(base_delay * retry_delay_multiplier, MAX_RETRY_DELAY)
                    error_type = type(e).__name__
                    error_msg = str(e)
                    
                    print(f"\n      ⚠️  {error_type} (attempt {total_attempts}/{IMMEDIATE_RETRIES + 1})")
                    print(f"         Condition: {condition_id[:30]}...")
                    print(f"         Error: {error_msg[:150]}")
                    
                    if total_attempts > IMMEDIATE_RETRIES:
                        print(f"      ❌ Failed after {total_attempts - 1} total attempts - giving up")
                        status['success'] = False
                        status['complete'] = False
                        status['error'] = f"{error_type}: {error_msg[:200]}"
                        status['requests_made'] = request_count
                        break
                    
                    if retry_delay_multiplier > 1.0:
                        print(f"         🔄 Retrying in {retry_delay:.0f}s (×{retry_delay_multiplier:.1f} critical backoff)...", flush=True)
                    else:
                        print(f"         🔄 Retrying in {retry_delay:.0f}s (exponential backoff)...", flush=True)
                    await asyncio.sleep(retry_delay)
            
            # If all retries failed, break the pagination loop
            if not request_success:
                break
        
        # Set final request count
        status['requests_made'] = request_count
        
        return all_redemptions, status


async def process_market_async(
    session: aiohttp.ClientSession,
    market: Dict,
    market_index: int,
    total_markets: int,
    uploader,
    stats: Dict,
    semaphore: asyncio.Semaphore,
    db_semaphore: asyncio.Semaphore,
    rate_limiter: GoldskyRateLimiter,
    exclusive_market_lock: asyncio.Lock,
    exclusive_market_active: Dict,
    active_markets_count: Dict,
    active_markets_condition: asyncio.Condition,
    use_local_db: bool = False
):
    """Process a single market: fetch and upload (with parallel upload support)"""
    import time
    market_start_time = time.time()
    
    try:
        condition_id = market['condition_id']
        question = market['question'][:60] + "..." if len(market['question']) > 60 else market['question']
        
        # Timestamp for market start
        start_timestamp = time.strftime('%H:%M:%S')
        print(f"\n[{market_index}/{total_markets}] {question}")
        print(f"   🕐 Started: {start_timestamp}")
        print(f"   Condition ID: {condition_id[:20]}...")
        print(f"   Event ID: {market['event_id']}")
        print(f"   Volume: ${market['volume']:,.2f}")
        
        # Check if this is a very large market that needs exclusive API access
        market_volume = float(market.get('volume', 0) or 0)
        needs_exclusive_access = market_volume > 300_000_000  # $300M+
        
        if needs_exclusive_access:
            # LARGE MARKET: Wait for all active markets to finish, then get exclusive access
            
            # First, wait if another large market is already processing
            if exclusive_market_active['active']:
                print(f"   ⏸️  Waiting for exclusive access (currently: {exclusive_market_active['market_name']})...")
                async with exclusive_market_lock:
                    pass  # Wait for exclusive lock
            
            # Now wait for all currently active regular markets to finish
            print(f"   ⏳ Large market detected (${market_volume:,.0f}) - waiting for active markets to finish...")
            async with active_markets_condition:
                while active_markets_count['count'] > 0:
                    print(f"      ... waiting for {active_markets_count['count']} active market(s) to complete")
                    await active_markets_condition.wait()
            
            # Now we have exclusive access - no other markets are running
            async with exclusive_market_lock:
                exclusive_market_active['active'] = True
                exclusive_market_active['market_name'] = question[:40]
                print(f"   🔒 EXCLUSIVE MODE ACTIVATED")
                print(f"      Market: {question}")
                print(f"      Volume: ${market_volume:,.0f}")
                print(f"      All other markets are paused")
                print(f"      ⏳ SEQUENTIAL PROCESSING: Each request waits for previous to complete")
                
                # Fetch redemptions with exclusive API access
                # Requests are made SEQUENTIALLY with adaptive delays (see fetch function)
                fetch_start = time.time()
                redemptions, fetch_status = await fetch_redemptions_for_market_async(session, condition_id, market, semaphore, rate_limiter, use_local_db)
                
                exclusive_market_active['active'] = False
                exclusive_market_active['market_name'] = None
                print(f"   🔓 EXCLUSIVE MODE RELEASED - other markets can continue")
        else:
            # REGULAR MARKET: Check if we should wait for exclusive mode
            
            # Wait if a large market is processing (respect exclusive mode)
            if exclusive_market_active['active']:
                print(f"   ⏸️  Paused: Large market in progress ({exclusive_market_active['market_name']})...")
                async with exclusive_market_lock:
                    pass  # Wait for exclusive lock to be released
                print(f"   ▶️  Resumed: Large market finished")
            
            # Register this market as active
            async with active_markets_condition:
                active_markets_count['count'] += 1
            
            try:
                # Normal fetch without exclusive access
                fetch_start = time.time()
                redemptions, fetch_status = await fetch_redemptions_for_market_async(session, condition_id, market, semaphore, rate_limiter, use_local_db)
            finally:
                # Always decrement counter and notify waiting large markets
                async with active_markets_condition:
                    active_markets_count['count'] -= 1
                    if active_markets_count['count'] == 0:
                        # Notify any waiting large markets that all regular markets finished
                        active_markets_condition.notify_all()
        fetch_elapsed = time.time() - fetch_start
        
        # Track if adaptive pagination was used
        if fetch_status.get('adaptive_pagination_used', False):
            stats['adaptive_pagination_used'] += 1
        
        if redemptions:
            stats['markets_with_redemptions'] += 1
            stats['total_redemptions'] += len(redemptions)
            
            market_volume = sum(r['payout_usdc'] for r in redemptions)
            stats['total_volume'] += market_volume
            
            for r in redemptions:
                stats['unique_redeemers'].add(r['redeemer_address'])
            
            # Check if data is complete
            if not fetch_status['complete']:
                print(f"   ⚠️  INCOMPLETE: Found {len(redemptions)} redemptions (${market_volume:,.2f}) - data may be partial!", flush=True)
                print(f"   ⚠️  Reason: {fetch_status['error']}", flush=True)
                stats['incomplete_markets'] += 1
                stats['failed_fetches'].append({
                    'market': market,
                    'redemptions_fetched': len(redemptions),
                    'error': fetch_status['error'],
                    'requests_made': fetch_status['requests_made']
                })
            else:
                print(f"   ✅ Found {len(redemptions)} redemptions (${market_volume:,.2f})", flush=True)
            print(f"   ⏱️  Fetch time: {fetch_elapsed:.2f}s", flush=True)
            
            # Upload to database if enabled (with concurrency control)
            if uploader:
                db_name = "local PostgreSQL" if use_local_db else "Supabase"
                if len(redemptions) > 1000:
                    print(f"   📤 Uploading {len(redemptions)} redemptions to {db_name} (large batch)...", flush=True)
                else:
                    print(f"   📤 Uploading to {db_name}...", end=" ", flush=True)
                
                # Track upload time
                upload_start = time.time()
                
                # Use semaphore to limit concurrent DB uploads (не перегружаем БД!)
                async with db_semaphore:
                    # Run sync upload in thread pool to not block async loop
                    # Each upload uses a new client instance (thread-safe)
                    loop = asyncio.get_event_loop()
                    try:
                        success = await loop.run_in_executor(None, uploader.upload_redemptions_batch, redemptions)
                        upload_elapsed = time.time() - upload_start
                        
                        if success:
                            if len(redemptions) <= 1000:
                                print(f"✅ Uploaded (⏱️ {upload_elapsed:.2f}s)")
                            else:
                                print(f"      ✅ Successfully uploaded large batch (⏱️ {upload_elapsed:.2f}s)")
                        else:
                            if len(redemptions) <= 1000:
                                print(f"❌ Failed (⏱️ {upload_elapsed:.2f}s)")
                            else:
                                print(f"      ❌ Failed to upload large batch (⏱️ {upload_elapsed:.2f}s)")
                            print(f"      🔍 Market: {market_index} | Condition: {condition_id[:30]}...")
                            print(f"      🔍 Records: {len(redemptions)} | Volume: ${market_volume:,.2f}")
                            # Save failed upload for retry
                            stats['failed_uploads'].append({
                                'redemptions': redemptions,
                                'market_info': {
                                    'market_index': market_index,
                                    'question': question,
                                    'condition_id': condition_id,
                                    'volume': market_volume
                                }
                            })
                            stats['upload_errors'] += 1
                    except Exception as upload_err:
                        upload_elapsed = time.time() - upload_start
                        error_type = type(upload_err).__name__
                        error_detail = str(upload_err)[:150]
                        print(f"❌ Upload Exception: {error_type}")
                        print(f"   Market: [{market_index}] {question}")
                        print(f"   Condition: {condition_id[:30]}...")
                        print(f"   Records: {len(redemptions)}")
                        print(f"   Error: {error_detail}")
                        print(f"   ⏱️  Upload time before error: {upload_elapsed:.2f}s")
                        # Save failed upload for retry
                        stats['failed_uploads'].append({
                            'redemptions': redemptions,
                            'market_info': {
                                'market_index': market_index,
                                'question': question,
                                'condition_id': condition_id,
                                'volume': market_volume
                            }
                        })
                        stats['upload_errors'] += 1
        else:
            # Check if fetch failed
            if not fetch_status['success'] or not fetch_status['complete']:
                print(f"   ❌ FETCH FAILED: No redemptions retrieved", flush=True)
                print(f"   ❌ Reason: {fetch_status['error']}", flush=True)
                stats['incomplete_markets'] += 1
                stats['failed_fetches'].append({
                    'market': market,
                    'redemptions_fetched': 0,
                    'error': fetch_status['error'],
                    'requests_made': fetch_status['requests_made']
                })
            # Warn if high-volume market has no redemptions (might indicate API issue)
            elif market['volume'] > 100000 and market['closed']:
                print(f"   ⚠️  No redemptions found (suspicious: high volume ${market['volume']:,.2f}, market closed)", flush=True)
                stats['suspicious_empty_markets'] += 1
            else:
                print(f"   ⚪ No redemptions found", flush=True)
            print(f"   ⏱️  Fetch time: {fetch_elapsed:.2f}s", flush=True)
        
        # Total market processing time
        market_elapsed = time.time() - market_start_time
        end_timestamp = time.strftime('%H:%M:%S')
        print(f"   🏁 Completed: {end_timestamp} (Total: {market_elapsed:.2f}s)", flush=True)
        
        stats['markets_processed'] += 1
        
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        print(f"\n   ❌ CRITICAL ERROR processing market [{market_index}]")
        print(f"      Type: {error_type}")
        print(f"      Market: {question}")
        print(f"      Condition: {condition_id[:40]}...")
        print(f"      Error: {error_msg[:200]}")
        import traceback
        print(f"      Traceback: {traceback.format_exc()[:300]}")
        stats['markets_processed'] += 1
        stats['processing_errors'] += 1


# ==========================================
# MAIN PROCESSING (ASYNC)
# ==========================================
async def process_all_markets_async(auto_upload: bool = False, use_local_db: bool = False):
    """Main async function to process all markets in parallel"""
    import time
    script_start_time = time.time()
    
    # Setup logging to file
    log_file = setup_logging()
    
    # Resolve retry-failed mode before any use
    retry_failed_mode = '--retry-failed' in sys.argv or '--failed' in sys.argv
    
    # Use same performance settings regardless of database type
    # Database type should NOT affect API request parameters
    global MAX_CONCURRENT_MARKETS, BATCH_SIZE, BATCH_DELAY, INITIAL_BATCH_SIZE
    
    # In retry-failed mode, use more conservative settings
    if retry_failed_mode:
        MAX_CONCURRENT_MARKETS = 5  # Much lower concurrency for problematic markets
        BATCH_SIZE = 10  # Smaller batches
        BATCH_DELAY = 2  # Add delay between batches
        # Keep INITIAL_BATCH_SIZE as is - adaptive pagination will reduce if needed
        perf_mode = "🔄 RETRY MODE (Conservative settings for problematic markets)"
    else:
        MAX_CONCURRENT_MARKETS = MAX_CONCURRENT_MARKETS_CLOUD  # Always use cloud settings
        BATCH_SIZE = BATCH_SIZE_CLOUD
        BATCH_DELAY = BATCH_DELAY_CLOUD
        
        if use_local_db:
            perf_mode = "⚡ STANDARD MODE (Local PostgreSQL)"
        else:
            perf_mode = "⚡ STANDARD MODE (Supabase Cloud)"
    
    # Start timestamp
    start_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    print("=" * 70)
    if retry_failed_mode:
        print("🔄 POLYMARKET REDEMPTIONS FETCHER - RETRY FAILED MARKETS MODE")
    else:
        print("🚀 POLYMARKET REDEMPTIONS FETCHER (OPTIMIZED PARALLEL)")
    print("=" * 70)
    print(f"🕐 Script started: {start_timestamp}")
    print(f"{perf_mode}")
    if retry_failed_mode:
        print(f"⚠️  RETRY MODE:")
        print(f"   - Lower concurrency (safer for problematic markets)")
        print(f"   - Adaptive pagination will find optimal batch size")
        print(f"   - All retry mechanisms are active")
    print(f"🎯 SMART RATE LIMITING:")
    print(f"   - Goldsky API limit: {GOLDSKY_MAX_RPS} requests/second")
    print(f"   - Interval between requests: {(1.0/GOLDSKY_MAX_RPS)*1.01:.3f}s (with 1% safety margin)")
    print(f"   - Strategy: Token bucket - parallel requests queue for time slots")
    print(f"🔄 INTELLIGENT RETRY SYSTEM:")
    print(f"   - Immediate retries on error: {IMMEDIATE_RETRIES} attempts")
    print(f"   - Exponential backoff: {INITIAL_RETRY_DELAY}s → {INITIAL_RETRY_DELAY*2}s → {INITIAL_RETRY_DELAY*4}s → {INITIAL_RETRY_DELAY*8}s → {INITIAL_RETRY_DELAY*16}s (max {MAX_RETRY_DELAY}s)")
    print(f"   - Strategy: Retry immediately on failure, give API time to recover")
    print(f"🎯 SMART ADAPTIVE PAGINATION:")
    print(f"   - Initial batch size: {INITIAL_BATCH_SIZE} redemptions/request")
    print(f"   - Pattern tracking: Last {TIMEOUT_WINDOW_SIZE} requests (sliding window)")
    print(f"   - Auto-reduce threshold: {TIMEOUT_THRESHOLD_FOR_REDUCTION*100:.0f}% timeout rate")
    print(f"   - Critical threshold: {TIMEOUT_THRESHOLD_CRITICAL*100:.0f}% timeout rate (×1.5-3.0 retry delay)")
    print(f"   - Progressive reduction: {INITIAL_BATCH_SIZE} → {INITIAL_BATCH_SIZE//2} → {INITIAL_BATCH_SIZE//4} → ... (min: {MIN_BATCH_SIZE})")
    print(f"   - Strategy: Pattern-based adaptation + dynamic retry delays")
    print(f"⚡ Processing settings:")
    print(f"   - Batch size: {BATCH_SIZE} markets")
    print(f"   - Concurrent markets: {MAX_CONCURRENT_MARKETS}")
    print(f"   - Request timeout: {REQUEST_TIMEOUT}s")
    print(f"   - Delay between batches: {BATCH_DELAY}s")
    print(f"   - Database uploads: Parallel (max {MAX_CONCURRENT_DB_UPLOADS} concurrent)")
    
    # Initialize database uploader if auto_upload is enabled
    uploader = None
    if auto_upload:
        db_name = "LOCAL PostgreSQL" if use_local_db else "Supabase"
        print(f"🔄 Auto-upload to {db_name} enabled")
        try:
            # Add parent directory to path for imports
            script_dir = os.path.dirname(os.path.abspath(__file__))
            parent_dir = os.path.dirname(script_dir)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            
            from db.supabase_uploader import SupabaseUploader
            uploader = SupabaseUploader(use_local_db=use_local_db)
            print(f"✅ Connected to {db_name}")
        except Exception as e:
            print(f"❌ Failed to connect to {db_name}: {e}")
            print("   Continuing without upload...")
            auto_upload = False
            uploader = None
    
    # 1. Load markets from DB or file
    # Check for source flags
    use_json_file = '--from-json' in sys.argv or '--file' in sys.argv or '-f' in sys.argv
    custom_file = None
    for i, arg in enumerate(sys.argv):
        if arg in ['--file', '-f'] and i + 1 < len(sys.argv):
            custom_file = sys.argv[i + 1]
            use_json_file = True
            break
    
    if retry_failed_mode:
        # Load failed markets file
        if custom_file:
            if not os.path.exists(custom_file):
                print(f"❌ Specified failed markets file not found: {custom_file}")
                return
            failed_file = custom_file
            print(f"📌 Using specified failed markets file: {failed_file}")
        else:
            failed_file = find_latest_failed_markets_file()
            if not failed_file:
                return
        
        markets = load_failed_markets_file(failed_file)
        print(f"\n🔄 RETRY MODE: Processing {len(markets)} previously failed markets")
    
    elif use_json_file:
        # Legacy mode: load from JSON file
        print(f"📄 DATA SOURCE: JSON file (legacy mode)")
        print(f"   💡 Tip: Use database mode (no --file flag) for better performance")
        print()
        
        if custom_file:
            if not os.path.exists(custom_file):
                print(f"❌ Specified file not found: {custom_file}")
                return
            events_file = custom_file
            print(f"📌 Using specified file: {events_file}")
        else:
            events_file = find_latest_events_file()
            if not events_file:
                return
        
        events = load_events_file(events_file)
        
        # Extract markets from events
        print(f"\n📊 Extracting markets from events...")
        markets = extract_markets(events)
        
        if FILTER_CLOSED_ONLY:
            print(f"   Filter: Closed markets only")
        if MIN_VOLUME > 0:
            print(f"   Filter: Min volume ${MIN_VOLUME:,.2f}")
    
    else:
        # New mode: load from database (DEFAULT)
        print(f"📊 DATA SOURCE: Database (recommended)")
        print(f"   Database: {'Local PostgreSQL' if use_local_db else 'Supabase'}")
        print()
        
        # Get markets from database
        markets = get_markets_from_db(
            use_local_db=use_local_db,
            limit=MAX_MARKETS
        )
        
        if not markets:
            print("❌ No markets found in database")
            print("   Make sure you ran: python fetch_events_parallel_optimized.py --upload --local")
            return
    
    print()
    print(f"✅ Found {len(markets)} markets to process")
    
    # Apply MAX_MARKETS limit (only if not already applied by DB query)
    if MAX_MARKETS and not use_json_file and len(markets) > MAX_MARKETS:
        markets = markets[:MAX_MARKETS]
        print(f"⚠️  Limited to {MAX_MARKETS} markets")
    elif MAX_MARKETS and use_json_file:
        markets = markets[:MAX_MARKETS]
        print(f"⚠️  Limited to {MAX_MARKETS} markets")
    
    if not markets:
        print("❌ No markets to process")
        return
    
    # 3. Fetch redemptions for each market in parallel (with batching)
    print(f"\n💰 Fetching and uploading redemptions in batches...")
    print(f"   Batch size: {BATCH_SIZE} markets")
    print(f"   Concurrent per batch: {MAX_CONCURRENT_MARKETS} markets")
    print(f"   Delay between batches: {BATCH_DELAY}s")
    print("-" * 70)
    
    stats = {
        'markets_processed': 0,
        'markets_with_redemptions': 0,
        'total_redemptions': 0,
        'total_volume': 0.0,
        'unique_redeemers': set(),
        'upload_errors': 0,
        'processing_errors': 0,
        'suspicious_empty_markets': 0,  # High volume closed markets with no redemptions
        'failed_uploads': [],  # List of (redemptions, market_info) tuples that failed to upload
        'incomplete_markets': 0,  # Markets with partial data due to errors
        'failed_fetches': [],  # Markets that failed to fetch completely
        'adaptive_pagination_used': 0  # Markets that used adaptive pagination
    }
    
    # Create semaphores to limit concurrent operations
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_MARKETS)  # Ограничение на параллельную обработку маркетов
    db_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DB_UPLOADS)  # Ограничение на загрузку в БД
    
    # Create rate limiter for Goldsky API (THIS IS THE KEY!)
    rate_limiter = GoldskyRateLimiter(max_rps=GOLDSKY_MAX_RPS)
    print(f"\n✅ Rate limiter initialized: {GOLDSKY_MAX_RPS} RPS max")
    
    # Create exclusive lock for very large/problematic markets
    # When a market needs exclusive access, it locks this and blocks all other markets
    exclusive_market_lock = asyncio.Lock()
    exclusive_market_active = {'active': False, 'market_name': None}  # Shared state
    active_markets_count = {'count': 0}  # Track how many markets are currently processing
    active_markets_condition = asyncio.Condition()  # To wait for all markets to finish
    
    # Create aiohttp session
    async with aiohttp.ClientSession() as session:
        # Process markets in batches
        total_batches = (len(markets) + BATCH_SIZE - 1) // BATCH_SIZE
        
        for batch_num in range(0, len(markets), BATCH_SIZE):
            batch_markets = markets[batch_num:batch_num + BATCH_SIZE]
            batch_index = batch_num // BATCH_SIZE + 1
            
            # Batch start time
            batch_start_timestamp = time.strftime('%H:%M:%S')
            
            print(f"\n📦 Processing batch {batch_index}/{total_batches} ({len(batch_markets)} markets)")
            print(f"   🕐 Batch started: {batch_start_timestamp}")
            print(f"   Markets {batch_num + 1} to {batch_num + len(batch_markets)}")
            print("-" * 70)
            
            # Track batch timing
            import time
            batch_start = time.time()
            
            # Create tasks for this batch only
            tasks = []
            for i, market in enumerate(batch_markets):
                market_index = batch_num + i + 1
                task = process_market_async(
                    session, 
                    market, 
                    market_index, 
                    len(markets), 
                    uploader, 
                    stats, 
                    semaphore,
                    db_semaphore,
                    rate_limiter,
                    exclusive_market_lock,
                    exclusive_market_active,
                    active_markets_count,
                    active_markets_condition,
                    use_local_db
                )
                tasks.append(task)
            
            # Wait for all tasks in this batch to complete
            await asyncio.gather(*tasks, return_exceptions=True)
            
            # Show batch completion time
            batch_elapsed = time.time() - batch_start
            batch_end_timestamp = time.strftime('%H:%M:%S')
            markets_in_batch = len(batch_markets)
            avg_time_per_market = batch_elapsed / markets_in_batch if markets_in_batch > 0 else 0
            
            # Get rate limiter stats (total since start)
            rl_stats = rate_limiter.get_stats()
            total_script_elapsed = time.time() - script_start_time
            overall_rps = rl_stats['total_requests'] / total_script_elapsed if total_script_elapsed > 0 else 0
            
            print(f"\n✅ Batch {batch_index}/{total_batches} completed")
            print(f"   🏁 Finished: {batch_end_timestamp}")
            print(f"   ⏱️  Batch time: {batch_elapsed:.1f}s")
            print(f"   📊 Avg per market: {avg_time_per_market:.2f}s")
            print(f"   🎯 Total API requests: {rl_stats['total_requests']:,} (Overall RPS: {overall_rps:.2f})")
            
            # NO delay between batches! Rate limiter handles everything
            # Immediately proceed to next batch
    
    # 4. Retry failed/incomplete market fetches
    if stats['failed_fetches']:
        print("\n" + "=" * 70)
        print("🔄 RETRYING FAILED/INCOMPLETE MARKET FETCHES")
        print("=" * 70)
        retry_fetch_start_time = time.time()
        retry_fetch_timestamp = time.strftime('%H:%M:%S')
        print(f"🕐 Retry fetch started: {retry_fetch_timestamp}")
        print(f"Found {len(stats['failed_fetches'])} failed/incomplete markets")
        print(f"Strategy: One market at a time with longer timeout")
        print(f"Waiting 5 seconds to let API cool down...")
        await asyncio.sleep(5)
        print()
        
        retry_fetch_success = 0
        retry_fetch_failed = 0
        retry_fetch_improved = 0  # Got more data but still incomplete
        still_failed_fetches = []
        
        # Create new stricter semaphore (only 1 at a time for retries)
        retry_semaphore = asyncio.Semaphore(1)
        
        for i, failed_item in enumerate(stats['failed_fetches'], 1):
            market = failed_item['market']
            previous_count = failed_item['redemptions_fetched']
            condition_id = market['condition_id']
            question = market['question'][:60] + "..." if len(market['question']) > 60 else market['question']
            
            retry_item_start = time.time()
            retry_timestamp = time.strftime('%H:%M:%S')
            
            print(f"\n[Retry Fetch {i}/{len(stats['failed_fetches'])}] {question}")
            print(f"   🕐 Retry time: {retry_timestamp}")
            print(f"   Condition: {condition_id[:30]}...")
            print(f"   Previous: {previous_count} redemptions")
            print(f"   Error was: {failed_item['error'][:100]}")
            print(f"   🔄 Retrying fetch...", flush=True)
            
            try:
                # Retry with same function but one at a time
                redemptions_retry, fetch_status_retry = await fetch_redemptions_for_market_async(
                    session, condition_id, market, retry_semaphore, rate_limiter, use_local_db
                )
                retry_item_elapsed = time.time() - retry_item_start
                
                if fetch_status_retry['complete'] and fetch_status_retry['success']:
                    print(f"   ✅ Success! Got {len(redemptions_retry)} redemptions (⏱️ {retry_item_elapsed:.2f}s)")
                    retry_fetch_success += 1
                    
                    # Upload if enabled and we got data
                    if uploader and redemptions_retry:
                        market_volume = sum(r['payout_usdc'] for r in redemptions_retry)
                        print(f"   📤 Uploading {len(redemptions_retry)} redemptions...", end=" ", flush=True)
                        try:
                            success = await asyncio.get_event_loop().run_in_executor(
                                None, uploader.upload_redemptions_batch, redemptions_retry
                            )
                            if success:
                                print("✅ Uploaded")
                                # Update stats
                                if previous_count == 0:
                                    stats['markets_with_redemptions'] += 1
                                stats['total_redemptions'] += len(redemptions_retry) - previous_count
                                stats['total_volume'] += market_volume
                                for r in redemptions_retry:
                                    stats['unique_redeemers'].add(r['redeemer_address'])
                            else:
                                print("❌ Upload failed")
                        except Exception as e:
                            print(f"❌ Upload error: {type(e).__name__}")
                    
                elif len(redemptions_retry) > previous_count:
                    # Got more data but still incomplete
                    print(f"   ⚠️  Improved but incomplete: {len(redemptions_retry)} redemptions (was {previous_count}) (⏱️ {retry_item_elapsed:.2f}s)")
                    print(f"   ⚠️  Still failing: {fetch_status_retry['error'][:100]}")
                    retry_fetch_improved += 1
                    still_failed_fetches.append({
                        'market': market,
                        'redemptions_fetched': len(redemptions_retry),
                        'error': fetch_status_retry['error'],
                        'requests_made': fetch_status_retry['requests_made']
                    })
                    
                    # Upload improved data if enabled
                    if uploader and redemptions_retry and len(redemptions_retry) > previous_count:
                        market_volume = sum(r['payout_usdc'] for r in redemptions_retry)
                        print(f"   📤 Uploading improved data ({len(redemptions_retry)} redemptions)...", end=" ", flush=True)
                        try:
                            success = await asyncio.get_event_loop().run_in_executor(
                                None, uploader.upload_redemptions_batch, redemptions_retry
                            )
                            if success:
                                print("✅ Uploaded")
                                # Update stats with delta
                                if previous_count == 0:
                                    stats['markets_with_redemptions'] += 1
                                stats['total_redemptions'] += len(redemptions_retry) - previous_count
                                stats['total_volume'] += market_volume
                                for r in redemptions_retry:
                                    stats['unique_redeemers'].add(r['redeemer_address'])
                            else:
                                print("❌ Upload failed")
                        except Exception as e:
                            print(f"❌ Upload error: {type(e).__name__}")
                else:
                    print(f"   ❌ Still failed: {len(redemptions_retry)} redemptions (⏱️ {retry_item_elapsed:.2f}s)")
                    print(f"   ❌ Reason: {fetch_status_retry['error'][:100]}")
                    retry_fetch_failed += 1
                    still_failed_fetches.append(failed_item)
                    
            except Exception as e:
                retry_item_elapsed = time.time() - retry_item_start
                print(f"   ❌ Retry Exception: {type(e).__name__} (⏱️ {retry_item_elapsed:.2f}s)")
                print(f"      Error: {str(e)[:100]}")
                retry_fetch_failed += 1
                still_failed_fetches.append(failed_item)
            
            # Short delay between retries
            if i < len(stats['failed_fetches']):
                print(f"   ⏳ Waiting 2 seconds before next retry...")
                await asyncio.sleep(2)
        
        retry_fetch_elapsed = time.time() - retry_fetch_start_time
        retry_fetch_end_timestamp = time.strftime('%H:%M:%S')
        
        print(f"\n📊 Retry Fetch Results:")
        print(f"   🏁 Finished: {retry_fetch_end_timestamp}")
        print(f"   ⏱️  Total retry time: {retry_fetch_elapsed:.1f}s")
        print(f"   ✅ Fully successful: {retry_fetch_success}/{len(stats['failed_fetches'])}")
        print(f"   📈 Improved (partial): {retry_fetch_improved}/{len(stats['failed_fetches'])}")
        print(f"   ❌ Still failed: {retry_fetch_failed}/{len(stats['failed_fetches'])}")
        
        # Update stats
        stats['incomplete_markets'] = len(still_failed_fetches)
        stats['failed_fetches'] = still_failed_fetches
        
        # Save still-failed markets to file
        if still_failed_fetches:
            failed_markets_file = f"output/failed_markets_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            os.makedirs('output', exist_ok=True)
            
            failed_markets_data = []
            for item in still_failed_fetches:
                failed_markets_data.append({
                    'condition_id': item['market']['condition_id'],
                    'question': item['market']['question'],
                    'event_id': item['market']['event_id'],
                    'volume': item['market']['volume'],
                    'redemptions_fetched': item['redemptions_fetched'],
                    'error': item['error'],
                    'requests_made': item['requests_made']
                })
            
            with open(failed_markets_file, 'w', encoding='utf-8') as f:
                json.dump(failed_markets_data, f, indent=2, ensure_ascii=False)
            
            print(f"\n💾 Still-failed markets saved to: {failed_markets_file}")
            print(f"   Total markets: {len(still_failed_fetches)}")
            print(f"   You can review these markets later")
    
    # 5. Retry failed uploads
    if auto_upload and uploader and stats['failed_uploads']:
        print("\n" + "=" * 70)
        print("🔄 RETRYING FAILED UPLOADS")
        print("=" * 70)
        retry_start_time = time.time()
        retry_start_timestamp = time.strftime('%H:%M:%S')
        print(f"🕐 Retry started: {retry_start_timestamp}")
        print(f"Found {len(stats['failed_uploads'])} failed uploads ({sum(len(f['redemptions']) for f in stats['failed_uploads']):,} records)")
        print(f"Waiting 10 seconds to let API and DB cool down...")
        await asyncio.sleep(10)  # Даем API и БД время "отдохнуть"
        print(f"Attempting to retry uploads...")
        print()
        
        retry_success = 0
        retry_failed = 0
        still_failed_items = []
        
        for i, failed_item in enumerate(stats['failed_uploads'], 1):
            redemptions = failed_item['redemptions']
            market_info = failed_item['market_info']
            
            retry_item_start = time.time()
            retry_timestamp = time.strftime('%H:%M:%S')
            
            print(f"\n[Retry {i}/{len(stats['failed_uploads'])}] Market #{market_info['market_index']}")
            print(f"   🕐 Retry time: {retry_timestamp}")
            print(f"   Question: {market_info['question'][:60]}...")
            print(f"   Condition: {market_info['condition_id'][:30]}...")
            print(f"   Records: {len(redemptions)} | Volume: ${market_info['volume']:,.2f}")
            print(f"   🔄 Retrying upload...", end=" ", flush=True)
            
            try:
                # Use default chunk size (БД справляется, проблема была в API timing)
                # БД для локальной будет использовать chunk_size=5000 (быстро!)
                success = uploader.upload_redemptions_batch(redemptions)
                retry_item_elapsed = time.time() - retry_item_start
                
                if success:
                    print(f"✅ Success! (⏱️ {retry_item_elapsed:.2f}s)")
                    retry_success += 1
                else:
                    print(f"❌ Failed again (⏱️ {retry_item_elapsed:.2f}s)")
                    retry_failed += 1
                    still_failed_items.append(failed_item)
            except Exception as e:
                retry_item_elapsed = time.time() - retry_item_start
                print(f"❌ Exception: {type(e).__name__} (⏱️ {retry_item_elapsed:.2f}s)")
                print(f"      Error: {str(e)[:100]}")
                retry_failed += 1
                still_failed_items.append(failed_item)
            
            # Longer delay between retries to let DB/API rest
            if i < len(stats['failed_uploads']):
                print(f"   ⏳ Waiting 3 seconds before next retry...")
                await asyncio.sleep(3)
        
        retry_elapsed = time.time() - retry_start_time
        retry_end_timestamp = time.strftime('%H:%M:%S')
        
        print(f"\n📊 Retry Results:")
        print(f"   🏁 Finished: {retry_end_timestamp}")
        print(f"   ⏱️  Total retry time: {retry_elapsed:.1f}s")
        print(f"   ✅ Successful: {retry_success}/{len(stats['failed_uploads'])}")
        print(f"   ❌ Still failed: {retry_failed}/{len(stats['failed_uploads'])}")
        
        # Save still-failed data to file
        if still_failed_items:
            failed_file = f"output/failed_redemptions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            # Create output directory if needed
            os.makedirs('output', exist_ok=True)
            
            # Prepare data for saving
            failed_data = []
            for item in still_failed_items:
                failed_data.append({
                    'market_info': item['market_info'],
                    'redemptions': item['redemptions']
                })
            
            with open(failed_file, 'w', encoding='utf-8') as f:
                json.dump(failed_data, f, indent=2, ensure_ascii=False)
            
            print(f"\n💾 Still-failed data saved to: {failed_file}")
            print(f"   Total records: {sum(len(item['redemptions']) for item in still_failed_items):,}")
            print(f"   You can retry later using: python scripts/db/supabase_uploader.py {failed_file} --redemptions {'--local' if use_local_db else ''}")
        
        # Update stats
        stats['upload_errors'] = retry_failed  # Update to reflect final count
    
    # 6. Print final statistics
    print("\n" + "=" * 70)
    print("📊 FINAL STATISTICS")
    print("=" * 70)
    print(f"Markets processed:          {stats['markets_processed']}")
    print(f"Markets with redemptions:   {stats['markets_with_redemptions']}")
    print(f"Total redemptions:          {stats['total_redemptions']:,}")
    print(f"Unique redeemers:           {len(stats['unique_redeemers']):,}")
    print(f"Total payout volume:        ${stats['total_volume']:,.2f}")
    
    if stats['processing_errors'] > 0:
        print(f"\n⚠️  Processing Errors:")
        print(f"   Failed markets:          {stats['processing_errors']}")
    
    if stats['suspicious_empty_markets'] > 0:
        print(f"\n⚠️  Suspicious Cases:")
        print(f"   High-volume closed markets with no redemptions: {stats['suspicious_empty_markets']}")
        print(f"   (This might indicate GraphQL API rate limiting or data issues)")
    
    if stats['incomplete_markets'] > 0:
        print(f"\n⚠️  Incomplete/Failed Markets:")
        print(f"   Markets with errors/timeouts: {stats['incomplete_markets']}")
        print(f"   (Data for these markets may be partial or missing)")
        if stats['failed_fetches']:
            print(f"   💾 Failed markets saved to output/failed_markets_*.json")
    
    if stats['adaptive_pagination_used'] > 0:
        print(f"\n🎯 Adaptive Pagination:")
        print(f"   Markets using adaptive pagination: {stats['adaptive_pagination_used']}")
        print(f"   (Automatically reduced batch size for problematic markets)")
    
    if auto_upload and uploader:
        print(f"\n💾 Database Upload:")
        print(f"   Uploaded:                {uploader.stats['redemptions_inserted']:,} redemptions")
        
        # Show retry information if there were any
        if len(stats.get('failed_uploads', [])) > 0:
            original_failures = len(stats['failed_uploads'])
            final_failures = stats['upload_errors']
            retry_successes = original_failures - final_failures
            
            print(f"\n   🔄 Retry Statistics:")
            print(f"   Initial failures:        {original_failures}")
            print(f"   Retry successes:         {retry_successes}")
            print(f"   Final failures:          {final_failures}")
            
            if final_failures > 0:
                print(f"\n   ⚠️  {final_failures} upload(s) still failed after retry")
                print(f"   💾 Failed data saved to output/failed_redemptions_*.json")
        elif stats['upload_errors'] > 0:
            print(f"   Upload errors:           {stats['upload_errors']}")
            
        # Show detailed error summary
        if uploader.stats['errors']:
            print(f"\n   📋 ERROR SUMMARY ({len(uploader.stats['errors'])} total errors):")
            print(f"   " + "=" * 66)
            
            # Categorize errors
            timeout_errors = [e for e in uploader.stats['errors'] if '57014' in e or 'timeout' in e.lower()]
            other_errors = [e for e in uploader.stats['errors'] if e not in timeout_errors]
            
            if timeout_errors:
                print(f"   ⏰ Statement Timeouts: {len(timeout_errors)}")
                for error in timeout_errors[:3]:
                    print(f"      • {error[:90]}")
            
            if other_errors:
                print(f"   ❌ Other Errors: {len(other_errors)}")
                for error in other_errors[:3]:
                    print(f"      • {error[:90]}")
            
            if len(uploader.stats['errors']) > 6:
                print(f"   ... and {len(uploader.stats['errors']) - 6} more errors")
    
    # Total execution time and performance metrics
    total_elapsed = time.time() - script_start_time
    end_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    hours = int(total_elapsed // 3600)
    minutes = int((total_elapsed % 3600) // 60)
    seconds = int(total_elapsed % 60)
    
    print(f"\n🕐 Script finished: {end_timestamp}")
    print(f"⏱️  Total execution time: {hours}h {minutes}m {seconds}s ({total_elapsed:.1f}s)")
    
    # Performance metrics
    if stats['total_redemptions'] > 0 and total_elapsed > 0:
        redemptions_per_sec = stats['total_redemptions'] / total_elapsed
        markets_per_sec = stats['markets_processed'] / total_elapsed
        
        # Rate limiter statistics
        rl_stats = rate_limiter.get_stats()
        actual_rps = rl_stats['total_requests'] / total_elapsed if total_elapsed > 0 else 0
        efficiency = (actual_rps / GOLDSKY_MAX_RPS * 100) if GOLDSKY_MAX_RPS > 0 else 0
        
        print(f"\n📈 Performance:")
        print(f"   Redemptions/sec:        {redemptions_per_sec:.1f}")
        print(f"   Markets/sec:            {markets_per_sec:.2f}")
        if auto_upload and uploader:
            db_name = "PostgreSQL" if use_local_db else "Supabase"
            print(f"   Database ({db_name}):   {uploader.stats['redemptions_inserted'] / total_elapsed:.1f} records/sec")
        
        print(f"\n🎯 API Rate Limiting Stats:")
        print(f"   Total API requests:     {rl_stats['total_requests']:,}")
        print(f"   Actual RPS:             {actual_rps:.2f} / {GOLDSKY_MAX_RPS} max")
        print(f"   API efficiency:         {efficiency:.1f}%")
        print(f"   Total throttle time:    {rl_stats['total_wait_time']:.1f}s ({rl_stats['total_wait_time']/total_elapsed*100:.1f}% of runtime)")
        if efficiency > 85:
            print(f"   ✅ Excellent API utilization!")
        elif efficiency > 70:
            print(f"   ✅ Good API utilization")
        else:
            print(f"   ⚠️  Low API utilization - consider increasing parallelism")
    
    print("=" * 70)
    
    if stats['total_redemptions'] == 0:
        print("\n⚠️ No redemptions found for any market")
    elif auto_upload and uploader:
        db_name = "local PostgreSQL" if use_local_db else "Supabase"
        print(f"\n✅ Successfully uploaded {uploader.stats['redemptions_inserted']:,} redemptions to {db_name}!")
    else:
        print(f"\n✅ Processed {stats['total_redemptions']:,} redemptions")
        print("   💡 Use --upload flag to automatically upload to database")
        print("   💡 Use --local flag to upload to local PostgreSQL instead of Supabase")
    
    print("\n🎉 Done!")
    print(f"\n📁 Full log saved to: {log_file}")
    
    # Cleanup logging
    cleanup_logging()


def process_all_markets(auto_upload: bool = False, use_local_db: bool = False):
    """Synchronous wrapper for async processing"""
    try:
        asyncio.run(process_all_markets_async(auto_upload, use_local_db))
    except KeyboardInterrupt:
        print("\n\n⚠️  Script interrupted by user (Ctrl+C)")
        print("📁 Partial log has been saved")
        cleanup_logging()
    except Exception as e:
        print(f"\n\n❌ FATAL ERROR: {type(e).__name__}")
        print(f"   {str(e)}")
        import traceback
        print(f"\n{traceback.format_exc()}")
        cleanup_logging()
        raise


# ==========================================
# CLI ENTRY POINT
# ==========================================
if __name__ == '__main__':
    import sys

    # Check for flags
    auto_upload = '--upload' in sys.argv or '-u' in sys.argv
    use_local_db = '--local' in sys.argv or '-l' in sys.argv

    # Show help if requested
    if '--help' in sys.argv or '-h' in sys.argv:
        print("Usage: python fetch_redemptions.py [OPTIONS]")
        print()
        print("Options:")
        print("  --upload, -u       Upload redemptions to database")
        print("  --local, -l        Use local PostgreSQL instead of Supabase (requires --upload)")
        print("  --file FILE, -f    Use specific JSON events file (legacy mode)")
        print("  --from-json        Load markets from JSON file instead of database (legacy)")
        print("  --retry-failed     Retry processing failed markets from latest failed_markets_*.json")
        print("  --help, -h         Show this help message")
        print()
        print("Data Sources:")
        print("  DEFAULT: Database (recommended)")
        print("    - Reads markets from database (events + markets tables)")
        print("    - Faster, more reliable, always up-to-date")
        print("    - Requires: fetch_events_parallel_optimized.py --upload --local")
        print()
        print("  LEGACY: JSON file (--file or --from-json)")
        print("    - Reads from JSON file (data/json_output/)")
        print("    - Useful for specific snapshots or offline work")
        print()
        print("Examples:")
        print("  python fetch_redemptions.py --upload --local")
        print("      Fetch from DATABASE and upload to PostgreSQL (RECOMMENDED)")
        print()
        print("  python fetch_redemptions.py --upload")
        print("      Fetch from DATABASE and upload to Supabase")
        print()
        print("  python fetch_redemptions.py --file data/json_output/events.json --upload --local")
        print("      Fetch from JSON FILE (legacy) and upload to PostgreSQL")
        print()
        print("  python fetch_redemptions.py --retry-failed --upload --local")
        print("      Retry failed markets and upload to PostgreSQL")
        sys.exit(0)

    if use_local_db and not auto_upload:
        print("⚠️  Warning: --local flag requires --upload flag")
        print("   Use: python fetch_redemptions.py --upload --local")
        sys.exit(1)

    if auto_upload:
        db_name = "local PostgreSQL" if use_local_db else "Supabase"
        print(f"🔄 Auto-upload to {db_name} enabled")

    process_all_markets(auto_upload=auto_upload, use_local_db=use_local_db)
