"""
Parallel Fetch Trader Leaderboard from Polymarket API
Uses parallel processing with rate limiting and connection pooling

БЫСТРЫЙ ЗАПУСК:
===============
1. Preview mode с параллельной обработкой (5 workers):
   python fetch_trader_leaderboard_parallel.py --local

2. Полная загрузка в PostgreSQL (рекомендуется, по умолчанию ALL):
   python fetch_trader_leaderboard_parallel.py --upload --local

3. Топ трейдеры за день по PnL:
   python fetch_trader_leaderboard_parallel.py --local --time-period DAY --order-by PNL --limit 50

4. Топ трейдеры за неделю по PnL:
   python fetch_trader_leaderboard_parallel.py --local --time-period WEEK --order-by PNL --limit 50

5. Топ по объёму в категории политика:
   python fetch_trader_leaderboard_parallel.py --upload --local --category POLITICS --order-by VOL

6. Все категории за месяц (параллельно):
   python fetch_trader_leaderboard_parallel.py --upload --local --all-categories --time-period MONTH

7. Множественные временные периоды:
   python fetch_trader_leaderboard_parallel.py --upload --local --all-time-periods

8. Полная матрица (все категории × все периоды):
   python fetch_trader_leaderboard_parallel.py --upload --local --full-matrix

9. Конкретные пользователи по адресам:
   python fetch_trader_leaderboard_parallel.py --local --users 0xabc... 0xdef...

10. Поиск пользователей по username:
   python fetch_trader_leaderboard_parallel.py --local --usernames trader1 trader2

11. ⭐ НОВОЕ! Использовать кошельки из БД (по умолчанию ALL):
   python fetch_trader_leaderboard_parallel.py --upload --local --from-db

12. Из БД с лимитом на тестирование:
   python fetch_trader_leaderboard_parallel.py --upload --local --from-db --limit-wallets 100

ТРЕБОВАНИЯ:
===========
- Python 3.8+
- pip install -r requirements.txt
- Файл .env с настройками (для БД)
- База данных с таблицей trader_leaderboard

API LIMITS:
===========
- Leaderboard API: 150 requests / 10 seconds
- Optimized limit: 145 requests / 10 seconds (~97% of max)
- Automatic rate limiting and retry on failures

Features:
- ⚡ Параллельная обработка запросов (5 workers по умолчанию)
- 🧠 Memory-efficient batch processing
- 🎯 Поддержка всех параметров API (категории, периоды, сортировка)
- 🔄 Автоматический rate limiting (145 req/10s optimized limit)
- 🔁 Connection pooling для эффективного использования соединений
- 🔄 Автоматический retry при ошибках API
- 📦 Batch загрузка в БД (каждые 100 записей)
- 📊 Real-time прогресс и статистика
- 💾 Поддержка Supabase и локальной PostgreSQL
- 🎨 Матричная обработка (все категории × все периоды)
"""

import requests
import json
import time
import os
import sys
import io
import argparse
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from collections import deque
from itertools import product

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
DEFAULT_WORKERS = 5  # Conservative default (API limit: 1000/10s)
DEFAULT_LIMIT_PER_REQUEST = 50  # API max is 50
BATCH_SIZE = 100  # How many records to upload to DB at once
DB_FETCH_BATCH_SIZE = 2500  # How many wallet addresses to fetch from DB at once

# Rate limiting: 1000 req/10s, use 950 req/10s for higher speed
RATE_LIMIT_MAX = 1000
RATE_LIMIT_WINDOW = 10
RATE_LIMIT_SAFE = 970  # ~97% of max, optimized for speed

# API Enums
CATEGORIES = [
    "OVERALL", "POLITICS", "SPORTS", "CRYPTO", "CULTURE",
    "MENTIONS", "WEATHER", "ECONOMICS", "TECH", "FINANCE"
]

TIME_PERIODS = ["DAY", "WEEK", "MONTH", "ALL"]

