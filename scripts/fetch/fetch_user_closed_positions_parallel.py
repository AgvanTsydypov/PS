"""
Parallel Fetch Closed Positions for Redeemers from Polymarket API
Uses parallel processing with rate limiting and connection pooling

БЫСТРЫЙ ЗАПУСК:
===============
1. Preview mode с параллельной обработкой (5 workers):
   python fetch_user_closed_positions_parallel.py --local

2. Полная загрузка в PostgreSQL (рекомендуется):
   python fetch_user_closed_positions_parallel.py --upload --local

3. Тест на нескольких пользователях:
   python fetch_user_closed_positions_parallel.py --local --limit 10 --verbose

4. Настройка параллелизма (больше workers = быстрее, но риск rate limit):
   python fetch_user_closed_positions_parallel.py --upload --local --workers 10

5. Больше позиций на пользователя:
   python fetch_user_closed_positions_parallel.py --upload --local --positions 1000

6. Фильтрация по конкретным рынкам (conditionIds):
   python fetch_user_closed_positions_parallel.py --local --market 0xabc123... 0xdef456...

7. Фильтрация по событиям (event IDs):
   python fetch_user_closed_positions_parallel.py --local --event-id 12345 67890

8. Фильтрация по названию рынка:
   python fetch_user_closed_positions_parallel.py --local --title "Trump"

9. Низкое потребление памяти (для слабых компьютеров, батчи по 5000 записей):
   python fetch_user_closed_positions_parallel.py --upload --local --db-batch 5000

ТРЕБОВАНИЯ:
===========
- Python 3.8+
- pip install -r requirements.txt
- Файл .env с настройками (для БД)
- База данных с таблицей redemptions и events

API LIMITS:
===========
- Closed Positions API: 150 requests / 10 seconds
- Optimized limit: 145 requests / 10 seconds (~97% of max)
- Automatic rate limiting and retry on failures

КЛЮЧЕВАЯ ОСОБЕННОСТЬ:
=====================
Скрипт использует SQL запрос из lowest_XXXm_event_redeemers.sql, который возвращает
пары (пользователь, рынок). API вызовы автоматически фильтруются по конкретному
рынку для каждого пользователя, делая запросы более точными и эффективными.

Features:
- ⚡ Параллельная обработка user-market пар (5 workers по умолчанию)
- 🧠 Memory-efficient batch processing (server-side cursor для больших датасетов)
- 🎯 Автоматическая фильтрация по рынкам из SQL запроса
- 🔄 Автоматический rate limiting (145 req/10s optimized limit)
- 🔁 Connection pooling для эффективного использования соединений
- 🔄 Автоматический retry при ошибках API
- 📦 Batch загрузка в БД (каждые 100 записей)
- 📊 Real-time прогресс и статистика
- 💾 Поддержка Supabase и локальной PostgreSQL
- 💻 Работает на слабых компьютерах (низкое потребление RAM)
"""

import requests
import json
import time
import os
import sys
import io
import argparse
from datetime import datetime
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from collections import deque

# Fix encoding for Windows console
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Add project root to Python path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Import the existing database uploader
from scripts.db.supabase_uploader import SupabaseUploader


# ==========================================
# CONFIGURATION
# ==========================================

API_BASE_URL = "https://data-api.polymarket.com/v1"
DEFAULT_WORKERS = 5  # Conservative default (API limit: 150/10s)
DEFAULT_POSITIONS_PER_USER = 50
BATCH_SIZE = 100  # How many records to upload to DB at once
DB_FETCH_BATCH_SIZE = 10000  # How many redeemer records to fetch from DB at once (memory optimization)

# Rate limiting: 150 req/10s, use 145 req/10s for higher speed
RATE_LIMIT_MAX = 150
RATE_LIMIT_WINDOW = 10
RATE_LIMIT_SAFE = 145  # ~97% of max, optimized for speed


# ==========================================
# RATE LIMITER
# ==========================================

