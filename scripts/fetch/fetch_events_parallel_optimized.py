"""
OPTIMIZED Parallel fetcher with proper connection pooling
Fixes the bottleneck caused by too many sessions
Now includes rate limiting to respect API limits
"""

import json
import time
import os
import sys
from datetime import datetime
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Semaphore
from collections import deque
import requests
import fetch_events_config as config


class RateLimiter:
    """
    Thread-safe rate limiter using sliding window algorithm
    Ensures we don't exceed API rate limits
    """
    
    def __init__(self, max_requests: int = 500, window_seconds: int = 10):
        """
        Initialize rate limiter
        
        Args:
            max_requests: Maximum requests allowed per window (default: 500)
            window_seconds: Time window in seconds (default: 10)
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


class SharedPolymarketClient:
    """Shared client with connection pooling and rate limiting for all threads"""
    
    BASE_URL = "https://gamma-api.polymarket.com"
    
    def __init__(self, max_pool_connections: int = 50, rate_limiter: Optional[RateLimiter] = None):
        """
        Initialize with connection pooling and rate limiting
        
        Args:
            max_pool_connections: Max connections in pool (default: 50)
            rate_limiter: Optional rate limiter instance
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
    
    def get_events(self, 
                   limit: int = 10, 
                   offset: int = 0,
                   closed: Optional[bool] = None,
                   order: str = 'id',
                   ascending: bool = False) -> tuple[Optional[List[Dict]], Optional[str]]:
        """
        Fetch events (thread-safe with rate limiting)
        
        Returns:
            tuple: (events_list, error_message)
                - ([], None) = Empty result (valid, no more data)
                - ([...], None) = Success with data
                - (None, error) = Failed request
        """
        try:
            # Wait if necessary to respect rate limit
            if self.rate_limiter:
                self.rate_limiter.wait_if_needed()
            
            params = {
                'limit': limit, 
                'offset': offset, 
                'order': order, 
                'ascending': str(ascending).lower()
            }
            if closed is not None:
                params['closed'] = str(closed).lower()
            
            url = f"{self.BASE_URL}/events"
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