ORDER_BY_OPTIONS = ["PNL", "VOL"]


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
    
    def fetch_leaderboard(
        self,
        category: str = "OVERALL",
        time_period: str = "DAY",
        order_by: str = "PNL",
        limit: int = 25,
        offset: int = 0,
        user: Optional[str] = None,
        user_name: Optional[str] = None
    ) -> Tuple[Optional[List[Dict]], Optional[str]]:
        """
        Fetch trader leaderboard rankings (thread-safe with rate limiting)
        
        Args:
            category: Market category (OVERALL, POLITICS, SPORTS, etc.)
            time_period: Time period (DAY, WEEK, MONTH, ALL)
            order_by: Sort criteria (PNL, VOL)
            limit: Max number of traders to return (max 50)
            offset: Starting index for pagination
            user: Filter by specific user address
            user_name: Filter by specific username
        
        Returns:
            tuple: (leaderboard_list, error_message)
                - ([], None) = Empty result (valid, no more data)
                - ([...], None) = Success with data
                - (None, error) = Failed request
        """
        try:
            # Wait if necessary to respect rate limit
            if self.rate_limiter:
                self.rate_limiter.wait_if_needed()
            
            params = {
                'category': category,
                'timePeriod': time_period,
                'orderBy': order_by,
                'limit': min(limit, 50),  # API max is 50
                'offset': offset
            }
            
            # Add optional filter parameters
            if user:
                params['user'] = user
            
            if user_name:
                params['userName'] = user_name
            
            url = f"{API_BASE_URL}/leaderboard"
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

def transform_leaderboard_entry(entry: Dict, category: str, time_period: str, order_by: str) -> Dict:
    """
    Transform API response to match database schema
    """
    now = datetime.now()
    return {
        'rank': int(entry.get('rank', 0)) if entry.get('rank') else None,
        'proxy_wallet': entry.get('proxyWallet', ''),
        'user_name': entry.get('userName'),
        'vol': float(entry.get('vol', 0)),
        'pnl': float(entry.get('pnl', 0)),
        'profile_image': entry.get('profileImage'),
        'x_username': entry.get('xUsername'),
        'verified_badge': entry.get('verifiedBadge', False),
        'category': category,
        'time_period': time_period,
        'order_by': order_by,
        'fetched_at': now.isoformat(),
        'fetched_date': now.date().isoformat()
    }


# ==========================================
# DATABASE FUNCTIONS
# ==========================================

def upload_leaderboard_batch(uploader: SupabaseUploader, entries: List[Dict]) -> bool:
    """
    Upload a batch of leaderboard entries to the database
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
                    INSERT INTO public.trader_leaderboard (
                        rank, proxy_wallet, user_name, vol, pnl,
                        profile_image, x_username, verified_badge,
                        category, time_period, order_by, fetched_at, fetched_date
                    ) VALUES (
                        %(rank)s, %(proxy_wallet)s, %(user_name)s, %(vol)s, %(pnl)s,
                        %(profile_image)s, %(x_username)s, %(verified_badge)s,
                        %(category)s, %(time_period)s, %(order_by)s, %(fetched_at)s, %(fetched_date)s
                    )
                    ON CONFLICT (proxy_wallet, category, time_period, order_by, fetched_date) 
                    DO UPDATE SET
                        rank = EXCLUDED.rank,
                        user_name = EXCLUDED.user_name,
                        vol = EXCLUDED.vol,
                        pnl = EXCLUDED.pnl,
                        profile_image = EXCLUDED.profile_image,
                        x_username = EXCLUDED.x_username,
                        verified_badge = EXCLUDED.verified_badge,
                        fetched_at = EXCLUDED.fetched_at,
                        updated_at = NOW()
                """
                
                cursor.executemany(insert_query, entries)
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
            response = client.table('trader_leaderboard').upsert(
                entries,
                on_conflict='proxy_wallet,category,time_period,order_by,fetched_at'
            ).execute()
            
            return True
            
    except Exception as e:
        print(f"   ❌ Upload error: {str(e)}")
        return False