class RateLimiter:
    """
    Thread-safe rate limiter using sliding window algorithm
    Ensures we don't exceed API rate limits
    """
    
    def __init__(self, max_requests: int = RATE_LIMIT_SAFE, window_seconds: int = RATE_LIMIT_WINDOW):
        """
        Initialize rate limiter
        
        Args:
            max_requests: Maximum requests allowed per window (defaults to RATE_LIMIT_SAFE)
            window_seconds: Time window in seconds
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = deque()  # Store timestamps of requests
        self.lock = Lock()
        
        # Use the provided limit (already optimized)
        self.safe_limit = max_requests
        
    def wait_if_needed(self):
        """
        Wait if necessary to respect rate limit
        Thread-safe with proper blocking
        """
        while True:
            with self.lock:
                now = time.time()
                
                # Remove requests older than window
                while self.requests and self.requests[0] < now - self.window_seconds:
                    self.requests.popleft()
                
                # If we have capacity, record and proceed
                if len(self.requests) < self.safe_limit:
                    self.requests.append(now)
                    return
                
                # Calculate how long to wait
                oldest_request = self.requests[0]
                sleep_time = (oldest_request + self.window_seconds - now) + 0.1
            
            # Wait outside the lock (so other threads can proceed)
            if sleep_time > 0:
                time.sleep(sleep_time)
    
    def get_current_rate(self) -> int:
        """Get current number of requests in window"""
        with self.lock:
            now = time.time()
            while self.requests and self.requests[0] < now - self.window_seconds:
                self.requests.popleft()
            return len(self.requests)


# ==========================================
# SHARED API CLIENT
# ==========================================

class SharedAPIClient:
    """Shared API client with connection pooling and rate limiting"""
    
    def __init__(self, max_pool_connections: int = 20, rate_limiter: Optional[RateLimiter] = None):
        """
        Initialize with connection pooling and rate limiting
        
        Args:
            max_pool_connections: Max connections in pool
            rate_limiter: Rate limiter instance
        """
        self.session = requests.Session()
        self.rate_limiter = rate_limiter
        
        # Configure connection pooling
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=max_pool_connections,
            pool_maxsize=max_pool_connections,
            max_retries=3,
            pool_block=False
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        
        self.session.headers.update({
            'Content-Type': 'application/json',
        })
    
    def fetch_closed_positions(
        self,
        user_address: str,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "REALIZEDPNL",
        sort_direction: str = "DESC",
        market: Optional[List[str]] = None,
        title: Optional[str] = None,
        event_id: Optional[List[int]] = None
    ) -> tuple[Optional[List[Dict]], Optional[str]]:
        """
        Fetch closed positions for a user (thread-safe with rate limiting)
        
        Args:
            user_address: The address of the user (required)
            limit: The max number of positions to return (max 50)
            offset: The starting index for pagination
            sort_by: The sort criteria (REALIZEDPNL, TITLE, PRICE, AVGPRICE, TIMESTAMP)
            sort_direction: The sort direction (ASC, DESC)
            market: List of conditionIds to filter by (cannot be used with event_id)
            title: Filter by market title
            event_id: List of event IDs to filter by (cannot be used with market)
        
        Returns:
            tuple: (positions_list, error_message)
                - ([], None) = Empty result (valid, no more data)
                - ([...], None) = Success with data
                - (None, error) = Failed request
        """
        try:
            # Wait if necessary to respect rate limit
            if self.rate_limiter:
                self.rate_limiter.wait_if_needed()
            
            params = {
                'user': user_address,
                'limit': min(limit, 50),  # API max is 50
                'offset': offset,
                'sortBy': sort_by,
                'sortDirection': sort_direction
            }
            
            # Add optional filter parameters
            if market and event_id:
                # Cannot use both market and event_id according to API docs
                return (None, "Cannot use both 'market' and 'event_id' parameters together")
            
            if market:
                # Convert list to comma-separated string
                params['market'] = ','.join(market)
            
            if title:
                params['title'] = title[:100]  # API max length is 100
            
            if event_id:
                # Convert list to comma-separated string
                params['eventId'] = ','.join(map(str, event_id))
            
            url = f"{API_BASE_URL}/closed-positions"
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            # Return empty list if no data (valid response)
            if not data:
                return ([], None)
            
            return (data, None)
            
        except Exception as e:
            # Return None with error message for failed requests
            return (None, str(e))
    
    def close(self):
        """Close the session"""
        self.session.close()


# ==========================================
# DATA TRANSFORMATION
# ==========================================

def transform_closed_position(position: Dict) -> Dict:
    """
    Transform API response to match database schema
    """
    # Parse timestamp
    timestamp_unix = position.get('timestamp', 0)
    timestamp_human = None
    if timestamp_unix:
        try:
            timestamp_human = datetime.fromtimestamp(timestamp_unix).isoformat()
        except:
            pass
    
    # Parse end_date
    end_date = position.get('endDate')
    end_date_parsed = None
    if end_date:
        try:
            # Try parsing ISO format
            end_date_parsed = datetime.fromisoformat(end_date.replace('Z', '+00:00')).isoformat()
        except:
            pass
    
    return {
        'proxy_wallet': position.get('proxyWallet', ''),
        'event_id': None,  # Will be populated later via JOIN with events table
        'market_id': None,  # Will be populated later via JOIN with markets table
        'condition_id': position.get('conditionId'),
        'asset': position.get('asset'),
        'avg_price': float(position.get('avgPrice', 0)),
        'total_bought': float(position.get('totalBought', 0)),
        'realized_pnl': float(position.get('realizedPnl', 0)),
        'cur_price': float(position.get('curPrice', 0)),
        'timestamp_unix': timestamp_unix,
        'timestamp_human': timestamp_human,
        'title': position.get('title'),
        'slug': position.get('slug'),
        'icon': position.get('icon'),
        'event_slug': position.get('eventSlug'),
        'outcome': position.get('outcome'),
        'outcome_index': position.get('outcomeIndex'),
        'opposite_outcome': position.get('oppositeOutcome'),
        'opposite_asset': position.get('oppositeAsset'),
        'end_date': end_date,
        'end_date_parsed': end_date_parsed
    }


# ==========================================
# DATABASE FUNCTIONS
# ==========================================

def get_redeemers_from_db_generator(use_local_db: bool = False, limit: Optional[int] = None, batch_size: int = DB_FETCH_BATCH_SIZE):
    """
    Generator that yields batches of redeemer records from database
    Memory-efficient: uses server-side cursor to avoid loading all data at once
    
    Args:
        use_local_db: Use local PostgreSQL instead of Supabase
        limit: Optional limit on total records
        batch_size: Number of records to fetch per batch
    
    Yields:
        Batches of redeemer dicts (each batch is a list)
    """
    print("=" * 70)
    print("📊 QUERYING DATABASE FOR REDEEMERS (BATCH MODE)")
    print("=" * 70)
    
    # Read SQL query from file
    sql_file = "sql/queries/all_XXXm_event_redeemers.sql"
    if not os.path.exists(sql_file):
        raise FileNotFoundError(f"SQL file not found: {sql_file}")
    
    with open(sql_file, 'r', encoding='utf-8') as f:
        sql_query = f.read()
    
    print(f"📄 Using SQL query from: {sql_file}")
    print(f"🧠 Memory optimization: Fetching in batches of {batch_size:,} records")
    
    # Get database connection parameters
    uploader = SupabaseUploader(use_local_db=use_local_db)
    
    if use_local_db:
        # Use local PostgreSQL with server-side cursor
        import psycopg2
        import psycopg2.extras
        
        print(f"🟢 Connecting to local PostgreSQL...")
        print(f"   Database: {uploader.connection_params['database']}")
        print(f"   Host: {uploader.connection_params['host']}:{uploader.connection_params['port']}")
        
        conn = psycopg2.connect(**uploader.connection_params)
        
        # Use named cursor for server-side cursor (doesn't load all data into memory)
        cursor_name = f"redeemers_cursor_{int(time.time())}"
        cursor = conn.cursor(cursor_name, cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Set fetch size for efficient batch reading
        cursor.itersize = batch_size
        
        try:
            # ⚠️ КРИТИЧЕСКИ ВАЖНО: Добавить фильтр по датам событий
            # Импортируем конфиг для получения дат
            current_script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            fetch_dir = os.path.join(current_script_dir, 'fetch')
            if fetch_dir not in sys.path:
                sys.path.insert(0, fetch_dir)
            
            try:
                import fetch_events_config as config
                
                # Добавляем фильтры по датам в SQL запрос
                date_filters = []
                query_params = {}  # Dict для named параметров
                
                if hasattr(config, 'START_DATE') and config.START_DATE:
                    date_filters.append("e.end_date::date >= %(date_from)s")
                    query_params['date_from'] = config.START_DATE.date()
                    print(f"🔍 Filter: Events from {config.START_DATE.date()}")
                
                if hasattr(config, 'END_DATE') and config.END_DATE:
                    date_filters.append("e.end_date::date <= %(date_to)s")
                    query_params['date_to'] = config.END_DATE.date()
                    print(f"🔍 Filter: Events until {config.END_DATE.date()}")
                
                if date_filters:
                    # Вставляем фильтры ПЕРЕД ORDER BY
                    sql_query = sql_query.replace(
                        "ORDER BY r.event_id",
                        " AND " + " AND ".join(date_filters) + "\nORDER BY r.event_id"
                    )
                    print(f"✅ Date filters applied to query")
            except ImportError:
                print("⚠️  Warning: Could not import fetch_events_config, skipping date filters")
                query_params = {}
            
            print(f"🔍 Executing query (server-side cursor)...")
            if query_params:
                cursor.execute(sql_query, query_params)
            else:
                cursor.execute(sql_query)
            
            total_fetched = 0
            batch_num = 0
            
            while True:
                # Fetch a batch
                batch = cursor.fetchmany(batch_size)
                
                if not batch:
                    break
                
                batch_num += 1
                total_fetched += len(batch)
                
                # Convert to list of dicts
                batch_records = [dict(row) for row in batch]
                
                # Apply limit if specified
                if limit and total_fetched > limit:
                    overflow = total_fetched - limit
                    batch_records = batch_records[:-overflow]
                    print(f"✅ Batch {batch_num}: Fetched {len(batch_records):,} records (limit reached: {limit:,} total)")
                    yield batch_records
                    break
                
                print(f"✅ Batch {batch_num}: Fetched {len(batch_records):,} records (total so far: {total_fetched:,})")
                yield batch_records
            
            print(f"🏁 Total records fetched: {total_fetched:,}")
            
        finally:
            cursor.close()
            conn.close()
    
    else:
        print("⚠️  WARNING: Supabase mode requires local PostgreSQL for complex queries")
        print("   Please use --local flag")
        return


def upload_closed_positions_batch(uploader: SupabaseUploader, positions: List[Dict]) -> bool:
    """
    Upload a batch of closed positions to the database
    """
    try:
        if uploader.use_local_db:
            # Use local PostgreSQL
            import psycopg2
            
            conn = psycopg2.connect(**uploader.connection_params)
            cursor = conn.cursor()
            
            try:
                # Prepare bulk insert
                insert_query = """
                    INSERT INTO public.user_closed_positions (
                        proxy_wallet, event_id, market_id, condition_id, asset,
                        avg_price, total_bought, realized_pnl, cur_price,
                        timestamp_unix, timestamp_human,
                        title, slug, icon, event_slug,
                        outcome, outcome_index, opposite_outcome, opposite_asset,
                        end_date, end_date_parsed
                    ) VALUES (
                        %(proxy_wallet)s, %(event_id)s, %(market_id)s, %(condition_id)s, %(asset)s,
                        %(avg_price)s, %(total_bought)s, %(realized_pnl)s, %(cur_price)s,
                        %(timestamp_unix)s, %(timestamp_human)s,
                        %(title)s, %(slug)s, %(icon)s, %(event_slug)s,
                        %(outcome)s, %(outcome_index)s, %(opposite_outcome)s, %(opposite_asset)s,
                        %(end_date)s, %(end_date_parsed)s
                    )
                    ON CONFLICT (proxy_wallet, condition_id, asset, timestamp_unix) 
                    DO UPDATE SET
                        event_id = EXCLUDED.event_id,
                        market_id = EXCLUDED.market_id,
                        avg_price = EXCLUDED.avg_price,
                        total_bought = EXCLUDED.total_bought,
                        realized_pnl = EXCLUDED.realized_pnl,
                        cur_price = EXCLUDED.cur_price,
                        updated_at = NOW()
                """
                
                cursor.executemany(insert_query, positions)
                conn.commit()
                
                return True
                
            except Exception as e:
                conn.rollback()
                print(f"   ❌ Database error: {str(e)}")
                return False
            finally:
                cursor.close()
                conn.close()
        
        else:
            # Use Supabase
            from supabase import create_client
            
            client = create_client(uploader.supabase_url, uploader.supabase_key)
            
            # Supabase upsert
            response = client.table('user_closed_positions').upsert(
                positions,
                on_conflict='proxy_wallet,condition_id,asset,timestamp_unix'
            ).execute()
            
            return True
            
    except Exception as e:
        print(f"   ❌ Upload error: {str(e)}")
        return False


# ==========================================
# PARALLEL FETCHER
# ==========================================

class ParallelPositionsFetcher:
    """Parallel fetcher with connection pooling and rate limiting"""
    
    def __init__(self, 
                 max_workers: int = DEFAULT_WORKERS,
                 positions_per_user: int = DEFAULT_POSITIONS_PER_USER,
                 upload: bool = False,
                 use_local_db: bool = False,
                 verbose: bool = False,
                 market_filter: Optional[List[str]] = None,
                 title_filter: Optional[str] = None,
                 event_id_filter: Optional[List[int]] = None):
        """
        Initialize parallel fetcher
        
        Args:
            max_workers: Number of parallel threads
            positions_per_user: Max positions to fetch per user
            upload: Whether to upload to database
            use_local_db: Use local PostgreSQL instead of Supabase
            verbose: Show detailed output
            market_filter: List of conditionIds to filter by (optional)
            title_filter: Filter by market title (optional)
            event_id_filter: List of event IDs to filter by (optional)
        """
        self.max_workers = max_workers
        self.positions_per_user = positions_per_user
        self.upload = upload
        self.use_local_db = use_local_db
        self.verbose = verbose
        self.market_filter = market_filter
        self.title_filter = title_filter
        self.event_id_filter = event_id_filter
        
        # Create rate limiter (145 req/10s optimized for speed)
        self.rate_limiter = RateLimiter(max_requests=RATE_LIMIT_SAFE, window_seconds=RATE_LIMIT_WINDOW)
        
        # Shared client with connection pool and rate limiter
        self.client = SharedAPIClient(
            max_pool_connections=max_workers + 10,
            rate_limiter=self.rate_limiter
        )
        
        # Database uploader
        self.uploader = SupabaseUploader(use_local_db=use_local_db) if upload else None
        
        # Thread-safe state
        self.lock = Lock()
        self.all_positions = []
        self.failed_users = []
        
        # Statistics
        self.stats = {
            'total_users': 0,
            'users_processed': 0,
            'users_with_positions': 0,
            'total_positions': 0,
            'errors': 0,
            'retried_users': 0,
            'retry_successes': 0,
            'db_uploads': 0,
            'db_upload_errors': 0,
            'start_time': None,
            'end_time': None
        }
    
    def fetch_all_for_user(
        self, 
        user_address: str,
        market: Optional[List[str]] = None,
        title: Optional[str] = None,
        event_id: Optional[List[int]] = None
    ) -> tuple[List[Dict], Optional[str]]:
        """
        Fetch all closed positions for a single user with pagination
        
        Args:
            user_address: The address of the user (required)
            market: List of conditionIds to filter by (optional)
            title: Filter by market title (optional)
            event_id: List of event IDs to filter by (optional)
        
        Returns:
            tuple: (positions_list, error_message)
        """
        all_positions = []
        offset = 0
        batch_size = 50  # API max per request
        
        while len(all_positions) < self.positions_per_user:
            positions, error = self.client.fetch_closed_positions(
                user_address,
                limit=batch_size,
                offset=offset,
                market=market,
                title=title,
                event_id=event_id
            )
            
            if error:
                # API error
                return (None, error)
            
            if not positions or len(positions) == 0:
                # No more data
                break
            
            all_positions.extend(positions)
            offset += len(positions)
            
            # If we got less than batch_size, we've reached the end
            if len(positions) < batch_size:
                break
        
        return (all_positions[:self.positions_per_user], None)
    
    def process_single_user(self, user_address: str, user_index: int, total_users: int, market_override: Optional[str] = None) -> tuple[int, bool]:
        """
        Process a single user (fetch and optionally upload)
        
        Args:
            user_address: User's wallet address
            user_index: Index for tracking
            total_users: Total number to process
            market_override: Specific market conditionId for this user (overrides global filter)
        
        Returns:
            tuple: (positions_count, is_error)
        """
        try:
            # Determine which market filter to use
            # If market_override is provided, use it (from SQL query)
            # Otherwise, use the global market_filter
            market_filter = [market_override] if market_override else self.market_filter
            
            # Fetch positions with filters
            positions, error = self.fetch_all_for_user(
                user_address,
                market=market_filter,
                title=self.title_filter,
                event_id=self.event_id_filter
            )
            
            if error:
                # Failed request
                with self.lock:
                    self.stats['errors'] += 1
                    self.failed_users.append((user_address, market_override))
                return (0, True)
            
            if not positions or len(positions) == 0:
                # No positions found (not an error)
                return (0, False)
            
            # Transform data
            transformed = [transform_closed_position(p) for p in positions]
            
            # Store or upload
            with self.lock:
                self.all_positions.extend(transformed)
                self.stats['users_with_positions'] += 1
                self.stats['total_positions'] += len(transformed)
                
                # Upload in batches if needed
                if self.upload and len(self.all_positions) >= BATCH_SIZE:
                    batch_to_upload = self.all_positions[:BATCH_SIZE]
                    self.all_positions = self.all_positions[BATCH_SIZE:]
                    
                    success = upload_closed_positions_batch(self.uploader, batch_to_upload)
                    if success:
                        self.stats['db_uploads'] += len(batch_to_upload)
                    else:
                        self.stats['db_upload_errors'] += len(batch_to_upload)
            
            return (len(transformed), False)
            
        except Exception as e:
            with self.lock:
                self.stats['errors'] += 1
                self.failed_users.append((user_address, market_override))
            return (0, True)
    
    def process_all_users(self, user_records: List[Dict], show_header: bool = True):
        """
        Process all user-market pairs in parallel
        
        Args:
            user_records: List of dicts with 'redeemer_address' and optionally 'condition_id'
            show_header: Whether to show the configuration header (use False for batch processing)
        """
        # Initialize start time only if not already set
        if self.stats['start_time'] is None:
            self.stats['start_time'] = datetime.now()
        
        # Add to total users count (for batch processing)
        self.stats['total_users'] += len(user_records)
        
        if show_header:
            print("\n" + "=" * 70)
            print("🚀 PARALLEL FETCHING CLOSED POSITIONS")
            print("=" * 70)
        
        print(f"📋 Processing {len(user_records):,} records in this batch")
        
        # Check if we have market filtering from SQL query
        has_market_from_sql = any('condition_id' in r and r.get('condition_id') for r in user_records)
        if has_market_from_sql:
            unique_users = len(set(r['redeemer_address'] for r in user_records))
            unique_markets = len(set(r.get('condition_id') for r in user_records if r.get('condition_id')))
            print(f"   • User-Market pairs: {len(user_records):,}")
            print(f"   • Unique users: {unique_users:,}")
            print(f"   • Unique markets: {unique_markets:,}")
        else:
            unique_users = len(set(r['redeemer_address'] for r in user_records))
            print(f"   • Unique users: {unique_users:,}")
        
        if show_header:
            print(f"   • Positions per query: {self.positions_per_user}")
            print(f"   • Parallel workers: {self.max_workers}")
            print(f"   • Upload to DB: {'YES' if self.upload else 'NO (preview only)'}")
            if self.upload:
                print(f"   • Database: {'Local PostgreSQL' if self.use_local_db else 'Supabase'}")
            print(f"   • Connection Pooling: ENABLED ✅")
            print(f"   • Rate Limiting: ENABLED ✅ ({RATE_LIMIT_SAFE}/{RATE_LIMIT_WINDOW}s safe limit)")
            
            # Show active filters
            if self.market_filter or self.title_filter or self.event_id_filter or has_market_from_sql:
                print(f"\n🔍 Active Filters:")
                if has_market_from_sql:
                    print(f"   • Markets: From SQL query (user-specific)")
                elif self.market_filter:
                    print(f"   • Markets: {', '.join(self.market_filter[:3])}{'...' if len(self.market_filter) > 3 else ''}")
                if self.title_filter:
                    print(f"   • Title: '{self.title_filter}'")
                if self.event_id_filter:
                    print(f"   • Event IDs: {', '.join(map(str, self.event_id_filter))}")
            
            # Estimate time
            if self.positions_per_user > 100:
                requests_per_user = (self.positions_per_user // 50) + 1
                # Rate limiter allows ~14.5 req/s (145/10s)
                req_per_second = RATE_LIMIT_SAFE / RATE_LIMIT_WINDOW
                seconds_per_user = requests_per_user / req_per_second
                total_seconds = (len(user_records) * seconds_per_user) / self.max_workers
                total_minutes = total_seconds / 60
                
                print(f"\n⏱️  ESTIMATED TIME:")
                print(f"   Requests per query: ~{requests_per_user}")
                if total_minutes >= 60:
                    print(f"   Estimated time: ~{total_minutes/60:.1f} hours")
                elif total_minutes >= 1:
                    print(f"   Estimated time: ~{total_minutes:.1f} minutes")
                else:
                    print(f"   Estimated time: ~{total_seconds:.0f} seconds")
            
            print("=" * 70)
        print()
        
        # Process users in parallel
        last_update = time.time()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_user = {}
            for i, record in enumerate(user_records, 1):
                user_address = record['redeemer_address']
                market_id = record.get('condition_id')  # May be None
                
                future = executor.submit(
                    self.process_single_user, 
                    user_address, 
                    i, 
                    len(user_records),
                    market_id
                )
                future_to_user[future] = (user_address, market_id)
            
            # Process results as they complete
            for future in as_completed(future_to_user):
                user_address, market_id = future_to_user[future]
                
                try:
                    positions_count, is_error = future.result()
                    
                    with self.lock:
                        self.stats['users_processed'] += 1
                    
                except Exception as e:
                    with self.lock:
                        self.stats['users_processed'] += 1
                        self.stats['errors'] += 1
                        self.failed_users.append((user_address, market_id))
                
                # Progress update
                current_time = time.time()
                if current_time - last_update >= 0.5:
                    with self.lock:
                        elapsed = (datetime.now() - self.stats['start_time']).total_seconds()
                        rate = self.stats['users_processed'] / elapsed if elapsed > 0 else 0
                        
                        # Show rate limiter status
                        current_rate = self.rate_limiter.get_current_rate()
                        rate_info = f"API: {current_rate}/{RATE_LIMIT_SAFE}"
                        
                        print(
                            f"📥 Progress: {self.stats['users_processed']}/{self.stats['total_users']} records | "
                            f"{self.stats['total_positions']:,} positions | "
                            f"{rate:.1f} queries/s | "
                            f"{rate_info} | "
                            f"{self.stats['errors']} failed",
                            end="\r",
                            flush=True
                        )
                    last_update = current_time
        
        print()
        print("-" * 70)
        
        # Upload remaining positions
        if self.upload and self.all_positions:
            print(f"📤 Uploading final batch of {len(self.all_positions)} positions...")
            success = upload_closed_positions_batch(self.uploader, self.all_positions)
            with self.lock:
                if success:
                    self.stats['db_uploads'] += len(self.all_positions)
                    print(f"✅ Final batch uploaded successfully")
                else:
                    self.stats['db_upload_errors'] += len(self.all_positions)
                    print(f"❌ Final batch upload failed")
            self.all_positions = []
        
        # Retry failed users
        if self.failed_users:
            self._retry_failed_users()
        
        self.stats['end_time'] = datetime.now()
    
    def _retry_failed_users(self, max_retries: int = 2):
        """
        Retry failed user-market pairs with exponential backoff
        """
        retry_attempt = 1
        
        while self.failed_users and retry_attempt <= max_retries:
            print()
            print(f"🔄 Retry attempt {retry_attempt}/{max_retries}")
            print(f"   Retrying {len(self.failed_users)} failed records...")
            
            # Get failed users and clear the list
            with self.lock:
                users_to_retry = list(self.failed_users)
                self.failed_users = []
            
            # Wait before retrying
            if retry_attempt > 1:
                wait_time = 2 ** (retry_attempt - 1)
                print(f"   Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
            
            # Retry with fewer workers
            retry_workers = max(1, self.max_workers // 2)
            retry_successes = 0
            
            with ThreadPoolExecutor(max_workers=retry_workers) as executor:
                future_to_user = {}
                for i, (user_addr, market_id) in enumerate(users_to_retry, 1):
                    future = executor.submit(
                        self.process_single_user, 
                        user_addr, 
                        i, 
                        len(users_to_retry),
                        market_id
                    )
                    future_to_user[future] = (user_addr, market_id)
                
                for future in as_completed(future_to_user):
                    user_address, market_id = future_to_user[future]
                    
                    try:
                        positions_count, is_error = future.result()
                        
                        with self.lock:
                            self.stats['retried_users'] += 1
                        
                        if not is_error:
                            retry_successes += 1
                            with self.lock:
                                self.stats['retry_successes'] += 1
                        
                    except Exception as e:
                        with self.lock:
                            self.stats['retried_users'] += 1
                            self.failed_users.append((user_address, market_id))
            
            print(f"   ✓ Retry {retry_attempt} completed: {retry_successes} recovered")
            retry_attempt += 1
        
        # Final summary
        with self.lock:
            final_failures = len(self.failed_users)
            if final_failures > 0:
                print(f"   ⚠️  {final_failures} records still failed after {max_retries} retries")
            else:
                print(f"   ✅ All failed records recovered!")
        
        print("-" * 70)
    
    def print_summary(self):
        """Print summary statistics"""
        duration = (self.stats['end_time'] - self.stats['start_time']).total_seconds()
        
        print("\n" + "=" * 70)
        print("📊 PROCESSING SUMMARY")
        print("=" * 70)
        print(f"⏱️  Duration: {duration:.2f} seconds ({duration/60:.1f} minutes)")
        print(f"📝 Total records processed: {self.stats['total_users']}")
        print(f"✅ Successfully processed: {self.stats['users_processed']}")
        print(f"📊 Records with positions: {self.stats['users_with_positions']}")
        print(f"💼 Total positions fetched: {self.stats['total_positions']:,}")
        print(f"❌ Errors: {self.stats['errors']}")
        
        if self.stats['retried_users'] > 0:
            print(f"\n🔄 Retry Statistics:")
            print(f"   • Total retry attempts: {self.stats['retried_users']}")
            print(f"   • Successfully recovered: {self.stats['retry_successes']}")
            print(f"   • Still failed: {len(self.failed_users)}")
        
        if self.upload:
            print(f"\n💾 Database Upload:")
            print(f"   • Successfully uploaded: {self.stats['db_uploads']:,}")
            if self.stats['db_upload_errors'] > 0:
                print(f"   • Upload errors: {self.stats['db_upload_errors']:,}")
        
        if duration > 0:
            print(f"\n⚡ Speed:")
            print(f"   • {self.stats['users_processed']/duration:.2f} queries/second")
            print(f"   • {self.stats['total_positions']/duration:.1f} positions/second")
        
        if self.all_positions and not self.upload:
            print(f"\n📋 Sample Positions (first 5):")
            for i, pos in enumerate(self.all_positions[:5], 1):
                print(f"   {i}. {pos['proxy_wallet'][:10]}... - {pos['title'][:40]}...")
                print(f"      PnL: ${pos['realized_pnl']:.2f}")
        
        print("=" * 70)
    
    def close(self):
        """Clean up resources"""
        self.client.close()


# ==========================================
# CLI INTERFACE
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description='Parallel fetch of closed positions for redeemers from Polymarket API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview mode (5 workers, no upload)
  python fetch_user_closed_positions_parallel.py --local
  
  # Full upload with 10 workers
  python fetch_user_closed_positions_parallel.py --upload --local --workers 10
  
  # Test with 10 users
  python fetch_user_closed_positions_parallel.py --local --limit 10 --verbose
  
  # Fetch many positions per user
  python fetch_user_closed_positions_parallel.py --upload --local --positions 1000 --workers 8
  
  # Filter by specific markets (conditionIds)
  python fetch_user_closed_positions_parallel.py --local --market 0xabc123... 0xdef456...
  
  # Filter by event IDs
  python fetch_user_closed_positions_parallel.py --local --event-id 12345 67890
  
  # Filter by market title
  python fetch_user_closed_positions_parallel.py --local --title "Trump"
  
  # Low memory mode for weak computers (smaller batches)
  python fetch_user_closed_positions_parallel.py --upload --local --db-batch 5000
        """
    )
    
    parser.add_argument(
        '--upload',
        action='store_true',
        help='Upload fetched data to database (default: preview only)'
    )
    
    parser.add_argument(
        '--local',
        action='store_true',
        help='Use local PostgreSQL instead of Supabase'
    )
    
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Limit number of redeemers to process (for testing)'
    )
    
    parser.add_argument(
        '--positions',
        type=int,
        default=DEFAULT_POSITIONS_PER_USER,
        help=f'Max positions to fetch per user (default: {DEFAULT_POSITIONS_PER_USER})'
    )
    
    parser.add_argument(
        '--workers',
        type=int,
        default=DEFAULT_WORKERS,
        help=f'Number of parallel workers (default: {DEFAULT_WORKERS}, max recommended: 10)'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output'
    )
    
    parser.add_argument(
        '--market',
        type=str,
        nargs='+',
        default=None,
        help='Filter by market conditionIds (space-separated list). Cannot be used with --event-id'
    )
    
    parser.add_argument(
        '--title',
        type=str,
        default=None,
        help='Filter by market title (partial match)'
    )
    
    parser.add_argument(
        '--event-id',
        type=int,
        nargs='+',
        default=None,
        help='Filter by event IDs (space-separated list). Cannot be used with --market'
    )
    
    parser.add_argument(
        '--db-batch',
        type=int,
        default=DB_FETCH_BATCH_SIZE,
        help=f'Batch size for fetching redeemers from DB (default: {DB_FETCH_BATCH_SIZE:,}). Lower value = less memory usage'
    )
    
    args = parser.parse_args()
    
    # Validate that market and event-id are not used together
    if args.market and args.event_id:
        print("❌ ERROR: Cannot use both --market and --event-id parameters together")
        print("   Please choose one or the other.")
        sys.exit(1)
    
    # Validate workers
    if args.workers > 15:
        print(f"⚠️  WARNING: {args.workers} workers may exceed API rate limits")
        print(f"   Recommended max: 10 workers")
        print(f"   Using {args.workers} anyway, but expect throttling...")
        print()
    
    print("\n" + "=" * 70)
    print("🎯 POLYMARKET CLOSED POSITIONS FETCHER (PARALLEL)")
    print("=" * 70)
    print(f"Mode: {'UPLOAD' if args.upload else 'PREVIEW ONLY'}")
    if args.upload:
        print(f"Database: {'Local PostgreSQL' if args.local else 'Supabase'}")
    if args.limit:
        print(f"User limit: {args.limit}")
    print(f"Positions per user: {args.positions}")
    print(f"Parallel workers: {args.workers}")
    print(f"DB batch size: {args.db_batch:,} records (memory optimization)")
    
    # Show active filters
    if args.market or args.title or args.event_id:
        print(f"\n🔍 API Filters:")
        if args.market:
            print(f"   Market(s): {', '.join(args.market[:3])}{'...' if len(args.market) > 3 else ''}")
        if args.title:
            print(f"   Title: '{args.title}'")
        if args.event_id:
            print(f"   Event ID(s): {', '.join(map(str, args.event_id))}")
    
    print("=" * 70)
    
    try:
        # Step 1: Create parallel fetcher
        fetcher = ParallelPositionsFetcher(
            max_workers=args.workers,
            positions_per_user=args.positions,
            upload=args.upload,
            use_local_db=args.local,
            verbose=args.verbose,
            market_filter=args.market,
            title_filter=args.title,
            event_id_filter=args.event_id
        )
        
        # Step 2: Process redeemers in batches (memory-efficient)
        batch_generator = get_redeemers_from_db_generator(
            use_local_db=args.local,
            limit=args.limit,
            batch_size=args.db_batch
        )
        
        total_processed = 0
        batch_count = 0
        has_data = False
        
        print("\n" + "=" * 70)
        print("🚀 STARTING BATCH PROCESSING")
        print("=" * 70)
        
        for batch in batch_generator:
            has_data = True
            batch_count += 1
            
            if not batch:
                continue
            
            print(f"\n{'='*70}")
            print(f"📦 Processing Batch {batch_count} ({len(batch):,} records)")
            print(f"{'='*70}")
            
            # Process this batch (show header only for first batch)
            fetcher.process_all_users(batch, show_header=(batch_count == 1))
            
            total_processed += len(batch)
            print(f"\n✅ Batch {batch_count} completed. Total processed so far: {total_processed:,}")
        
        if not has_data:
            print("\n❌ No redeemers found in database")
            print("💡 Make sure:")
            print("   1. Your database is properly configured in .env")
            print("   2. The redemptions and events tables have data")
            print("   3. There are events with volume > 100,000,000")
            return
        
        # Step 3: Print summary
        print("\n" + "=" * 70)
        print("🏁 ALL BATCHES COMPLETED")
        print("=" * 70)
        fetcher.print_summary()
        
        # Step 4: Cleanup
        fetcher.close()
        
        print("\n✅ Process completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