class OptimizedParallelEventFetcher:
    """Optimized parallel fetcher with shared connection pool and rate limiting"""
    
    def __init__(self, max_workers: int = 20, enable_rate_limiting: bool = True):
        """
        Initialize optimized fetcher
        
        Args:
            max_workers: Number of parallel threads (default: 20)
            enable_rate_limiting: Enable rate limiting to respect API limits (default: True)
        """
        self.max_workers = max_workers
        
        # Create rate limiter (500 req/10s, using 90% = 450 req/10s to be safe)
        self.rate_limiter = RateLimiter(max_requests=500, window_seconds=10) if enable_rate_limiting else None
        
        # Shared client with connection pool and rate limiter
        self.client = SharedPolymarketClient(
            max_pool_connections=max_workers + 10,
            rate_limiter=self.rate_limiter
        )
        
        self.stats = {
            'total_fetched': 0,
            'total_filtered': 0,
            'total_filtered_by_date': 0,
            'total_filtered_by_volume': 0,
            'total_filtered_by_status': 0,
            'total_markets_original': 0,
            'total_markets_filtered': 0,
            'pages_processed': 0,
            'start_time': None,
            'end_time': None,
            'failed_requests': 0,
            'retried_requests': 0,
            'retry_successes': 0
        }
        self.lock = Lock()
        self.all_events = []
        self.failed_offsets = []  # Track failed offsets for retry
    
    def fetch_all_events(self) -> List[Dict]:
        """Fetch all events using parallel requests with shared connection pool"""
        self.stats['start_time'] = datetime.now()
        
        print("🚀 PolyStars - OPTIMIZED Parallel Events Fetcher")
        print("=" * 70)
        print(f"📋 Configuration:")
        print(f"   • Minimum Event Volume: ${config.MIN_VOLUME:,.0f}")
        print(f"   • Minimum Market Volume: ${config.MIN_MARKET_VOLUME:,.0f}")
        print(f"   • Closed Only: {config.CLOSED_ONLY}")
        print(f"   • Resolution Status: {config.RESOLUTION_STATUS}")
        print(f"   • Batch Size: {config.BATCH_SIZE}")
        print(f"   • Parallel Workers: {self.max_workers}")
        print(f"   • Max Events: {config.MAX_EVENTS or 'Unlimited'}")
        print(f"   • Connection Pooling: ENABLED ✅")
        if self.rate_limiter:
            print(f"   • Rate Limiting: ENABLED ✅ (450/10s safe limit)")
        else:
            print(f"   • Rate Limiting: DISABLED ⚠️")
        
        if config.START_DATE or config.END_DATE:
            print(f"   • Date Range:")
            if config.START_DATE:
                print(f"      From: {config.START_DATE.strftime('%Y-%m-%d')}")
            if config.END_DATE:
                print(f"      To:   {config.END_DATE.strftime('%Y-%m-%d')}")
        else:
            print(f"   • Date Range: All dates")
        
        print("=" * 70)
        print()
        
        # Step 1: Fetch initial batch
        print("🔍 Determining data size...", end=" ", flush=True)
        initial_batch = self._fetch_single_page(0)
        if not initial_batch:
            print("❌ Failed to fetch initial batch")
            return []
        
        print(f"✓ Got {len(initial_batch)} events")
        
        # Process initial batch
        initial_events, initial_error = initial_batch
        if initial_error or not initial_events:
            print("❌ Failed to fetch initial batch")
            return []
        
        filtered_initial = self._filter_events(initial_events)
        with self.lock:
            self.all_events.extend(filtered_initial)
            self.stats['total_fetched'] += len(initial_events)
            self.stats['total_filtered'] += len(filtered_initial)
            self.stats['pages_processed'] += 1
        
        # Step 2: Create list of page offsets
        max_pages_estimate = 2000
        offsets = [
            offset for offset in range(config.BATCH_SIZE, max_pages_estimate * config.BATCH_SIZE, config.BATCH_SIZE)
        ]
        
        print(f"📊 Starting parallel fetch of up to {len(offsets)} pages...")
        print(f"⚡ Using {self.max_workers} parallel workers with shared connection pool")
        print("-" * 70)
        
        # Step 3: Fetch pages in parallel
        last_update = time.time()
        empty_results = 0
        error_results = 0
        total_futures = len(offsets)
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_offset = {
                executor.submit(self._fetch_and_filter_page, offset): offset 
                for offset in offsets
            }
            
            # Process results as they complete
            for future in as_completed(future_to_offset):
                offset = future_to_offset[future]
                
                try:
                    result, is_error = future.result()
                    
                    if is_error:
                        error_results += 1
                        with self.lock:
                            self.stats['failed_requests'] += 1
                            self.failed_offsets.append(offset)  # Track for retry
                    elif result is None:
                        empty_results += 1
                    
                except Exception as e:
                    with self.lock:
                        self.stats['failed_requests'] += 1
                        self.failed_offsets.append(offset)  # Track for retry
                    error_results += 1
                
                # Progress update
                current_time = time.time()
                if current_time - last_update >= 0.5:
                    with self.lock:
                        elapsed = (datetime.now() - self.stats['start_time']).total_seconds()
                        rate = self.stats['pages_processed'] / elapsed if elapsed > 0 else 0
                        
                        # Show rate limiter status if enabled
                        rate_info = ""
                        if self.rate_limiter:
                            current_rate = self.rate_limiter.get_current_rate()
                            rate_info = f" | API: {current_rate}/450"
                        
                        print(
                            f"📥 Progress: {self.stats['pages_processed']:,} pages | "
                            f"{self.stats['total_filtered']:,} matched | "
                            f"{rate:.1f} pages/s{rate_info} | "
                            f"{self.stats['failed_requests']} failed",
                            end="\r",
                            flush=True
                        )
                    last_update = current_time
                
                # Check max events limit
                if config.MAX_EVENTS:
                    with self.lock:
                        if len(self.all_events) >= config.MAX_EVENTS:
                            print(f"\n✅ Reached maximum events limit: {config.MAX_EVENTS}")
                            for f in future_to_offset:
                                f.cancel()
                            break
        
        print()
        print("-" * 70)
        print(f"📊 Completed processing {total_futures} page requests")
        successful = total_futures - empty_results - error_results
        print(f"   • Successful (with data): {successful}")
        print(f"   • Empty (no more data): {empty_results}")
        print(f"   • Failed (errors/timeouts): {error_results}")
        if error_results > 0:
            print(f"   ⚠️  WARNING: {error_results} requests failed - retrying...")
        print("-" * 70)
        
        # RETRY FAILED REQUESTS
        if self.failed_offsets:
            self._retry_failed_requests()
        
        self.stats['end_time'] = datetime.now()
        return self.all_events
    
    def _retry_failed_requests(self, max_retries: int = 3):
        """
        Retry failed requests with exponential backoff
        
        Args:
            max_retries: Maximum number of retry attempts per request
        """
        retry_attempt = 1
        
        while self.failed_offsets and retry_attempt <= max_retries:
            print()
            print(f"🔄 Retry attempt {retry_attempt}/{max_retries}")
            print(f"   Retrying {len(self.failed_offsets)} failed requests...")
            
            # Get failed offsets and clear the list
            with self.lock:
                offsets_to_retry = list(self.failed_offsets)
                self.failed_offsets = []
            
            # Exponential backoff: wait before retrying
            if retry_attempt > 1:
                wait_time = 2 ** (retry_attempt - 1)  # 2, 4, 8 seconds
                print(f"   Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
            
            # Retry with fewer workers to be more gentle
            retry_workers = min(10, self.max_workers // 2)
            retry_successes = 0
            last_update = time.time()
            
            with ThreadPoolExecutor(max_workers=retry_workers) as executor:
                future_to_offset = {
                    executor.submit(self._fetch_and_filter_page, offset): offset 
                    for offset in offsets_to_retry
                }
                
                for future in as_completed(future_to_offset):
                    offset = future_to_offset[future]
                    
                    try:
                        result, is_error = future.result()
                        
                        with self.lock:
                            self.stats['retried_requests'] += 1
                        
                        if is_error:
                            # Still failed, will retry again
                            with self.lock:
                                self.failed_offsets.append(offset)
                        else:
                            # Success!
                            retry_successes += 1
                            with self.lock:
                                self.stats['retry_successes'] += 1
                        
                        # Progress update
                        current_time = time.time()
                        if current_time - last_update >= 1.0:
                            with self.lock:
                                remaining = len(self.failed_offsets)
                                print(
                                    f"   📥 Retry progress: {retry_successes} succeeded, "
                                    f"{remaining} still failing...",
                                    end="\r",
                                    flush=True
                                )
                            last_update = current_time
                        
                    except Exception as e:
                        with self.lock:
                            self.stats['retried_requests'] += 1
                            self.failed_offsets.append(offset)
            
            print()
            print(f"   ✓ Retry {retry_attempt} completed: {retry_successes} recovered")
            
            retry_attempt += 1
        
        # Final summary
        with self.lock:
            final_failures = len(self.failed_offsets)
            if final_failures > 0:
                print(f"   ⚠️  {final_failures} requests still failed after {max_retries} retries")
            else:
                print(f"   ✅ All failed requests recovered!")
        
        print("-" * 70)
    
    def _fetch_single_page(self, offset: int) -> tuple[Optional[List[Dict]], bool]:
        """
        Fetch a single page using shared client
        
        Returns:
            tuple: (events_list, is_error)
                - ([], False) = Empty result (no more data)
                - ([...], False) = Success with data
                - (None, True) = Failed request (should retry)
        """
        events, error = self.client.get_events(
            limit=config.BATCH_SIZE,
            offset=offset,
            closed=config.CLOSED_ONLY,
            order='id',
            ascending=False
        )
        
        if error:
            # Failed request
            return (None, True)
        
        if not events or len(events) == 0:
            # Empty result (no more data)
            return (None, False)
        
        return (events, False)
    
    def _fetch_and_filter_page(self, offset: int) -> tuple[Optional[int], bool]:
        """
        Fetch and filter a single page (thread-safe)
        
        Returns:
            tuple: (filtered_count, is_error)
        """
        events, is_error = self._fetch_single_page(offset)
        
        if is_error:
            # Failed request - don't count as processed
            return (None, True)
        
        if events is None:
            # Empty result (end of data)
            return (None, False)
        
        filtered = self._filter_events(events)
        
        with self.lock:
            self.all_events.extend(filtered)
            self.stats['total_fetched'] += len(events)
            self.stats['total_filtered'] += len(filtered)
            self.stats['pages_processed'] += 1
        
        return (len(filtered), False)
    
    def _filter_events(self, events: List[Dict]) -> List[Dict]:
        """Filter events by configured criteria and filter markets by volume"""
        filtered = []
        
        for event in events:
            if not self._check_date_range(event):
                with self.lock:
                    self.stats['total_filtered_by_date'] += 1
                continue
            
            volume = self._get_volume(event)
            if volume < config.MIN_VOLUME:
                with self.lock:
                    self.stats['total_filtered_by_volume'] += 1
                continue
            
            if not self._check_resolution_status(event, config.RESOLUTION_STATUS):
                with self.lock:
                    self.stats['total_filtered_by_status'] += 1
                continue
            
            # Filter markets by volume if MIN_MARKET_VOLUME is set
            event = self._filter_markets_by_volume(event)
            
            filtered.append(event)
        
        return filtered
    
    def _check_date_range(self, event: Dict) -> bool:
        """Check if event is within configured date range"""
        if config.START_DATE is None and config.END_DATE is None:
            return True
        
        event_date = self._get_event_date(event)
        if event_date is None:
            return True
        
        if config.START_DATE and event_date < config.START_DATE:
            return False
        
        if config.END_DATE and event_date > config.END_DATE:
            return False
        
        return True
    
    def _get_event_date(self, event: Dict) -> Optional[datetime]:
        """Extract and parse event date"""
        end_date_str = event.get('endDate') or event.get('endDateIso')
        if not end_date_str:
            return None
        
        try:
            if 'T' in end_date_str:
                event_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            else:
                event_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            
            if event_date.tzinfo is not None:
                event_date = event_date.replace(tzinfo=None)
            
            return event_date
        except (ValueError, AttributeError):
            return None
    
    def _get_volume(self, event: Dict) -> float:
        """Extract volume from event"""
        volume = event.get('volume', 0)
        if isinstance(volume, str):
            try:
                volume = float(volume)
            except (ValueError, TypeError):
                volume = 0
        return float(volume) if volume else 0
    
    def _check_resolution_status(self, event: Dict, required_status: str) -> bool:
        """Check if event or its markets have required resolution status"""
        uma_status = event.get('umaResolutionStatus', '')
        if uma_status == required_status:
            return True
        
        markets = event.get('markets', [])
        if markets:
            for market in markets:
                market_uma_status = market.get('umaResolutionStatus', '')
                if market_uma_status == required_status:
                    return True
        
        return False
    
    def _filter_markets_by_volume(self, event: Dict) -> Dict:
        """
        Filter markets within an event by minimum volume
        Returns a copy of the event with filtered markets
        """
        if 'markets' not in event or not event['markets']:
            return event
        
        original_markets = event['markets']
        original_count = len(original_markets)
        
        # Filter markets by volume
        filtered_markets = []
        for market in original_markets:
            market_volume = self._get_volume(market)
            if market_volume >= config.MIN_MARKET_VOLUME:
                filtered_markets.append(market)
        
        # Update stats
        with self.lock:
            self.stats['total_markets_original'] += original_count
            self.stats['total_markets_filtered'] += len(filtered_markets)
        
        # Create a copy of the event with filtered markets
        event_copy = event.copy()
        event_copy['markets'] = filtered_markets
        
        return event_copy
    
    def print_summary(self):
        """Print summary statistics"""
        duration = (self.stats['end_time'] - self.stats['start_time']).total_seconds()
        
        print("\n" + "=" * 70)
        print("📈 SUMMARY")
        print("=" * 70)
        print(f"⏱️  Duration: {duration:.2f} seconds ({duration/60:.1f} minutes)")
        print(f"📄 Pages processed: {self.stats['pages_processed']:,}")
        print(f"📊 Total events fetched: {self.stats['total_fetched']:,}")
        print(f"✅ Events matched all filters: {self.stats['total_filtered']:,}")
        print(f"❌ Failed requests: {self.stats['failed_requests']}")
        
        if self.stats['retried_requests'] > 0:
            print(f"\n🔄 Retry Statistics:")
            print(f"   • Total retry attempts: {self.stats['retried_requests']:,}")
            print(f"   • Successfully recovered: {self.stats['retry_successes']:,}")
            print(f"   • Still failed: {len(self.failed_offsets):,}")
        
        if duration > 0:
            print(f"⚡ Speed: {self.stats['pages_processed']/duration:.1f} pages/s")
            print(f"   ({self.stats['total_fetched']/duration:.1f} events/s)")
        
        print(f"\n🔍 Filtering breakdown:")
        print(f"   • Excluded by date range: {self.stats['total_filtered_by_date']:,}")
        print(f"   • Excluded by volume: {self.stats['total_filtered_by_volume']:,}")
        print(f"   • Excluded by resolution status: {self.stats['total_filtered_by_status']:,}")
        
        if self.stats['total_markets_original'] > 0:
            markets_removed = self.stats['total_markets_original'] - self.stats['total_markets_filtered']
            markets_kept_pct = (self.stats['total_markets_filtered'] / self.stats['total_markets_original']) * 100
            print(f"\n📊 Markets filtering:")
            print(f"   • Original markets: {self.stats['total_markets_original']:,}")
            print(f"   • Markets kept (≥${config.MIN_MARKET_VOLUME}): {self.stats['total_markets_filtered']:,} ({markets_kept_pct:.1f}%)")
            print(f"   • Markets removed (<${config.MIN_MARKET_VOLUME}): {markets_removed:,}")
        
        if self.all_events:
            volumes = [self._get_volume(e) for e in self.all_events]
            print(f"\n💰 Volume Statistics:")
            print(f"   • Total: ${sum(volumes):,.2f}")
            print(f"   • Average: ${sum(volumes)/len(volumes):,.2f}")
            print(f"   • Max: ${max(volumes):,.2f}")
            print(f"   • Min: ${min(volumes):,.2f}")
            
            print(f"\n📋 Sample Events (first 5):")
            for i, event in enumerate(self.all_events[:5], 1):
                title = event.get('title', 'N/A')
                volume = self._get_volume(event)
                print(f"   {i}. {title[:60]}... (${volume:,.0f})")
        
        print("=" * 70)


def save_events_to_json(events: List[Dict], filename: str = None) -> str:
    """Save events to JSON file with metadata"""
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    
    if filename is None:
        if config.OUTPUT_FILENAME:
            filename = config.OUTPUT_FILENAME
        else:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'polymarket_events_optimized_{timestamp}.json'
    
    if not filename.startswith(config.OUTPUT_DIR):
        filename = os.path.join(config.OUTPUT_DIR, filename)
    
    output = {
        'metadata': {
            'timestamp': datetime.now().isoformat(),
            'total_events': len(events),
            'fetch_method': 'parallel_optimized',
            'filters': {
                'closed': config.CLOSED_ONLY,
                'min_event_volume': config.MIN_VOLUME,
                'min_market_volume': config.MIN_MARKET_VOLUME,
                'resolution_status': config.RESOLUTION_STATUS,
                'date_range': {
                    'start': config.START_DATE.isoformat() if config.START_DATE else None,
                    'end': config.END_DATE.isoformat() if config.END_DATE else None
                }
            }
        },
        'events': events
    }
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 Saved {len(events):,} events to: {filename}")
    
    file_size = os.path.getsize(filename)
    if file_size > 1_000_000:
        print(f"📦 File size: {file_size / 1_000_000:.2f} MB")
    else:
        print(f"📦 File size: {file_size / 1_000:.2f} KB")
    
    return filename


def main():
    """Main execution function"""
    # Parse command line arguments
    auto_upload = '--upload' in sys.argv or '-u' in sys.argv
    use_local_db = '--local' in sys.argv or '-l' in sys.argv
    show_help = '--help' in sys.argv or '-h' in sys.argv
    
    if show_help:
        print("Usage: python fetch_events_parallel_optimized.py [OPTIONS]")
        print()
        print("Options:")
        print("  --upload, -u       Upload events and markets to database")
        print("  --local, -l        Use local PostgreSQL instead of Supabase (requires --upload)")
        print("  --help, -h         Show this help message")
        print()
        print("Examples:")
        print("  python fetch_events_parallel_optimized.py")
        print("      Fetch only, save to JSON file")
        print()
        print("  python fetch_events_parallel_optimized.py --upload")
        print("      Fetch and upload to Supabase + save to JSON")
        print()
        print("  python fetch_events_parallel_optimized.py --upload --local")
        print("      Fetch and upload to local PostgreSQL + save to JSON")
        return
    
    if use_local_db and not auto_upload:
        print("⚠️  Warning: --local flag requires --upload flag")
        print("   Use: python fetch_events_parallel_optimized.py --upload --local")
        return
    
    print("🔬 Testing optimal worker count...")
    print()
    
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
            print()
        except Exception as e:
            print(f"❌ Failed to connect to {db_name}: {e}")
            print("   Continuing without upload...")
            auto_upload = False
            uploader = None
            print()
    
    # Start with 35 workers (optimized)
    fetcher = OptimizedParallelEventFetcher(max_workers=35)
    
    events = fetcher.fetch_all_events()
    fetcher.print_summary()
    
    if events:
        # Always save to JSON file
        filename = save_events_to_json(events)
        print(f"\n✅ Results saved to JSON: {filename}")
        
        # Upload to database if enabled
        if auto_upload and uploader:
            try:
                print()
                print("=" * 70)
                print("📤 UPLOADING TO DATABASE")
                print("=" * 70)
                
                # Upload events
                uploader.upload_events(events)
                
                # Upload markets
                uploader.upload_markets(events)
                
                # Upload metadata (Supabase only)
                if not use_local_db:
                    metadata = {
                        'timestamp': datetime.now().isoformat(),
                        'total_events': len(events),
                        'fetch_method': 'parallel_optimized',
                        'filters': {
                            'closed': config.CLOSED_ONLY,
                            'min_event_volume': config.MIN_VOLUME,
                            'min_market_volume': config.MIN_MARKET_VOLUME,
                            'resolution_status': config.RESOLUTION_STATUS,
                            'date_range': {
                                'start': config.START_DATE.isoformat() if config.START_DATE else None,
                                'end': config.END_DATE.isoformat() if config.END_DATE else None
                            }
                        }
                    }
                    uploader.upload_metadata(metadata)
                
                # Print upload summary
                uploader.print_summary()
                
                db_name = "local PostgreSQL" if use_local_db else "Supabase"
                print(f"\n✅ Successfully uploaded to {db_name}!")
                
            except Exception as upload_error:
                print(f"\n❌ Upload failed: {type(upload_error).__name__}")
                print(f"   Error: {str(upload_error)}")
                print(f"   Data is still saved in JSON: {filename}")
        
        print(f"\n🎉 Done!")
        if not auto_upload:
            print(f"💡 Tip: Use --upload flag to automatically upload to database")
    else:
        print("\n⚠️ No events found matching the criteria")


if __name__ == '__main__':
    main()