def get_unique_wallets_from_db_generator(
    use_local_db: bool = False,
    limit: Optional[int] = None,
    batch_size: int = DB_FETCH_BATCH_SIZE
):
    """
    Generator that yields batches of unique wallet addresses from database
    Memory-efficient: uses server-side cursor to avoid loading all data at once
    
    Uses the same source as closed positions (redemptions table)
    
    Args:
        use_local_db: Use local PostgreSQL instead of Supabase
        limit: Optional limit on total wallets
        batch_size: Number of wallets to fetch per batch
    
    Yields:
        Batches of wallet addresses (each batch is a list of strings)
    """
    print("=" * 70)
    print("📊 QUERYING DATABASE FOR UNIQUE WALLETS")
    print("=" * 70)
    
    # SQL query to get unique wallet addresses from redemptions
    # Same source as closed positions - ensures consistency
    # ⚠️ ВАЖНО: Фильтруем по датам событий через JOIN с events
    sql_query = """
        SELECT DISTINCT r.redeemer_address
        FROM public.redemptions r
        JOIN public.events e ON r.event_id = e.id
        WHERE r.redeemer_address IS NOT NULL
          AND r.redeemer_address != ''
          AND r.event_id IS NOT NULL
        ORDER BY r.redeemer_address
    """
    
    print(f"📄 Using unique wallets from redemptions table")
    print(f"🧠 Memory optimization: Fetching in batches of {batch_size:,} wallets")
    
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
        
        # Use named cursor for server-side cursor
        cursor_name = f"wallets_cursor_{int(time.time())}"
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
                query_params = []
                
                if hasattr(config, 'START_DATE') and config.START_DATE:
                    date_filters.append("e.end_date >= %s")
                    query_params.append(config.START_DATE)
                    print(f"🔍 Filter: Events from {config.START_DATE.date()}")
                
                if hasattr(config, 'END_DATE') and config.END_DATE:
                    date_filters.append("e.end_date <= %s")
                    query_params.append(config.END_DATE)
                    print(f"🔍 Filter: Events until {config.END_DATE.date()}")
                
                if date_filters:
                    # Добавляем фильтры к запросу (заменяем ORDER BY)
                    sql_query_with_filters = sql_query.replace(
                        "ORDER BY r.redeemer_address",
                        "AND " + " AND ".join(date_filters) + "\n        ORDER BY r.redeemer_address"
                    )
                    sql_query = sql_query_with_filters
                    print(f"✅ Date filters applied to query")
            except ImportError:
                print("⚠️  Warning: Could not import fetch_events_config, skipping date filters")
                query_params = []
            
            print(f"🔍 Executing query (server-side cursor)...")
            if query_params:
                # Convert list to tuple for psycopg2
                cursor.execute(sql_query, tuple(query_params))
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
                
                # Extract wallet addresses
                wallet_addresses = [row['redeemer_address'] for row in batch]
                
                # Apply limit if specified
                if limit and total_fetched > limit:
                    overflow = total_fetched - limit
                    wallet_addresses = wallet_addresses[:-overflow]
                    print(f"✅ Batch {batch_num}: Fetched {len(wallet_addresses):,} wallets (limit reached: {limit:,} total)")
                    yield wallet_addresses
                    break
                
                print(f"✅ Batch {batch_num}: Fetched {len(wallet_addresses):,} wallets (total so far: {total_fetched:,})")
                yield wallet_addresses
            
            print(f"🏁 Total unique wallets fetched: {total_fetched:,}")
            
        finally:
            cursor.close()
            conn.close()
    
    else:
        print("⚠️  WARNING: Supabase mode requires local PostgreSQL for complex queries")
        print("   Please use --local flag")
        return


# ==========================================
# PARALLEL FETCHER
# ==========================================

