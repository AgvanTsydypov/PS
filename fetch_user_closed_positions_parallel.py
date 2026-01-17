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

ТРЕБОВАНИЯ:
===========
- Python 3.8+
- pip install -r requirements.txt
- Файл .env с настройками (для БД)
- База данных с таблицей redemptions и events

API LIMITS:
===========
- Closed Positions API: 150 requests / 10 seconds
- Safe limit: 135 requests / 10 seconds (90% of max)
- Automatic rate limiting and retry on failures

Features:
- ⚡ Параллельная обработка пользователей (5 workers по умолчанию)
- 🔄 Автоматический rate limiting (135 req/10s safe limit)
- 🔁 Connection pooling для эффективного использования соединений
- 🔄 Автоматический retry при ошибках API
- 📦 Batch загрузка в БД (каждые 100 записей)
- 📊 Real-time прогресс и статистика
- 💾 Поддержка Supabase и локальной PostgreSQL
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

# Import the existing database uploader
from supabase_uploader import SupabaseUploader


# ==========================================
# CONFIGURATION
# ==========================================

API_BASE_URL = "https://data-api.polymarket.com/v1"
DEFAULT_WORKERS = 5  # Conservative default (API limit: 150/10s)
DEFAULT_POSITIONS_PER_USER = 50
BATCH_SIZE = 100  # How many records to upload to DB at once

# Rate limiting: 150 req/10s, use 90% = 135 req/10s
RATE_LIMIT_MAX = 150
RATE_LIMIT_WINDOW = 10
RATE_LIMIT_SAFE = int(RATE_LIMIT_MAX * 0.9)  # 135


# ==========================================
# RATE LIMITER
# ==========================================

class RateLimiter:
    """
    Thread-safe rate limiter using sliding window algorithm
    Ensures we don't exceed API rate limits
    """
    
    def __init__(self, max_requests: int = RATE_LIMIT_MAX, window_seconds: int = RATE_LIMIT_WINDOW):
        """
        Initialize rate limiter
        
        Args:
            max_requests: Maximum requests allowed per window
            window_seconds: Time window in seconds
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = deque()  # Store timestamps of requests
        self.lock = Lock()
        
        # Use 90% of limit to be safe
        self.safe_limit = int(max_requests * 0.9)
        
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
        sort_direction: str = "DESC"
    ) -> tuple[Optional[List[Dict]], Optional[str]]:
        """
        Fetch closed positions for a user (thread-safe with rate limiting)
        
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

