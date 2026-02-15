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
- HYBRID TEMPORAL PAGINATION ($200M+ markets):
  * Timestamp-based pagination (timestamp_gte)
  * Temporal windows: 7-21 days per window (зависит от volume)
  * Early break: прерывается при достижении window_end
  * GoldSky limitation: timestamp_lt не работает, используем manual filtering
  * Exclusive mode: isolated thread for stability
  * Aggressive batch reduction on timeouts
- Умный Rate Limiter для Goldsky API:
  * Token Bucket алгоритм - точно 9 RPS
  * Lock не держится во время sleep - полная параллельность
  * Автоматическое распределение временных слотов
- 🔒 EXCLUSIVE MODE для больших маркетов (>=$200M):
  * Останавливает ВСЕ другие маркеты
  * ПОСЛЕДОВАТЕЛЬНАЯ обработка: ждет ответа от предыдущего запроса
  * 🎯 SMART DELAY STARTUP (2026):
    - $1B+: стартует с 8s delay (дает БД больше времени)
    - $500M+: стартует с 6s delay
    - $200M+: стартует с 4s delay
    - Меньше timeouts с первого запроса
  * 🎯 АГРЕССИВНОЕ СНИЖЕНИЕ BATCH SIZE:
    - При 3+ consecutive timeouts: СРАЗУ снижает batch size
    - Приоритет снижению batch над увеличением delay
    - Progressive batch sizing: 1000 → 500 → 250 → 100 → 50
    - Delay увеличивается только когда batch на минимуме (50)
    - При 5+ успехах подряд: увеличивает batch size обратно
  * 🎯 CONNECTION POOL REFRESH:
    - Детектирует "Session is closed" ошибки
    - Прерывает обработку окна для reconnect
    - Позволяет продолжить в новом запуске
  * Результат: стабильная работа + быстрая адаптация к нагрузке БД
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
    def __init__(self, log_file, original_stdout=None):
        self.terminal = sys.stdout
        self.original_stdout = original_stdout or sys.stdout  # For Docker logs
        self.log_file = open(log_file, 'a', encoding='utf-8')
        # Try to also write to /dev/stdout for Docker logs visibility
        self.docker_stdout = None
        try:
            if os.path.exists('/dev/stdout'):
                self.docker_stdout = open('/dev/stdout', 'w', encoding='utf-8', buffering=1)
        except:
            pass
        
    def write(self, message):
        self.terminal.write(message)
        self.terminal.flush()  # Force immediate output to terminal
        self.log_file.write(message)
        self.log_file.flush()  # Force immediate write to file
        # Also write to /dev/stdout for Docker logs (if in container)
        if self.docker_stdout:
            try:
                self.docker_stdout.write(message)
                self.docker_stdout.flush()
            except:
                pass  # Ignore errors if stdout is closed
        
    def flush(self):
        self.terminal.flush()
        self.log_file.flush()
        if self.docker_stdout:
            try:
                self.docker_stdout.flush()
            except:
                pass
    
    def close(self):
        self.log_file.close()
        if self.docker_stdout:
            try:
                self.docker_stdout.close()
            except:
                pass

# Global logger instance
_logger = None
_log_file_handle = None
_original_print = print  # Save original print function

def _custom_print(*args, **kwargs):
    """Custom print that also writes to log file in Docker mode"""
    global _log_file_handle
    # Call original print (goes to stdout)
    _original_print(*args, **kwargs)
    # Also write to log file if in Docker mode
    if _log_file_handle:
        try:
            # Convert args to string like print does
            message = ' '.join(str(arg) for arg in args)
            end = kwargs.get('end', '\n')
            _log_file_handle.write(message + end)
            _log_file_handle.flush()
        except:
            pass

def setup_logging():
    """Setup dual logging to console and file"""
    global _logger, _log_file_handle, print
    
    # Create logs directory if it doesn't exist
    log_dir = 'logs'
    os.makedirs(log_dir, exist_ok=True)
    
    # Create log file with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(log_dir, f'redemptions_fetch_{timestamp}.log')
    
    # Save original stdout BEFORE DualLogger intercepts it (for Docker logs)
    original_stdout = sys.stdout
    
    # Setup dual logger
    _logger = DualLogger(log_file, original_stdout=original_stdout)
    _log_file_handle = _logger.log_file  # Keep reference for direct writing
    
    # ⚠️ DO NOT intercept sys.stdout in Docker (breaks cron | tee piping)
    # Only intercept in local development (no /dev/stdout)
    in_docker = os.path.exists('/dev/stdout')
    if not in_docker:
        sys.stdout = _logger
    else:
        # In Docker: replace print() with custom version that writes to file
        import builtins
        builtins.print = _custom_print
    
    print(f"📝 Logging to: {log_file}")
    print(f"   All output will be saved to this file in real-time")
    if in_docker:
        print(f"   Docker mode: stdout not intercepted (visible in docker logs)")
    print()
    
    return log_file

def cleanup_logging():
    """Cleanup logging and restore stdout"""
    global _logger, _log_file_handle
    if _logger:
        sys.stdout = _logger.terminal
        _logger.close()
        _logger = None
        _log_file_handle = None
    # Restore original print
    import builtins
    builtins.print = _original_print

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

# Goldsky API Token (optional)
GOLDSKY_API_TOKEN = os.getenv('GOLDSKY_API_TOKEN', None)

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

REQUEST_TIMEOUT = 300  # Timeout 5 min for very large markets

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

# ==========================================
# MARKET SIZE THRESHOLDS (Пороги разделения маркетов)
# ==========================================
# Эти значения определяют когда применяются специальные стратегии обработки
EXCLUSIVE_ACCESS_THRESHOLD = 200_000_000  # $200M+ маркеты: эксклюзивный доступ (останавливает другие маркеты)
TEMPORAL_WINDOWING_THRESHOLD = 400_000_000  # $300M+ маркеты: временные окна + специальные задержки