class ParallelLeaderboardFetcher:
    """Parallel fetcher with connection pooling and rate limiting"""
    
    def __init__(self, 
                 max_workers: int = DEFAULT_WORKERS,
                 limit_per_request: int = DEFAULT_LIMIT_PER_REQUEST,
                 upload: bool = False,
                 use_local_db: bool = False,
                 verbose: bool = False):
        """
        Initialize parallel fetcher
        
        Args:
            max_workers: Number of parallel threads
            limit_per_request: Max entries to fetch per request
            upload: Whether to upload to database
            use_local_db: Use local PostgreSQL instead of Supabase
            verbose: Show detailed output
        """
        self.max_workers = max_workers
        self.limit_per_request = limit_per_request
        self.upload = upload
        self.use_local_db = use_local_db
        self.verbose = verbose
        
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
        self.all_entries = []
        self.failed_requests = []
        
        # Statistics
        self.stats = {
            'total_requests': 0,
            'requests_processed': 0,
            'total_entries': 0,
            'errors': 0,
            'retried_requests': 0,
            'retry_successes': 0,
            'db_uploads': 0,
            'db_upload_errors': 0,
            'start_time': None,
            'end_time': None
        }
    
    def fetch_all_with_pagination(
        self,
        category: str,
        time_period: str,
        order_by: str,
        user: Optional[str] = None,
        user_name: Optional[str] = None
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        Fetch all leaderboard entries with pagination
        
        Args:
            category: Market category
            time_period: Time period
            order_by: Sort criteria
            user: Filter by user address
            user_name: Filter by username
        
        Returns:
            tuple: (entries_list, error_message)
        """
        all_entries = []
        offset = 0
        
        while True:
            entries, error = self.client.fetch_leaderboard(
                category=category,
                time_period=time_period,
                order_by=order_by,
                limit=self.limit_per_request,
                offset=offset,
                user=user,
                user_name=user_name
            )
            
            if error:
                # API error
                return (None, error)
            
            if not entries or len(entries) == 0:
                # No more data
                break
            
            all_entries.extend(entries)
            offset += len(entries)
            
            # If we got less than limit, we've reached the end
            if len(entries) < self.limit_per_request:
                break
            
            # API returns max 1000 results (offset max)
            if offset >= 1000:
                break
        
        return (all_entries, None)
    
    def process_single_request(
        self, 
        category: str, 
        time_period: str, 
        order_by: str,
        request_index: int,
        total_requests: int,
        user: Optional[str] = None,
        user_name: Optional[str] = None
    ) -> Tuple[int, bool]:
        """
        Process a single leaderboard request (fetch and optionally upload)
        
        Args:
            category: Market category
            time_period: Time period
            order_by: Sort criteria
            request_index: Index for tracking
            total_requests: Total number to process
            user: Filter by user address
            user_name: Filter by username
        
        Returns:
            tuple: (entries_count, is_error)
        """
        try:
            # Fetch entries with pagination
            entries, error = self.fetch_all_with_pagination(
                category=category,
                time_period=time_period,
                order_by=order_by,
                user=user,
                user_name=user_name
            )
            
            if error:
                # Failed request
                with self.lock:
                    self.stats['errors'] += 1
                    self.failed_requests.append((category, time_period, order_by, user, user_name))
                return (0, True)
            
            if not entries or len(entries) == 0:
                # No entries found (not an error)
                return (0, False)
            
            # Transform data
            transformed = [
                transform_leaderboard_entry(e, category, time_period, order_by) 
                for e in entries
            ]
            
            # Store or upload
            with self.lock:
                self.all_entries.extend(transformed)
                self.stats['total_entries'] += len(transformed)
                
                # Upload in batches if needed
                if self.upload and len(self.all_entries) >= BATCH_SIZE:
                    batch_to_upload = self.all_entries[:BATCH_SIZE]
                    self.all_entries = self.all_entries[BATCH_SIZE:]
                    
                    success = upload_leaderboard_batch(self.uploader, batch_to_upload)
                    if success:
                        self.stats['db_uploads'] += len(batch_to_upload)
                    else:
                        self.stats['db_upload_errors'] += len(batch_to_upload)
            
            return (len(transformed), False)
            
        except Exception as e:
            with self.lock:
                self.stats['errors'] += 1
                self.failed_requests.append((category, time_period, order_by, user, user_name))
            return (0, True)
    
    def process_requests(self, requests: List[Tuple]):
        """
        Process multiple requests in parallel
        
        Args:
            requests: List of tuples (category, time_period, order_by, user, user_name)
        """
        self.stats['start_time'] = datetime.now()
        self.stats['total_requests'] = len(requests)
        
        print("\n" + "=" * 70)
        print("🚀 PARALLEL FETCHING LEADERBOARD")
        print("=" * 70)
        print(f"📋 Total requests: {len(requests):,}")
        print(f"   • Entries per request: up to {self.limit_per_request}")
        print(f"   • Parallel workers: {self.max_workers}")
        print(f"   • Upload to DB: {'YES' if self.upload else 'NO (preview only)'}")
        if self.upload:
            print(f"   • Database: {'Local PostgreSQL' if self.use_local_db else 'Supabase'}")
        print(f"   • Connection Pooling: ENABLED ✅")
        print(f"   • Rate Limiting: ENABLED ✅ ({RATE_LIMIT_SAFE}/{RATE_LIMIT_WINDOW}s safe limit)")
        print("=" * 70)
        print()
        
        # Process requests in parallel
        last_update = time.time()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_request = {}
            for i, request in enumerate(requests, 1):
                category, time_period, order_by, user, user_name = request
                
                future = executor.submit(
                    self.process_single_request,
                    category,
                    time_period,
                    order_by,
                    i,
                    len(requests),
                    user,
                    user_name
                )
                future_to_request[future] = request
            
            # Process results as they complete
            for future in as_completed(future_to_request):
                request = future_to_request[future]
                
                try:
                    entries_count, is_error = future.result()
                    
                    with self.lock:
                        self.stats['requests_processed'] += 1
                    
                except Exception as e:
                    with self.lock:
                        self.stats['requests_processed'] += 1
                        self.stats['errors'] += 1
                        self.failed_requests.append(request)
                
                # Progress update
                current_time = time.time()
                if current_time - last_update >= 0.5:
                    with self.lock:
                        elapsed = (datetime.now() - self.stats['start_time']).total_seconds()
                        rate = self.stats['requests_processed'] / elapsed if elapsed > 0 else 0
                        
                        # Show rate limiter status
                        current_rate = self.rate_limiter.get_current_rate()
                        rate_info = f"API: {current_rate}/{RATE_LIMIT_SAFE}"
                        
                        print(
                            f"📥 Progress: {self.stats['requests_processed']}/{self.stats['total_requests']} requests | "
                            f"{self.stats['total_entries']:,} entries | "
                            f"{rate:.1f} req/s | "
                            f"{rate_info} | "
                            f"{self.stats['errors']} failed",
                            end="\r",
                            flush=True
                        )
                    last_update = current_time
        
        print()
        print("-" * 70)
        
        # Upload remaining entries
        if self.upload and self.all_entries:
            print(f"📤 Uploading final batch of {len(self.all_entries)} entries...")
            success = upload_leaderboard_batch(self.uploader, self.all_entries)
            with self.lock:
                if success:
                    self.stats['db_uploads'] += len(self.all_entries)
                    print(f"✅ Final batch uploaded successfully")
                else:
                    self.stats['db_upload_errors'] += len(self.all_entries)
                    print(f"❌ Final batch upload failed")
            self.all_entries = []
        
        # Retry failed requests (for non-batch mode)
        if self.failed_requests:
            self._retry_failed_requests(max_retries=2)
        
        self.stats['end_time'] = datetime.now()
    
    def _retry_failed_requests(self, max_retries: int = 2, batch_label: str = ""):
        """
        Retry failed requests with exponential backoff
        
        Args:
            max_retries: Maximum number of retry attempts
            batch_label: Optional label for batch (e.g., "Batch 1")
        """
        retry_attempt = 1
        label_str = f" ({batch_label})" if batch_label else ""
        
        while self.failed_requests and retry_attempt <= max_retries:
            print()
            print(f"🔄 Retry attempt {retry_attempt}/{max_retries}{label_str}")
            print(f"   Retrying {len(self.failed_requests)} failed requests...")
            
            # Get failed requests and clear the list
            with self.lock:
                requests_to_retry = list(self.failed_requests)
                self.failed_requests = []
            
            # Wait before retrying
            if retry_attempt > 1:
                wait_time = 2 ** (retry_attempt - 1)
                print(f"   Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
            
            # Retry with fewer workers
            retry_workers = max(1, self.max_workers // 2)
            retry_successes = 0
            
            with ThreadPoolExecutor(max_workers=retry_workers) as executor:
                future_to_request = {}
                for i, request in enumerate(requests_to_retry, 1):
                    category, time_period, order_by, user, user_name = request
                    
                    future = executor.submit(
                        self.process_single_request,
                        category,
                        time_period,
                        order_by,
                        i,
                        len(requests_to_retry),
                        user,
                        user_name
                    )
                    future_to_request[future] = request
                
                for future in as_completed(future_to_request):
                    request = future_to_request[future]
                    
                    try:
                        entries_count, is_error = future.result()
                        
                        with self.lock:
                            self.stats['retried_requests'] += 1
                        
                        if not is_error:
                            retry_successes += 1
                            with self.lock:
                                self.stats['retry_successes'] += 1
                        
                    except Exception as e:
                        with self.lock:
                            self.stats['retried_requests'] += 1
                            self.failed_requests.append(request)
            
            print(f"   ✓ Retry {retry_attempt} completed: {retry_successes} recovered")
            retry_attempt += 1
        
        # Final summary
        with self.lock:
            final_failures = len(self.failed_requests)
            if final_failures > 0:
                print(f"   ⚠️  {final_failures} requests still failed after {max_retries} retries")
            else:
                print(f"   ✅ All failed requests recovered!")
        
        print("-" * 70)
    
    def print_summary(self):
        """Print summary statistics"""
        duration = (self.stats['end_time'] - self.stats['start_time']).total_seconds()
        
        print("\n" + "=" * 70)
        print("📊 PROCESSING SUMMARY")
        print("=" * 70)
        print(f"⏱️  Duration: {duration:.2f} seconds ({duration/60:.1f} minutes)")
        print(f"📝 Total requests processed: {self.stats['total_requests']}")
        print(f"✅ Successfully processed: {self.stats['requests_processed']}")
        print(f"💼 Total entries fetched: {self.stats['total_entries']:,}")
        print(f"❌ Errors: {self.stats['errors']}")
        
        if self.stats['retried_requests'] > 0:
            print(f"\n🔄 Retry Statistics:")
            print(f"   • Total retry attempts: {self.stats['retried_requests']}")
            print(f"   • Successfully recovered: {self.stats['retry_successes']}")
            print(f"   • Still failed: {len(self.failed_requests)}")
        
        if self.upload:
            print(f"\n💾 Database Upload:")
            print(f"   • Successfully uploaded: {self.stats['db_uploads']:,}")
            if self.stats['db_upload_errors'] > 0:
                print(f"   • Upload errors: {self.stats['db_upload_errors']:,}")
        
        if duration > 0:
            print(f"\n⚡ Speed:")
            print(f"   • {self.stats['requests_processed']/duration:.2f} requests/second")
            print(f"   • {self.stats['total_entries']/duration:.1f} entries/second")
        
        if self.all_entries and not self.upload:
            print(f"\n📋 Sample Entries (first 5):")
            for i, entry in enumerate(self.all_entries[:5], 1):
                print(f"   {i}. Rank #{entry['rank']} - {entry['user_name'] or entry['proxy_wallet'][:10]+'...'}")
                print(f"      Category: {entry['category']}, Period: {entry['time_period']}")
                print(f"      PnL: ${entry['pnl']:.2f}, Volume: ${entry['vol']:.2f}")
        
        print("=" * 70)
    
    def process_wallets_from_db(
        self,
        use_local_db: bool = False,
        wallet_limit: Optional[int] = None,
        category: str = "OVERALL",
        time_period: str = "DAY",
        order_by: str = "PNL"
    ):
        """
        Process wallets from database in batches
        
        Args:
            use_local_db: Use local PostgreSQL
            wallet_limit: Limit number of wallets to process
            category: Market category
            time_period: Time period
            order_by: Sort criteria
        """
        self.stats['start_time'] = datetime.now()
        
        print("\n" + "=" * 70)
        print("🚀 FETCHING LEADERBOARD FOR WALLETS FROM DATABASE")
        print("=" * 70)
        print(f"   • Category: {category}")
        print(f"   • Time Period: {time_period}")
        print(f"   • Order By: {order_by}")
        print(f"   • Parallel workers: {self.max_workers}")
        print(f"   • Upload to DB: {'YES' if self.upload else 'NO (preview only)'}")
        if self.upload:
            print(f"   • Database: {'Local PostgreSQL' if use_local_db else 'Supabase'}")
        print("=" * 70)
        print()
        
        # Get wallet generator
        batch_generator = get_unique_wallets_from_db_generator(
            use_local_db=use_local_db,
            limit=wallet_limit,
            batch_size=DB_FETCH_BATCH_SIZE
        )
        
        total_wallets_processed = 0
        batch_count = 0
        last_update = time.time()
        
        print("\n" + "=" * 70)
        print("🚀 STARTING BATCH PROCESSING")
        print("=" * 70)
        
        for wallet_batch in batch_generator:
            if not wallet_batch:
                continue
            
            batch_count += 1
            
            print(f"\n{'='*70}")
            print(f"📦 Processing Batch {batch_count} ({len(wallet_batch):,} wallets)")
            print(f"{'='*70}")
            
            # Create requests for this batch
            requests = [
                (category, time_period, order_by, wallet, None)
                for wallet in wallet_batch
            ]
            
            self.stats['total_requests'] += len(requests)
            
            # Process batch in parallel
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_request = {}
                for i, request in enumerate(requests, 1):
                    cat, period, order, user, user_name = request
                    
                    future = executor.submit(
                        self.process_single_request,
                        cat,
                        period,
                        order,
                        i,
                        len(requests),
                        user,
                        user_name
                    )
                    future_to_request[future] = request
                
                # Process results as they complete
                for future in as_completed(future_to_request):
                    request = future_to_request[future]
                    
                    try:
                        entries_count, is_error = future.result()
                        
                        with self.lock:
                            self.stats['requests_processed'] += 1
                        
                    except Exception as e:
                        with self.lock:
                            self.stats['requests_processed'] += 1
                            self.stats['errors'] += 1
                            self.failed_requests.append(request)
                    
                    # Progress update
                    current_time = time.time()
                    if current_time - last_update >= 0.5:
                        with self.lock:
                            elapsed = (datetime.now() - self.stats['start_time']).total_seconds()
                            rate = self.stats['requests_processed'] / elapsed if elapsed > 0 else 0
                            
                            # Show rate limiter status
                            current_rate = self.rate_limiter.get_current_rate()
                            rate_info = f"API: {current_rate}/{RATE_LIMIT_SAFE}"
                            
                            print(
                                f"📥 Progress: {self.stats['requests_processed']}/{self.stats['total_requests']} wallets | "
                                f"{self.stats['total_entries']:,} entries | "
                                f"{rate:.1f} req/s | "
                                f"{rate_info} | "
                                f"{self.stats['errors']} failed",
                                end="\r",
                                flush=True
                            )
                        last_update = current_time
            
            total_wallets_processed += len(wallet_batch)
            print(f"\n✅ Batch {batch_count} completed. Total wallets processed so far: {total_wallets_processed:,}")
            
            # Retry failed requests immediately after this batch
            if self.failed_requests:
                batch_failed_count = len(self.failed_requests)
                print(f"\n🔄 Batch {batch_count}: {batch_failed_count} failed requests detected, retrying immediately...")
                self._retry_failed_requests(max_retries=2, batch_label=f"Batch {batch_count}")
        
        print()
        print("-" * 70)
        
        # Upload remaining entries
        if self.upload and self.all_entries:
            print(f"📤 Uploading final batch of {len(self.all_entries)} entries...")
            success = upload_leaderboard_batch(self.uploader, self.all_entries)
            with self.lock:
                if success:
                    self.stats['db_uploads'] += len(self.all_entries)
                    print(f"✅ Final batch uploaded successfully")
                else:
                    self.stats['db_upload_errors'] += len(self.all_entries)
                    print(f"❌ Final batch upload failed")
            self.all_entries = []
        
        # Final retry for any remaining failed requests (shouldn't happen if batch retries worked)
        if self.failed_requests:
            print(f"\n⚠️ Found {len(self.failed_requests)} remaining failed requests after all batches")
            print(f"🔄 Attempting final retry...")
            self._retry_failed_requests(max_retries=2, batch_label="Final")
        
        self.stats['end_time'] = datetime.now()
        
        print("\n" + "=" * 70)
        print("🏁 ALL BATCHES COMPLETED")
        print("=" * 70)
    
    def close(self):
        """Clean up resources"""
        self.client.close()


# ==========================================
# REQUEST BUILDERS
# ==========================================

def build_requests_from_args(args) -> List[Tuple]:
    """
    Build list of requests based on command-line arguments
    
    Returns:
        List of tuples: (category, time_period, order_by, user, user_name)
    """
    requests = []
    
    # Handle specific users or usernames
    if args.users or args.usernames:
        # Single request with user filter
        categories = [args.category]
        time_periods = [args.time_period]
        order_bys = [args.order_by]
        
        for cat, period, order in product(categories, time_periods, order_bys):
            if args.users:
                for user in args.users:
                    requests.append((cat, period, order, user, None))
            if args.usernames:
                for username in args.usernames:
                    requests.append((cat, period, order, None, username))
        
        return requests
    
    # Build matrix of requests
    if args.full_matrix:
        # All combinations
        categories = CATEGORIES
        time_periods = TIME_PERIODS
        order_bys = ORDER_BY_OPTIONS
    elif args.all_categories and args.all_time_periods:
        # All categories × all periods
        categories = CATEGORIES
        time_periods = TIME_PERIODS
        order_bys = [args.order_by]
    elif args.all_categories:
        # All categories, single period
        categories = CATEGORIES
        time_periods = [args.time_period]
        order_bys = [args.order_by]
    elif args.all_time_periods:
        # All periods, single category
        categories = [args.category]
        time_periods = TIME_PERIODS
        order_bys = [args.order_by]
    else:
        # Single combination
        categories = [args.category]
        time_periods = [args.time_period]
        order_bys = [args.order_by]
    
    # Generate all combinations
    for cat, period, order in product(categories, time_periods, order_bys):
        requests.append((cat, period, order, None, None))
    
    return requests


# ==========================================
# CLI INTERFACE
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description='Parallel fetch of trader leaderboard from Polymarket API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview mode (default: OVERALL, ALL, PNL)
  python fetch_trader_leaderboard_parallel.py --local
  
  # ⭐ Fetch ranks for wallets from database (recommended, default ALL)
  python fetch_trader_leaderboard_parallel.py --upload --local --from-db
  
  # Test with limited wallets from database
  python fetch_trader_leaderboard_parallel.py --upload --local --from-db --limit-wallets 100
  
  # Top traders by volume in politics for the week
  python fetch_trader_leaderboard_parallel.py --upload --local --category POLITICS --time-period WEEK --order-by VOL
  
  # All categories for the month
  python fetch_trader_leaderboard_parallel.py --upload --local --all-categories --time-period MONTH
  
  # All time periods for crypto
  python fetch_trader_leaderboard_parallel.py --upload --local --category CRYPTO --all-time-periods
  
  # Full matrix (all categories × all periods × both orders)
  python fetch_trader_leaderboard_parallel.py --upload --local --full-matrix
  
  # Specific users by address
  python fetch_trader_leaderboard_parallel.py --local --users 0xabc123... 0xdef456...
  
  # Search by username
  python fetch_trader_leaderboard_parallel.py --local --usernames trader1 trader2
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
        '--category',
        type=str,
        choices=CATEGORIES,
        default='OVERALL',
        help='Market category for the leaderboard'
    )
    
    parser.add_argument(
        '--time-period',
        type=str,
        choices=TIME_PERIODS,
        default='ALL',
        help='Time period for leaderboard results (default: ALL - всё время)'
    )
    
    parser.add_argument(
        '--order-by',
        type=str,
        choices=ORDER_BY_OPTIONS,
        default='PNL',
        help='Leaderboard ordering criteria'
    )
    
    parser.add_argument(
        '--limit',
        type=int,
        default=DEFAULT_LIMIT_PER_REQUEST,
        help=f'Max entries per request (default: {DEFAULT_LIMIT_PER_REQUEST}, max: 50)'
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
        '--users',
        type=str,
        nargs='+',
        default=None,
        help='Filter by specific user addresses (space-separated)'
    )
    
    parser.add_argument(
        '--usernames',
        type=str,
        nargs='+',
        default=None,
        help='Filter by specific usernames (space-separated)'
    )
    
    parser.add_argument(
        '--all-categories',
        action='store_true',
        help='Fetch all categories'
    )
    
    parser.add_argument(
        '--all-time-periods',
        action='store_true',
        help='Fetch all time periods'
    )
    
    parser.add_argument(
        '--full-matrix',
        action='store_true',
        help='Fetch full matrix (all categories × all periods × both orders)'
    )
    
    parser.add_argument(
        '--from-db',
        action='store_true',
        help='⭐ Fetch leaderboard ranks for wallets from database (redemptions table)'
    )
    
    parser.add_argument(
        '--limit-wallets',
        type=int,
        default=None,
        help='Limit number of wallets to process from database (for testing)'
    )
    
    args = parser.parse_args()
    
    # Validate workers
    if args.workers > 15:
        print(f"⚠️  WARNING: {args.workers} workers may exceed API rate limits")
        print(f"   Recommended max: 10 workers")
        print(f"   Using {args.workers} anyway, but expect throttling...")
        print()
    
    # Validate limit
    if args.limit > 50:
        print(f"⚠️  WARNING: API max limit is 50, using 50 instead of {args.limit}")
        args.limit = 50
    
    print("\n" + "=" * 70)
    print("🎯 POLYMARKET TRADER LEADERBOARD FETCHER (PARALLEL)")
    print("=" * 70)
    print(f"Mode: {'UPLOAD' if args.upload else 'PREVIEW ONLY'}")
    if args.upload:
        print(f"Database: {'Local PostgreSQL' if args.local else 'Supabase'}")
    print(f"Entries per request: {args.limit}")
    print(f"Parallel workers: {args.workers}")
    
    # Check if using database wallets
    if args.from_db:
        print(f"\n⭐ Source: WALLETS FROM DATABASE (redemptions table)")
        if args.limit_wallets:
            print(f"   • Wallet limit: {args.limit_wallets:,}")
        print(f"   • Category: {args.category}")
        print(f"   • Time Period: {args.time_period}")
        print(f"   • Order By: {args.order_by}")
    else:
        # Show what will be fetched
        requests = build_requests_from_args(args)
        print(f"\n📋 Will fetch {len(requests)} leaderboard(s):")
        if len(requests) <= 10:
            for cat, period, order, user, user_name in requests:
                filter_str = f" (user: {user or user_name})" if user or user_name else ""
                print(f"   • {cat} / {period} / {order}{filter_str}")
        else:
            print(f"   • Multiple combinations (use --verbose for full list)")
    
    print("=" * 70)
    
    try:
        # Create parallel fetcher
        fetcher = ParallelLeaderboardFetcher(
            max_workers=args.workers,
            limit_per_request=args.limit,
            upload=args.upload,
            use_local_db=args.local,
            verbose=args.verbose
        )
        
        if args.from_db:
            # Process wallets from database
            fetcher.process_wallets_from_db(
                use_local_db=args.local,
                wallet_limit=args.limit_wallets,
                category=args.category,
                time_period=args.time_period,
                order_by=args.order_by
            )
        else:
            # Process regular requests
            requests = build_requests_from_args(args)
            fetcher.process_requests(requests)
        
        # Print summary
        fetcher.print_summary()
        
        # Cleanup
        fetcher.close()
        
        print("\n✅ Process completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