def get_redeemers_from_db(use_local_db: bool = False, limit: Optional[int] = None) -> List[Dict]:
    """
    Query database using the SQL from lowest_100m_event_redeemers.sql
    Returns list of dicts with event_id, event_title, redeemer_address
    """
    print("=" * 70)
    print("📊 QUERYING DATABASE FOR REDEEMERS")
    print("=" * 70)
    
    # Read SQL query from file
    sql_file = "sql_q/lowest_100m_event_redeemers.sql"
    if not os.path.exists(sql_file):
        raise FileNotFoundError(f"SQL file not found: {sql_file}")
    
    with open(sql_file, 'r', encoding='utf-8') as f:
        sql_query = f.read()
    
    print(f"📄 Using SQL query from: {sql_file}")
    
    # Get database connection parameters
    uploader = SupabaseUploader(use_local_db=use_local_db)
    
    if use_local_db:
        # Use local PostgreSQL
        import psycopg2
        import psycopg2.extras
        
        print(f"🟢 Connecting to local PostgreSQL...")
        print(f"   Database: {uploader.connection_params['database']}")
        print(f"   Host: {uploader.connection_params['host']}:{uploader.connection_params['port']}")
        
        conn = psycopg2.connect(**uploader.connection_params)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        try:
            cursor.execute(sql_query)
            results = cursor.fetchall()
            
            # Convert to list of dicts
            redeemers = [dict(row) for row in results]
            
            print(f"✅ Found {len(redeemers)} redeemer records")
            
            if limit:
                redeemers = redeemers[:limit]
                print(f"⚠️  Limited to first {limit} redeemers")
            
            return redeemers
            
        finally:
            cursor.close()
            conn.close()
    
    else:
        print("⚠️  WARNING: Supabase mode requires local PostgreSQL for complex queries")
        print("   Please use --local flag")
        return []


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
                 verbose: bool = False):
        """
        Initialize parallel fetcher
        
        Args:
            max_workers: Number of parallel threads
            positions_per_user: Max positions to fetch per user
            upload: Whether to upload to database
            use_local_db: Use local PostgreSQL instead of Supabase
            verbose: Show detailed output
        """
        self.max_workers = max_workers
        self.positions_per_user = positions_per_user
        self.upload = upload
        self.use_local_db = use_local_db
        self.verbose = verbose
        
        # Create rate limiter (150 req/10s, using 90% = 135 req/10s to be safe)
        self.rate_limiter = RateLimiter(max_requests=RATE_LIMIT_MAX, window_seconds=RATE_LIMIT_WINDOW)
        
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
    
    def fetch_all_for_user(self, user_address: str) -> tuple[List[Dict], Optional[str]]:
        """
        Fetch all closed positions for a single user with pagination
        
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
                offset=offset
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
    
    def process_single_user(self, user_address: str, user_index: int, total_users: int) -> tuple[int, bool]:
        """
        Process a single user (fetch and optionally upload)
        
        Returns:
            tuple: (positions_count, is_error)
        """
        try:
            # Fetch positions
            positions, error = self.fetch_all_for_user(user_address)
            
            if error:
                # Failed request
                with self.lock:
                    self.stats['errors'] += 1
                    self.failed_users.append(user_address)
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
                self.failed_users.append(user_address)
            return (0, True)
    
    def process_all_users(self, user_addresses: List[str]):
        """
        Process all users in parallel
        """
        self.stats['start_time'] = datetime.now()
        self.stats['total_users'] = len(user_addresses)
        
        print("\n" + "=" * 70)
        print("🚀 PARALLEL FETCHING CLOSED POSITIONS")
        print("=" * 70)
        print(f"📋 Configuration:")
        print(f"   • Total users to process: {len(user_addresses)}")
        print(f"   • Positions per user: {self.positions_per_user}")
        print(f"   • Parallel workers: {self.max_workers}")
        print(f"   • Upload to DB: {'YES' if self.upload else 'NO (preview only)'}")
        if self.upload:
            print(f"   • Database: {'Local PostgreSQL' if self.use_local_db else 'Supabase'}")
        print(f"   • Connection Pooling: ENABLED ✅")
        print(f"   • Rate Limiting: ENABLED ✅ ({RATE_LIMIT_SAFE}/{RATE_LIMIT_WINDOW}s safe limit)")
        
        # Estimate time
        if self.positions_per_user > 100:
            requests_per_user = (self.positions_per_user // 50) + 1
            # Assuming rate limiter allows ~13.5 req/s (135/10s)
            req_per_second = RATE_LIMIT_SAFE / RATE_LIMIT_WINDOW
            seconds_per_user = requests_per_user / req_per_second
            total_seconds = (len(user_addresses) * seconds_per_user) / self.max_workers
            total_minutes = total_seconds / 60
            
            print(f"\n⏱️  ESTIMATED TIME:")
            print(f"   Requests per user: ~{requests_per_user}")
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
            future_to_user = {
                executor.submit(self.process_single_user, addr, i, len(user_addresses)): addr
                for i, addr in enumerate(user_addresses, 1)
            }
            
            # Process results as they complete
            for future in as_completed(future_to_user):
                user_address = future_to_user[future]
                
                try:
                    positions_count, is_error = future.result()
                    
                    with self.lock:
                        self.stats['users_processed'] += 1
                    
                except Exception as e:
                    with self.lock:
                        self.stats['users_processed'] += 1
                        self.stats['errors'] += 1
                        self.failed_users.append(user_address)
                
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
                            f"📥 Progress: {self.stats['users_processed']}/{self.stats['total_users']} users | "
                            f"{self.stats['total_positions']:,} positions | "
                            f"{rate:.1f} users/s | "
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
        Retry failed users with exponential backoff
        """
        retry_attempt = 1
        
        while self.failed_users and retry_attempt <= max_retries:
            print()
            print(f"🔄 Retry attempt {retry_attempt}/{max_retries}")
            print(f"   Retrying {len(self.failed_users)} failed users...")
            
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
                future_to_user = {
                    executor.submit(self.process_single_user, addr, i, len(users_to_retry)): addr
                    for i, addr in enumerate(users_to_retry, 1)
                }
                
                for future in as_completed(future_to_user):
                    user_address = future_to_user[future]
                    
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
                            self.failed_users.append(user_address)
            
            print(f"   ✓ Retry {retry_attempt} completed: {retry_successes} recovered")
            retry_attempt += 1
        
        # Final summary
        with self.lock:
            final_failures = len(self.failed_users)
            if final_failures > 0:
                print(f"   ⚠️  {final_failures} users still failed after {max_retries} retries")
            else:
                print(f"   ✅ All failed users recovered!")
        
        print("-" * 70)
    
    def print_summary(self):
        """Print summary statistics"""
        duration = (self.stats['end_time'] - self.stats['start_time']).total_seconds()
        
        print("\n" + "=" * 70)
        print("📊 PROCESSING SUMMARY")
        print("=" * 70)
        print(f"⏱️  Duration: {duration:.2f} seconds ({duration/60:.1f} minutes)")
        print(f"👥 Total users: {self.stats['total_users']}")
        print(f"✅ Users processed: {self.stats['users_processed']}")
        print(f"📊 Users with positions: {self.stats['users_with_positions']}")
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
            print(f"   • {self.stats['users_processed']/duration:.2f} users/second")
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
    
    args = parser.parse_args()
    
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
    print("=" * 70)
    
    try:
        # Step 1: Get redeemers from database
        redeemers = get_redeemers_from_db(
            use_local_db=args.local,
            limit=args.limit
        )
        
        if not redeemers:
            print("\n❌ No redeemers found in database")
            print("💡 Make sure:")
            print("   1. Your database is properly configured in .env")
            print("   2. The redemptions and events tables have data")
            print("   3. There are events with volume > 100,000,000")
            return
        
        # Get unique addresses
        unique_addresses = list(set([r['redeemer_address'] for r in redeemers]))
        print(f"\n📋 Unique redeemer addresses: {len(unique_addresses)}")
        
        # Step 2: Create parallel fetcher
        fetcher = ParallelPositionsFetcher(
            max_workers=args.workers,
            positions_per_user=args.positions,
            upload=args.upload,
            use_local_db=args.local,
            verbose=args.verbose
        )
        
        # Step 3: Process all users
        fetcher.process_all_users(unique_addresses)
        
        # Step 4: Print summary
        fetcher.print_summary()
        
        # Step 5: Cleanup
        fetcher.close()
        
        print("\n✅ Process completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