# Initial delay level for large markets ($300M+)
# Delay levels: [2.0, 4.0, 6.0, 8.0, 10.0, 15.0, 20.0]
# Level 0 = 2s, Level 1 = 4s, Level 2 = 6s, Level 3 = 8s, Level 4 = 10s, Level 5 = 15s, Level 6 = 20s
INITIAL_DELAY_LEVEL_LARGE_MARKETS = 1  # Start at 4s delay (balanced between speed and reliability)

# Early exit threshold for temporal windowing ($300M+)
# After N consecutive empty windows, switch to probe mode (LIMIT 1 check for each remaining window)
EARLY_EXIT_THRESHOLD = 3  # AGGRESSIVE: Switch to smart skip after just 1 empty window (faster!)
PROBE_MODE_ENABLED = False  # Smart skip enabled - probes once for all remaining windows

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
            'start_date': None,  # Not available in failed markets - will use fallback
            'end_date': None,    # Not available in failed markets - will use fallback
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
            
            # ⚠️ КРИТИЧЕСКИ ВАЖНО: Фильтр по датам событий
            # Импортируем конфиг для получения дат
            current_script_dir = os.path.dirname(os.path.abspath(__file__))
            if current_script_dir not in sys.path:
                sys.path.insert(0, current_script_dir)
            
            try:
                import fetch_events_config as config
                
                if hasattr(config, 'START_DATE') and config.START_DATE:
                    filters.append("e.end_date >= %s")
                    params.append(config.START_DATE)
                    print(f"🔍 Filter: Events from {config.START_DATE.date()}")
                
                if hasattr(config, 'END_DATE') and config.END_DATE:
                    filters.append("e.end_date <= %s")
                    params.append(config.END_DATE)
                    print(f"🔍 Filter: Events until {config.END_DATE.date()}")
            except ImportError:
                print("⚠️  Warning: Could not import fetch_events_config, skipping date filters")
            
            if filters:
                sql_query += " AND " + " AND ".join(filters)
            
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
                    'start_date': row.get('start_date') or row.get('start_date_iso'),  # For temporal windowing
                    'end_date': row.get('end_date') or row.get('end_date_iso'),      # For temporal windowing
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
                    'start_date': row.get('start_date') or row.get('start_date_iso'),  # For temporal windowing
                    'end_date': row.get('end_date') or row.get('end_date_iso'),      # For temporal windowing
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
                'start_date': market.get('startDate') or market.get('startDateIso') or market.get('createdAt') or market.get('creationDate'),
                'end_date': market.get('endDate') or market.get('endDateIso') or market.get('end_date_iso') or market.get('closedTime') or market.get('closedAt'),
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
def split_into_time_windows(start_timestamp: int, end_timestamp: int, window_days: int = 7) -> List[tuple]:
    """Split time range into windows for temporal pagination"""
    windows = []
    window_size = window_days * 24 * 60 * 60
    
    current_start = start_timestamp
    while current_start < end_timestamp:
        current_end = min(current_start + window_size, end_timestamp)
        windows.append((current_start, current_end))
        current_start = current_end
    
    return windows


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
    HYBRID TEMPORAL PAGINATION for markets >= $200M
    
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
        last_timestamp = 0  # Timestamp-based pagination
        request_count = 0
        current_batch_size = INITIAL_BATCH_SIZE  # Start with 1000, adaptively reduce on errors
        batch_size_reduced = False  # Track if we've reduced batch size
        
        # Check market volume for special handling
        market_volume = float(market_info.get('volume', 0) or 0)
        use_temporal_windows = market_volume >= TEMPORAL_WINDOWING_THRESHOLD  # Uses temporal windowing
        is_very_large_market = market_volume >= TEMPORAL_WINDOWING_THRESHOLD  # Gets exclusive mode + special strategies
        
        # TEMPORAL WINDOWING setup for large markets
        if use_temporal_windows:
            # Get market date range from market_info
            # Default: if no dates, use 1 year window (more reasonable than epoch)
            current_time = int(time.time())
            market_start_time = current_time - (365 * 24 * 60 * 60)  # 1 year ago default
            market_end_time = current_time
            
            # DEBUG: Show what we got
            if market_volume >= 1_000_000_000:  # Only for very large markets
                print(f"      DEBUG market_info keys: {list(market_info.keys())}", flush=True)
                print(f"      DEBUG start_date: '{market_info.get('start_date')}'", flush=True)
                print(f"      DEBUG end_date: '{market_info.get('end_date')}'", flush=True)
                print(f"      DEBUG market_start_time timestamp: {market_start_time}", flush=True)
                print(f"      DEBUG market_end_time timestamp: {market_end_time}", flush=True)
            
            # Parse start date (ISO string to Unix timestamp)
            start_date_str = market_info.get('start_date')
            if start_date_str:
                try:
                    # Format: "2024-03-19T00:38:28.293Z" or "2024-03-19"
                    # Replace Z with +00:00 for Python 3.10 compatibility
                    if 'Z' in start_date_str:
                        start_date_str = start_date_str.replace('Z', '+00:00')
                    
                    if 'T' in start_date_str:
                        # ISO datetime with timezone
                        start_dt = datetime.fromisoformat(start_date_str)
                    else:
                        # Just date
                        start_dt = datetime.strptime(start_date_str[:10], '%Y-%m-%d')
                    
                    market_start_time = int(start_dt.timestamp())
                except Exception as e:
                    print(f"      ⚠️ Failed to parse start_date '{start_date_str}': {e}", flush=True)
            
            # Parse end date (ISO string to Unix timestamp)
            end_date_str = market_info.get('end_date')
            if end_date_str:
                try:
                    if 'Z' in end_date_str:
                        end_date_str = end_date_str.replace('Z', '+00:00')
                    
                    if 'T' in end_date_str:
                        end_dt = datetime.fromisoformat(end_date_str)
                    else:
                        end_dt = datetime.strptime(end_date_str[:10], '%Y-%m-%d')
                    
                    market_end_time = int(end_dt.timestamp())
                except Exception as e:
                    print(f"      ⚠️ Failed to parse end_date '{end_date_str}': {e}", flush=True)
            
            # CRITICAL: Extend market_end_time to current time
            # Redemptions happen AFTER market closes (people redeeming winning shares)
            # If we only use market's official end_date, we'll miss all redemptions!
            original_end_time = market_end_time
            market_end_time = max(market_end_time, current_time)  # Extend to now
            
            if market_end_time > original_end_time:
                from datetime import datetime as dt
                original_str = dt.fromtimestamp(original_end_time).strftime('%Y-%m-%d')
                extended_str = dt.fromtimestamp(market_end_time).strftime('%Y-%m-%d')
                print(f"      📅 Extended end date: {original_str} → {extended_str} (redemptions happen after close)", flush=True)
            
            # Sanity check: start should be before end
            if market_start_time >= market_end_time:
                print(f"      ⚠️ Invalid date range (start >= end), adjusting...", flush=True)
                market_start_time = market_end_time - (365 * 24 * 60 * 60)  # 1 year before end
            
            # Window size based on volume
            # STRATEGY: SMALLER windows for reliability (prevent timeouts)
            # All $300M+ markets use same conservative approach
            window_days = 7  # 7 days = MORE windows but NO timeouts
            
            # 🎯 CRITICAL FIX: Windows should start from market CLOSE time, not open time
            # Redemptions happen AFTER market closes, so no point checking windows before that
            time_windows = split_into_time_windows(original_end_time, market_end_time, window_days)
            
            # Show market date range (from close to now)
            close_str = datetime.fromtimestamp(original_end_time).strftime('%Y-%m-%d')
            now_str = datetime.fromtimestamp(market_end_time).strftime('%Y-%m-%d')
            print(f"      TEMPORAL WINDOWING: {len(time_windows)} windows ({window_days} days)", flush=True)
            print(f"      Redemption range: {close_str} (market close) to {now_str} (now)", flush=True)
        else:
            time_windows = [(0, int(time.time()))]
        
        # Pattern tracking for smart adaptation
        timeout_history = deque(maxlen=TIMEOUT_WINDOW_SIZE)  # Track last N requests: True=timeout, False=success
        retry_delay_multiplier = 1.0  # Start normal, can increase in critical situations
        last_request_duration = 0.0  # Track duration of last successful request for adaptive delay
        
        # Initialize status dict FIRST (before using it!)
        status = {
            'success': True,
            'complete': True,
            'error': None,
            'requests_made': 0,
            'adaptive_pagination_used': False
        }
        
        # 🎯 NEW: Smart delay + PROGRESSIVE BATCH SIZE for very large markets
        # Strategy: Start SMALL (50), increase on success, decrease on failure
        if is_very_large_market:
            # Delay levels: 2s → 4s → 6s → 8s → 10s → 15s → 20s
            delay_levels = [2.0, 4.0, 6.0, 8.0, 10.0, 15.0, 20.0]
            
            # 🎯 START WITH MODERATE DELAY for temporal windowing
            # STRATEGY: All $300M+ markets - balanced between speed and reliability
            current_delay_level = INITIAL_DELAY_LEVEL_LARGE_MARKETS  # Start at configured initial delay (default 4s)
            
            delay_increase_attempts = 0  # Track how many times we increased delay
            consecutive_successes = 0  # Track successful requests in a row
            consecutive_timeouts = 0  # Track consecutive timeouts for cooldown
            
            # 🔄 PROGRESSIVE BATCH SIZE: Start small, grow on success
            # STRATEGY: All $300M+ markets start conservative
            batch_size_levels = [25, 50, 100, 250, 500]  # Conservative for reliability
            
            current_batch_size = batch_size_levels[0]  # Start with smallest
            current_batch_level = 0  # Level 0
            batch_size_reduced = True  # Mark as "reduced" to trigger adaptive logic
            status['adaptive_pagination_used'] = True
            
            print(f"      🎯 Smart delay adaptation enabled (start: {delay_levels[current_delay_level]:.0f}s - balanced)", flush=True)
            print(f"      🔄 PROGRESSIVE BATCH SIZE: Starting with {current_batch_size} (will increase on success)", flush=True)
        
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
        
        # 🚀 SMART FIRST REQUEST: For temporal windowing, find first redemption quickly
        # Problem: Empty windows cause timeouts (PostgreSQL scans huge table)
        # Solution: LIMIT 1 probe to find where data starts
        first_redemption_timestamp = None
        if use_temporal_windows and len(time_windows) > 1:
            print(f"      🔍 Probing for first redemption (LIMIT 1)...", flush=True)
            probe_query = f"""
            query ($condId: String!, $timestampGte: BigInt!) {{
              redemptions(
                where: {{ condition: $condId, timestamp_gte: $timestampGte }}
                first: 1
                orderBy: timestamp
                orderDirection: asc
              ) {{
                timestamp
              }}
            }}
            """
            probe_variables = {
                "condId": condition_id,
                "timestampGte": str(time_windows[0][0])  # Market start time
            }
            
            try:
                # Use same pattern as main requests
                await rate_limiter.acquire()
                
                headers = {'Content-Type': 'application/json'}
                if GOLDSKY_API_TOKEN:
                    headers['Authorization'] = f'Bearer {GOLDSKY_API_TOKEN}'
                
                async with session.post(
                    GRAPH_URL,
                    json={'query': probe_query, 'variables': probe_variables},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)  # Shorter timeout for probe
                ) as response:
                    probe_data = await response.json()
                
                if probe_data and probe_data.get('data', {}).get('redemptions'):
                    probe_result = probe_data['data']['redemptions']
                    if probe_result:
                        first_redemption_timestamp = int(probe_result[0]['timestamp'])
                        from datetime import datetime as dt
                        first_dt = dt.fromtimestamp(first_redemption_timestamp).strftime('%Y-%m-%d %H:%M:%S')
                        print(f"      ✅ Found first redemption at: {first_dt}", flush=True)
                        print(f"         Will skip empty windows before this timestamp", flush=True)
                    else:
                        print(f"      ⚠️  No redemptions found for this market", flush=True)
                elif probe_data and probe_data.get('errors'):
                    # GraphQL timeout or other error
                    error_msg = str(probe_data.get('errors', []))
                    if 'timeout' in error_msg.lower():
                        print(f"      ⚠️  Probe timeout (expected for large empty market) - will process windows normally", flush=True)
                    else:
                        print(f"      ⚠️  Probe error: {error_msg[:100]}... - continuing with normal flow", flush=True)
                else:
                    print(f"      ⚠️  Probe returned no data - continuing with normal flow", flush=True)
            except asyncio.TimeoutError:
                print(f"      ⚠️  Probe timeout after 30s (expected for large market) - will process windows normally", flush=True)
            except Exception as e:
                print(f"      ⚠️  Probe failed: {e} - continuing with normal flow", flush=True)
        
        # TEMPORAL PAGINATION: Process each time window separately
        consecutive_empty_windows = 0  # Track empty windows to detect data end
        found_any_data = False  # Track if we've found ANY data yet (prevents premature exit)
        consecutive_skipped_windows = 0  # Track consecutive skips for delay reset
        probe_mode = False  # When True, use smart skip based on next_redemption_timestamp
        next_redemption_timestamp = None  # Timestamp of next redemption (from smart skip probe)
        
        for window_idx, (window_start, window_end) in enumerate(time_windows, 1):
            # Show window info
            if use_temporal_windows and len(time_windows) > 1:
                from datetime import datetime as dt
                start_dt = dt.fromtimestamp(window_start).strftime('%Y-%m-%d')
                end_dt = dt.fromtimestamp(window_end).strftime('%Y-%m-%d')
                print(f"\n      Window {window_idx}/{len(time_windows)}: {start_dt} -> {end_dt}", flush=True)
            
            # Initialize cursor for this window
            # Use max(window_start, last_timestamp) to skip empty windows
            # If previous window completed with all redemptions filtered, last_timestamp = window_end
            last_timestamp = max(window_start, last_timestamp)
            
            # Skip window entirely if cursor is already past window_end
            if last_timestamp >= window_end:
                if use_temporal_windows and len(time_windows) > 1:
                    print(f"      ⏭️  Skipping window (cursor already at {last_timestamp} >= {window_end})", flush=True)
                consecutive_skipped_windows += 1
                continue  # Skip to next window
            
            # RESET delay level for first non-skipped window after many skips
            # This handles: Window 1 timeout → delay 20s → SKIP AHEAD → Window 44 should start fresh at configured initial delay
            if is_very_large_market and consecutive_skipped_windows >= 5 and current_delay_level > INITIAL_DELAY_LEVEL_LARGE_MARKETS:
                old_delay_level = current_delay_level
                current_delay_level = INITIAL_DELAY_LEVEL_LARGE_MARKETS  # Reset to initial delay
                print(f"      🔄 Reset delay after {consecutive_skipped_windows} skips: level {old_delay_level+1} → {current_delay_level+1} ({delay_levels[old_delay_level]:.0f}s → {delay_levels[current_delay_level]:.0f}s)", flush=True)
            
            # Reset skip counter when we process a window
            consecutive_skipped_windows = 0
            
            # 🚀 SMART SKIP: If we probed and found first redemption, skip windows before it
            if first_redemption_timestamp and window_end < first_redemption_timestamp:
                if use_temporal_windows and len(time_windows) > 1:
                    from datetime import datetime as dt
                    end_dt = dt.fromtimestamp(window_end).strftime('%Y-%m-%d')
                    first_dt = dt.fromtimestamp(first_redemption_timestamp).strftime('%Y-%m-%d')
                    print(f"      ⏭️  Smart skip: Window ends {end_dt}, data starts {first_dt}", flush=True)
                continue  # Skip to next window
            
            # 🚀 SMART SKIP (PROBE MODE): If we probed and found next redemption, skip windows before it
            if next_redemption_timestamp and window_end < next_redemption_timestamp:
                if use_temporal_windows and len(time_windows) > 1:
                    from datetime import datetime as dt
                    end_dt = dt.fromtimestamp(window_end).strftime('%Y-%m-%d')
                    next_dt = dt.fromtimestamp(next_redemption_timestamp).strftime('%Y-%m-%d')
                    print(f"      ⏭️  Smart skip: Window ends {end_dt}, next data at {next_dt}", flush=True)
                continue  # Skip to next window
            
            window_redemptions_count = 0
            window_complete = False  # Track if window reached its end
            
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
            
                # Build query with timestamp-based pagination
                # NOTE: GoldSky doesn't support timestamp_lt properly (returns 0 results)
                # Solution: Use only timestamp_gte + break early when >= window_end
                if use_temporal_windows and len(time_windows) > 1:
                    # NOTE: Can't use timestamp_lt - GoldSky returns 0 results
                    # Use only timestamp_gte and break manually when >= window_end
                    query = f"""
                    query ($condId: String!, $timestampGte: BigInt!) {{
                      redemptions(
                        where: {{ condition: $condId, timestamp_gte: $timestampGte }}
                        first: {current_batch_size}
                        orderBy: timestamp
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
                        "timestampGte": str(last_timestamp)
                    }
                    
                    # DEBUG: Show window strategy for first request
                    if request_count == 1 and window_idx == 1:
                        from datetime import datetime as dt
                        ts_gte_dt = dt.fromtimestamp(last_timestamp).strftime('%Y-%m-%d %H:%M:%S')
                        ts_end_dt = dt.fromtimestamp(window_end).strftime('%Y-%m-%d %H:%M:%S')
                        print(f"      DEBUG Window strategy:", flush=True)
                        print(f"        timestamp_gte: {last_timestamp} ({ts_gte_dt})", flush=True)
                        print(f"        window_end: {window_end} ({ts_end_dt})", flush=True)
                        print(f"        Will break when timestamp >= {window_end}", flush=True)
                else:
                    # Without temporal windowing: only lower bound
                    query = f"""
                    query ($condId: String!, $timestampGte: BigInt!) {{
                      redemptions(
                        where: {{ condition: $condId, timestamp_gte: $timestampGte }}
                        first: {current_batch_size}
                        orderBy: timestamp
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
                        "timestampGte": str(last_timestamp)
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
                    
                        # Add API token if available
                        headers = {'Content-Type': 'application/json'}
                        if GOLDSKY_API_TOKEN:
                            headers['Authorization'] = f'Bearer {GOLDSKY_API_TOKEN}'
                    
                        async with session.post(
                            GRAPH_URL,
                            json={'query': query, 'variables': variables},
                            headers=headers,
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
                                
                                    # 🎯 AGGRESSIVE BATCH REDUCTION for large markets (>=$200M)
                                    if is_very_large_market:
                                        # NEW STRATEGY: Reduce batch EARLY (after 3 timeouts), then increase delay
                                    
                                        # Reset consecutive successes (we had a failure)
                                        consecutive_successes = 0
                                        consecutive_timeouts += 1
                                    
                                        # Check for cooldown after 3+ consecutive timeouts
                                        if consecutive_timeouts >= 3:
                                            cooldown_time = min(10.0 + (consecutive_timeouts - 3) * 2.0, 20.0)
                                            print(f"\n      🚨 COOLDOWN: {consecutive_timeouts} consecutive timeouts detected!")
                                            print(f"         💤 Giving PostgreSQL {cooldown_time:.0f}s to recover...", flush=True)
                                            await asyncio.sleep(cooldown_time)
                                    
                                        # PRIORITY: Reduce batch size after 3+ consecutive timeouts
                                        should_reduce_batch = (consecutive_timeouts >= 3 and current_batch_level > 0)
                                        
                                        # Check if we can increase delay (only if batch at minimum)
                                        can_increase_delay = (current_delay_level < len(delay_levels) - 1 and 
                                                             delay_increase_attempts < 10 and
                                                             current_batch_level == 0)
                                    
                                        if should_reduce_batch:
                                            # PRIORITY: Reduce batch size FIRST
                                            old_batch_level = current_batch_level
                                            old_batch_size = current_batch_size
                                            current_batch_level -= 1
                                            current_batch_size = batch_size_levels[current_batch_level]
                                            
                                            print(f"\n      ⚠️  GraphQL Timeout for {condition_id[:20]}... (attempt {total_attempts}/{IMMEDIATE_RETRIES + 1})")
                                            print(f"         Error: {error_msg[:150]}")
                                            print(f"         📉 BATCH REDUCTION: {consecutive_timeouts} timeouts, reducing {old_batch_size} → {current_batch_size}")
                                            print(f"         🔄 Retrying with smaller batch...", flush=True)
                                            
                                            batch_size_reduced = True
                                            status['adaptive_pagination_used'] = True
                                            consecutive_timeouts = 0  # Reset counter
                                            
                                            retry_pause = delay_levels[min(current_delay_level + 1, len(delay_levels) - 1)]
                                            await asyncio.sleep(retry_pause)
                                            continue
                                        elif can_increase_delay:
                                            # Increase delay level instead of reducing batch size
                                            old_level = current_delay_level
                                            current_delay_level += 1
                                            delay_increase_attempts += 1
                                        
                                            print(f"\n      ⚠️  GraphQL Timeout for {condition_id[:20]}... (attempt {total_attempts}/{IMMEDIATE_RETRIES + 1})")
                                            print(f"         Error: {error_msg[:150]}")
                                            print(f"         📈 SMART DELAY: Increasing delay level {old_level+1} → {current_delay_level+1} ({delay_levels[old_level]:.0f}s → {delay_levels[current_delay_level]:.0f}s)")
                                            print(f"         📊 Delay increase attempts: {delay_increase_attempts}/10 (batch size unchanged: {current_batch_size})")
                                            print(f"         🔄 Retrying with longer delay...", flush=True)
                                        
                                            # Use current delay level for retry pause (not fixed 2s!)
                                            retry_pause = delay_levels[current_delay_level]
                                            await asyncio.sleep(retry_pause)
                                            continue  # Retry with increased delay
                                        else:
                                            # Exhausted delay increases (10 attempts) - now reduce batch size
                                            if current_batch_level > 0:  # Can decrease batch level
                                                old_batch_level = current_batch_level
                                                old_batch_size = current_batch_size
                                                current_batch_level -= 1  # Decrease by 1 level
                                                current_batch_size = batch_size_levels[current_batch_level]
                                            
                                                # Partially reduce delay level (not reset to 0!)
                                                # Keep some delay advantage we earned
                                                old_delay_level = current_delay_level
                                                current_delay_level = max(current_delay_level - 2, 0)  # Drop by 2 levels, minimum 0
                                            
                                                print(f"\n      ⚠️  GraphQL Timeout for {condition_id[:20]}... (attempt {total_attempts}/{IMMEDIATE_RETRIES + 1})")
                                                print(f"         Error: {error_msg[:150]}")
                                                print(f"         📉 BATCH REDUCTION: Max delay reached, reducing batch level {old_batch_level+1} → {current_batch_level+1} ({old_batch_size} → {current_batch_size})")
                                                print(f"         📊 Delay increases exhausted (10/10), max delay level: {old_delay_level+1}/{len(delay_levels)}")
                                                print(f"         📊 PARTIAL DELAY REDUCTION: level {old_delay_level+1} → {current_delay_level+1} ({delay_levels[old_delay_level]:.0f}s → {delay_levels[current_delay_level]:.0f}s)")
                                                print(f"         🔄 Retrying with smaller batch...", flush=True)
                                            
                                                batch_size_reduced = True
                                                status['adaptive_pagination_used'] = True
                                            
                                                # Reset delay adaptation counters for new batch size
                                                delay_increase_attempts = 0
                                                consecutive_successes = 0  # Reset success counter
                                                # NOTE: current_delay_level is NOT reset - we keep some delay!
                                            
                                                # Use current delay level for retry pause
                                                retry_pause = delay_levels[current_delay_level]
                                                await asyncio.sleep(retry_pause)
                                                continue  # Retry with new batch size
                                            else:
                                                # Can't reduce batch level further (at level 0 = 50) - use exponential backoff with current delay level
                                                base_delay = INITIAL_RETRY_DELAY * (2 ** (graphql_error_retries - 1))
                                                exponential_delay = min(base_delay, MAX_RETRY_DELAY)
                                            
                                                # Combine exponential backoff with current delay level
                                                # Use the MAXIMUM of the two to ensure sufficient pause
                                                current_level_delay = delay_levels[current_delay_level]
                                                retry_delay = max(exponential_delay, current_level_delay)
                                            
                                                print(f"\n      ⚠️  GraphQL Timeout for {condition_id[:20]}... (attempt {total_attempts}/{IMMEDIATE_RETRIES + 1})")
                                                print(f"         Error: {error_msg[:150]}")
                                                print(f"         Current batch level: {current_batch_level+1}/{len(batch_size_levels)} ({current_batch_size} - minimum)")
                                                print(f"         Current delay level: {current_delay_level+1}/{len(delay_levels)} ({current_level_delay:.0f}s)")
                                                print(f"         🔄 Retrying in {retry_delay:.0f}s (max of exponential {exponential_delay:.0f}s and level {current_level_delay:.0f}s)...", flush=True)
                                            
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
                            
                            # DEBUG: Show batch info for first few requests
                            if request_count <= 3 and use_temporal_windows and len(time_windows) > 1:
                                print(f"      DEBUG Request #{request_count}: received {len(batch)} redemptions", flush=True)
                                if batch:
                                    first_ts = int(batch[0]['timestamp'])
                                    last_ts = int(batch[-1]['timestamp'])
                                    from datetime import datetime as dt
                                    print(f"        First: {first_ts} ({dt.fromtimestamp(first_ts).strftime('%Y-%m-%d %H:%M:%S')})", flush=True)
                                    print(f"        Last: {last_ts} ({dt.fromtimestamp(last_ts).strftime('%Y-%m-%d %H:%M:%S')})", flush=True)
                                    print(f"        Window end: {window_end} ({dt.fromtimestamp(window_end).strftime('%Y-%m-%d %H:%M:%S')})", flush=True)
                            
                            # 🎯 TEMPORAL WINDOWING: Check if we reached window_end
                            # Since GoldSky doesn't support timestamp_lt, we break manually
                            window_complete = False
                            skip_ahead = False  # Flag to skip multiple empty windows
                            if use_temporal_windows and len(time_windows) > 1 and batch:
                                # Store first timestamp before filtering (for skip-ahead optimization)
                                first_redemption_ts = int(batch[0]['timestamp'])
                                
                                # Check if any redemptions are beyond window_end
                                filtered_batch = []
                                for redemption in batch:
                                    redemption_ts = int(redemption['timestamp'])
                                    if redemption_ts >= window_end:
                                        # Reached end of window
                                        window_complete = True
                                        break
                                    filtered_batch.append(redemption)
                                
                                # If we filtered something out, batch is now smaller
                                if len(filtered_batch) < len(batch):
                                    original_count = len(batch)
                                    batch = filtered_batch
                                    filtered_count = original_count - len(batch)
                                    print(f"      DEBUG: Filtered out {filtered_count}/{original_count} redemptions (beyond window_end)", flush=True)
                                    
                                    # If all were filtered, window is complete with no new data
                                    if len(batch) == 0:
                                        window_complete = True
                                        print(f"      DEBUG: All redemptions filtered - window complete!", flush=True)
                                        
                                        # 🚀 SKIP AHEAD OPTIMIZATION: If first redemption is WAY AHEAD of window_end
                                        # We can skip multiple empty windows by jumping cursor forward
                                        time_gap = first_redemption_ts - window_end
                                        days_gap = time_gap / (24 * 60 * 60)
                                        
                                        # If gap is more than 2 window sizes, worth skipping ahead
                                        if days_gap > (window_days * 2):
                                            from datetime import datetime as dt
                                            gap_str = dt.fromtimestamp(first_redemption_ts).strftime('%Y-%m-%d')
                                            print(f"      ⚡ SKIP AHEAD: First redemption at {gap_str} ({days_gap:.0f} days ahead)", flush=True)
                                            print(f"         Jumping cursor forward to skip empty windows", flush=True)
                                            # Set cursor to first redemption timestamp
                                            # This will cause next windows to be skipped via the skip logic
                                            last_timestamp = first_redemption_ts
                                            skip_ahead = True
                                            
                                            # RESET delay level after skip ahead - we're starting fresh!
                                            if is_very_large_market:
                                                old_delay_level = current_delay_level
                                                # Reset to initial level
                                                current_delay_level = INITIAL_DELAY_LEVEL_LARGE_MARKETS
                                                print(f"      🔄 Reset delay: level {old_delay_level+1} → {current_delay_level+1} ({delay_levels[old_delay_level]:.0f}s → {delay_levels[current_delay_level]:.0f}s)", flush=True)
                        
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
                                consecutive_timeouts = 0  # Reset consecutive timeouts on success
                            
                                # 🔄 PROGRESSIVE BATCH SIZE: Increase batch size after 3+ successes
                                if consecutive_successes >= 3 and current_batch_level < len(batch_size_levels) - 1:
                                    old_batch_level = current_batch_level
                                    old_batch_size = current_batch_size
                                    current_batch_level += 1
                                    current_batch_size = batch_size_levels[current_batch_level]
                                    consecutive_successes = 0  # Reset counter
                                
                                    print(f"      📈 3+ successes! Increasing batch size level {old_batch_level+1} → {current_batch_level+1} ({old_batch_size} → {current_batch_size})", flush=True)
                            
                                # After 5+ consecutive successes, decrease delay level (if possible)
                                elif consecutive_successes >= 5 and current_delay_level > 0:
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
                        
                            # Check if window is complete (for temporal windowing)
                            if window_complete:
                                if use_temporal_windows and len(time_windows) > 1:
                                    print(f"      ✅ Window {window_idx}/{len(time_windows)} complete - reached window_end ({window_redemptions_count} redemptions)", flush=True)
                                    
                                    # Update cursor (skip_ahead already updated it to first_redemption_ts)
                                    if not skip_ahead:
                                        # Normal case: update cursor to window_end
                                        from datetime import datetime as dt
                                        old_cursor = last_timestamp
                                        last_timestamp = window_end
                                        print(f"      DEBUG: Cursor updated: {old_cursor} → {last_timestamp} ({dt.fromtimestamp(last_timestamp).strftime('%Y-%m-%d %H:%M:%S')})", flush=True)
                                    # else: cursor already updated to first_redemption_ts in skip_ahead logic
                                break
                            
                            if not batch:
                                # Show final stats for very large markets with progressive batch size
                                if is_very_large_market and len(all_redemptions) > 0:
                                    print(f"      ℹ️  Completed! Final batch level: {current_batch_level+1}/{len(batch_size_levels)} (size: {current_batch_size})", flush=True)
                                    if current_batch_level > 0:
                                        print(f"      📈 Successfully scaled up from {batch_size_levels[0]} to {current_batch_size}!", flush=True)
                                # Show final stats if batch size was reduced for regular markets
                                elif batch_size_reduced and len(all_redemptions) > 0:
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
                        
                            # Update cursor with timestamp
                            last_timestamp = int(batch[-1]['timestamp']) + 1
                            window_redemptions_count += len(batch)
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
                    
                        # Track consecutive timeouts for very large markets
                        if is_very_large_market:
                            consecutive_timeouts += 1
                            consecutive_successes = 0
                        
                            # Cooldown after 3+ consecutive timeouts
                            if consecutive_timeouts >= 3:
                                cooldown_time = min(10.0 + (consecutive_timeouts - 3) * 2.0, 20.0)
                                print(f"\n      🚨 COOLDOWN: {consecutive_timeouts} consecutive timeouts detected!")
                                print(f"         💤 Giving PostgreSQL {cooldown_time:.0f}s to recover...", flush=True)
                                await asyncio.sleep(cooldown_time)
                    
                        # Apply dynamic retry delay multiplier
                        base_delay = INITIAL_RETRY_DELAY * (2 ** (retry_count - 1))
                        retry_delay = min(base_delay * retry_delay_multiplier, MAX_RETRY_DELAY)
                    
                        # For very large markets, use at least current delay level
                        if is_very_large_market:
                            current_level_delay = delay_levels[current_delay_level]
                            retry_delay = max(retry_delay, current_level_delay)
                    
                        print(f"\n      ⚠️  Request timeout (attempt {total_attempts}/{IMMEDIATE_RETRIES + 1})")
                        print(f"         Condition: {condition_id[:30]}...")
                        print(f"         Request: {request_count}, Last timestamp: {last_timestamp}")
                    
                        if total_attempts > IMMEDIATE_RETRIES:
                            print(f"      ❌ Failed after {total_attempts - 1} total attempts - giving up")
                            status['success'] = False
                            status['complete'] = False
                            status['error'] = f"Timeout after {total_attempts - 1} attempts at request {request_count}"
                            status['requests_made'] = request_count
                            break
                    
                        if is_very_large_market:
                            print(f"         Current delay level: {current_delay_level+1}/{len(delay_levels)} ({delay_levels[current_delay_level]:.0f}s)")
                    
                        if retry_delay_multiplier > 1.0:
                            print(f"         🔄 Retrying in {retry_delay:.0f}s (×{retry_delay_multiplier:.1f} critical backoff)...", flush=True)
                        else:
                            print(f"         🔄 Retrying in {retry_delay:.0f}s (exponential backoff)...", flush=True)
                        await asyncio.sleep(retry_delay)
                    except Exception as e:
                        retry_count += 1
                        timeout_history.append(True)  # Record error as timeout-equivalent
                        
                        error_type = type(e).__name__
                        error_msg = str(e)
                        
                        # CRITICAL: Check for "Session is closed" error
                        if 'session' in error_msg.lower() and 'closed' in error_msg.lower():
                            print(f"\n      ❌ CRITICAL: Session closed - cannot retry")
                            print(f"         Error: {error_msg[:150]}")
                            print(f"         💡 This window needs new session/run")
                            status['success'] = False
                            status['complete'] = False
                            status['error'] = f"Session closed: {error_msg[:200]}"
                            status['requests_made'] = request_count
                            break
                    
                        # Track consecutive timeouts for very large markets
                        if is_very_large_market:
                            consecutive_timeouts += 1
                            consecutive_successes = 0
                        
                            # Cooldown after 3+ consecutive timeouts
                            if consecutive_timeouts >= 3:
                                cooldown_time = min(10.0 + (consecutive_timeouts - 3) * 2.0, 20.0)
                                print(f"\n      🚨 COOLDOWN: {consecutive_timeouts} consecutive errors detected!")
                                print(f"         💤 Giving PostgreSQL {cooldown_time:.0f}s to recover...", flush=True)
                                await asyncio.sleep(cooldown_time)
                    
                        # Apply dynamic retry delay multiplier
                        base_delay = INITIAL_RETRY_DELAY * (2 ** (retry_count - 1))
                        retry_delay = min(base_delay * retry_delay_multiplier, MAX_RETRY_DELAY)
                    
                        # For very large markets, use at least current delay level
                        if is_very_large_market:
                            current_level_delay = delay_levels[current_delay_level]
                            retry_delay = max(retry_delay, current_level_delay)
                    
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
                    
                        if is_very_large_market:
                            print(f"         Current delay level: {current_delay_level+1}/{len(delay_levels)} ({delay_levels[current_delay_level]:.0f}s)")
                    
                        if retry_delay_multiplier > 1.0:
                            print(f"         🔄 Retrying in {retry_delay:.0f}s (×{retry_delay_multiplier:.1f} critical backoff)...", flush=True)
                        else:
                            print(f"         🔄 Retrying in {retry_delay:.0f}s (exponential backoff)...", flush=True)
                        await asyncio.sleep(retry_delay)
            
                # If all retries failed, break the pagination loop
                if not request_success:
                    break
            
            # Show window completion stats
            if use_temporal_windows and len(time_windows) > 1:
                print(f"      Window {window_idx}/{len(time_windows)} complete: {window_redemptions_count:,} redemptions", flush=True)
                
                # Track empty windows for early exit / probe mode
                if window_redemptions_count == 0:
                    consecutive_empty_windows += 1
                    # Switch to SMART SKIP after threshold (like first probe!)
                    if consecutive_empty_windows >= EARLY_EXIT_THRESHOLD and found_any_data and not probe_mode:
                        remaining_windows = len(time_windows) - window_idx
                        if PROBE_MODE_ENABLED:
                            # Do ONE probe for ALL remaining windows (like first probe!)
                            print(f"      🔍 SMART SKIP: {consecutive_empty_windows} consecutive empty windows", flush=True)
                            print(f"         Probing for next redemption in remaining {remaining_windows} windows...", flush=True)
                            
                            try:
                                # ONE probe query for all remaining windows
                                probe_query = """
                                query ProbeNext($condId: String!, $timestampGte: BigInt!) {
                                    redemptions(
                                        where: { 
                                            condition: $condId,
                                            timestamp_gte: $timestampGte
                                        }
                                        first: 1
                                        orderBy: timestamp
                                        orderDirection: asc
                                    ) {
                                        timestamp
                                    }
                                }
                                """
                                
                                probe_variables = {
                                    'condId': condition_id,
                                    'timestampGte': str(last_timestamp)
                                }
                                
                                await rate_limiter.acquire()
                                
                                async with session.post(
                                    GRAPH_URL,
                                    json={'query': probe_query, 'variables': probe_variables},
                                    headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=30)
                                ) as probe_response:
                                    probe_data = await probe_response.json()
                                    
                                    if 'data' in probe_data and probe_data['data'].get('redemptions'):
                                        probe_redemptions = probe_data['data']['redemptions']
                                        if probe_redemptions:
                                            next_redemption_ts = int(probe_redemptions[0]['timestamp'])
                                            from datetime import datetime as dt
                                            next_dt = dt.fromtimestamp(next_redemption_ts).strftime('%Y-%m-%d %H:%M:%S')
                                            print(f"      ✅ Found next redemption at: {next_dt}", flush=True)
                                            print(f"         Will skip windows before this timestamp", flush=True)
                                            
                                            # Set probe mode with skip timestamp (like first probe!)
                                            probe_mode = True
                                            next_redemption_timestamp = next_redemption_ts
                                        else:
                                            print(f"      ⚠️  No more redemptions found - stopping", flush=True)
                                            break  # Exit - no more data
                                    else:
                                        print(f"      ⚠️  No more redemptions found - stopping", flush=True)
                                        break  # Exit - no more data
                                        
                            except asyncio.TimeoutError:
                                print(f"      ⚠️  Probe timeout - will try processing remaining windows normally", flush=True)
                                probe_mode = True  # Fallback to per-window probe
                            except Exception as e:
                                print(f"      ⚠️  Probe failed: {e} - will try processing remaining windows normally", flush=True)
                                probe_mode = True  # Fallback to per-window probe
                        else:
                            print(f"      🛑 EARLY EXIT: {consecutive_empty_windows} consecutive empty windows detected", flush=True)
                            print(f"         Skipping remaining {remaining_windows} windows (no more data expected)", flush=True)
                            break  # Exit the window loop - no more data
                else:
                    # Reset counter when we find data
                    consecutive_empty_windows = 0
                    # Exit probe mode if we found data again
                    if probe_mode:
                        print(f"      ✅ Data found! Resuming normal processing", flush=True)
                        probe_mode = False
                        next_redemption_timestamp = None
                    
                    # First window with data - reset delay level!
                    if not found_any_data and is_very_large_market:
                        old_delay_level = current_delay_level
                        # Reset to initial level
                        current_delay_level = INITIAL_DELAY_LEVEL_LARGE_MARKETS
                        if old_delay_level != current_delay_level:
                            print(f"      🔄 First data found! Reset delay: level {old_delay_level+1} → {current_delay_level+1} ({delay_levels[old_delay_level]:.0f}s → {delay_levels[current_delay_level]:.0f}s)", flush=True)
                    
                    found_any_data = True  # Mark that we've found at least some data
                
                # ADAPTIVE COOLDOWN between windows
                # STRATEGY: Fast through empty windows, slow through data windows
                if is_very_large_market and window_idx < len(time_windows):
                    if window_redemptions_count > 0:
                        # Window has data - use standard cooldown for PostgreSQL recovery
                        cooldown_time = 3.0
                        print(f"      💤 Window cooldown: {cooldown_time:.0f}s (PostgreSQL recovery time)", flush=True)
                    elif not found_any_data:
                        # Empty window before finding any data - fast skip to next
                        cooldown_time = 0.5
                        print(f"      ⚡ Fast skip: {cooldown_time}s (empty window, searching for data)", flush=True)
                    else:
                        # Empty window after data found - no cooldown needed (EARLY EXIT will trigger soon)
                        cooldown_time = 0
                    
                    if cooldown_time > 0:
                        await asyncio.sleep(cooldown_time)
            
            # If critical error, don't continue to next window
            if not status['complete'] or not status['success']:
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
        needs_exclusive_access = market_volume >= EXCLUSIVE_ACCESS_THRESHOLD  # Needs exclusive processing
        
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
