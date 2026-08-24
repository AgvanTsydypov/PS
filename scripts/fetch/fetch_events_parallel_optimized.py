"""
Events fetcher for Gamma: GET /events/keyset (cursor pagination).

Older offset-based parallel paging against /events is incompatible with the
documented keyset API (no offset; follow next_cursor).
"""

import json
import time
import os
import sys
from datetime import datetime
from typing import List, Dict, Optional
from threading import Lock
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
    
    def get_events_keyset(
        self,
        limit: int = 100,
        after_cursor: Optional[str] = None,
        closed: Optional[bool] = None,
        end_date_min: Optional[str] = None,
        end_date_max: Optional[str] = None,
        volume_min: Optional[float] = None,
        volume_max: Optional[float] = None,
        order: str = 'id',
        ascending: bool = False,
    ) -> tuple[Optional[List[Dict]], Optional[str], Optional[str]]:
        """
        Fetch one page via Gamma keyset pagination (GET /events/keyset).

        Docs: offset is rejected on this endpoint; use after_cursor from the
        previous response's next_cursor.

        Returns:
            (events, next_cursor, error)
            - error set => failed request (events/next_cursor undefined)
            - error None => success; next_cursor None means last page
        """
        try:
            if self.rate_limiter:
                self.rate_limiter.wait_if_needed()

            params: Dict[str, object] = {
                'limit': min(max(limit, 1), 500),
                'order': order,
                'ascending': str(ascending).lower(),
            }
            if after_cursor is not None:
                params['after_cursor'] = after_cursor
            if closed is not None:
                params['closed'] = str(closed).lower()
            if end_date_min:
                params['end_date_min'] = end_date_min
            if end_date_max:
                params['end_date_max'] = end_date_max
            if volume_min is not None:
                params['volume_min'] = volume_min
            if volume_max is not None:
                params['volume_max'] = volume_max

            url = f"{self.BASE_URL}/events/keyset"
            response = self.session.get(url, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()

            if not isinstance(data, dict):
                return (None, None, f'unexpected JSON type: {type(data).__name__}')

            events = data.get('events') or []
            next_cursor = data.get('next_cursor')
            if next_cursor is not None and not isinstance(next_cursor, str):
                next_cursor = str(next_cursor)

            return (events, next_cursor, None)

        except Exception as e:
            return (None, None, str(e))

    def get_events_by_ids(self, ids: List[int]) -> tuple[Optional[List[Dict]], Optional[str]]:
        """
        Fetch specific events by id (used for Genesis force-include list).
        Bypasses date/volume/status filters — caller is asserting these are wanted.
        """
        if not ids:
            return ([], None)
        try:
            if self.rate_limiter:
                self.rate_limiter.wait_if_needed()
            params: Dict[str, object] = {
                'limit': min(max(len(ids), 1), 500),
                'id': list(ids),
            }
            url = f"{self.BASE_URL}/events/keyset"
            response = self.session.get(url, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                return (None, f'unexpected JSON type: {type(data).__name__}')
            return (data.get('events') or [], None)
        except Exception as e:
            return (None, str(e))


class OptimizedParallelEventFetcher:
    """Gamma /events/keyset fetcher (sequential cursors) with pooled HTTP and rate limits."""

    def __init__(self, max_workers: int = 20, enable_rate_limiting: bool = True):
        """
        Args:
            max_workers: Connection pool sizing hint (pagination is sequential).
            enable_rate_limiting: Respect Gamma rate limits (default: True).
        """
        self.max_workers = max_workers
        
        # Create rate limiter (500 req/10s, using 90% = 450 req/10s to be safe)
        self.rate_limiter = RateLimiter(max_requests=500, window_seconds=10) if enable_rate_limiting else None
        
        # Shared client with connection pool and rate limiter
        self.client = SharedPolymarketClient(
            max_pool_connections=max_workers + 10,
            rate_limiter=self.rate_limiter
        )
        
        self.is_genesis = os.getenv('POLYSTARS_IS_GENESIS', 'false').strip().lower() == 'true'

        self.stats = {
            'total_fetched': 0,
            'total_filtered': 0,
            'total_filtered_by_date': 0,
            'total_filtered_by_volume': 0,
            'total_filtered_by_max_volume': 0,
            'total_filtered_by_status': 0,
            'total_filtered_by_genesis_exclude': 0,
            'total_added_by_genesis_include': 0,
            'total_markets_original': 0,
            'total_markets_filtered': 0,
            'pages_processed': 0,
            'start_time': None,
            'end_time': None,
            'failed_requests': 0,
        }
        self.lock = Lock()
        self.all_events = []
    
    def fetch_all_events(self) -> List[Dict]:
        """Fetch all events via GET /events/keyset (sequential cursor chain)."""
        self.stats['start_time'] = datetime.now()

        print("🚀 PS - Events fetcher (Gamma /events/keyset)")
        print("=" * 70)
        print(f"📋 Configuration:")
        print(f"   • Minimum Event Volume: ${config.MIN_VOLUME:,.0f}")
        if config.MAX_VOLUME:
            print(f"   • Maximum Event Volume: ${config.MAX_VOLUME:,.0f} ⚠️ (Testing filter)")
        print(f"   • Minimum Market Volume: ${config.MIN_MARKET_VOLUME:,.0f}")
        print(f"   • Closed Only: {config.CLOSED_ONLY}")
        print(f"   • Resolution Status: {config.RESOLUTION_STATUS}")
        print(f"   • Page size: 500 (keyset max; fewer round-trips)")
        print(f"   • Max Events: {config.MAX_EVENTS or 'Unlimited'}")
        print(f"   • Connection Pooling: ENABLED ✅")
        if self.rate_limiter:
            print(f"   • Rate Limiting: ENABLED ✅ (450/10s safe limit)")
        else:
            print(f"   • Rate Limiting: DISABLED ⚠️")

        use_server_filters = os.getenv("POLYSTARS_KEYSET_USE_SERVER_FILTERS", "1") not in ("0", "false", "False")

        server_end_date_min: Optional[str] = None
        server_end_date_max: Optional[str] = None
        server_volume_min: Optional[float] = None
        server_volume_max: Optional[float] = None
        if use_server_filters:
            if config.START_DATE:
                server_end_date_min = config.START_DATE.strftime('%Y-%m-%dT%H:%M:%SZ')
            if config.END_DATE:
                server_end_date_max = config.END_DATE.strftime('%Y-%m-%dT%H:%M:%SZ')
            if config.MIN_VOLUME:
                server_volume_min = float(config.MIN_VOLUME)
            if config.MAX_VOLUME:
                server_volume_max = float(config.MAX_VOLUME)

        if config.START_DATE or config.END_DATE:
            label = "SERVER-SIDE" if use_server_filters else "CLIENT-SIDE (server filters disabled via env)"
            print(f"   • Date Range ({label}):")
            if config.START_DATE:
                print(f"      From: {config.START_DATE.strftime('%Y-%m-%d')}")
            if config.END_DATE:
                print(f"      To:   {config.END_DATE.strftime('%Y-%m-%d')}")
        else:
            print(f"   • Date Range: All dates")
        if use_server_filters:
            print(f"   • Server filters: end_date_min/max + volume_min/max (POLYSTARS_KEYSET_USE_SERVER_FILTERS=1)")
            print(f"   • Order: endDate ascending (early-stop friendly)")
        else:
            print(f"   • Server filters: DISABLED (POLYSTARS_KEYSET_USE_SERVER_FILTERS=0) — full id-desc scan")

        print("=" * 70)
        print()

        page_limit = 500
        cursor: Optional[str] = None
        last_progress = time.time()
        max_pages = int(os.getenv("POLYSTARS_KEYSET_MAX_PAGES", "100000"))
        keyset_order = 'endDate' if use_server_filters else 'id'
        keyset_ascending = True if use_server_filters else False

        print("🔍 Fetching pages (keyset cursor chain)...", flush=True)
        print("-" * 70)

        while True:
            if config.MAX_EVENTS and len(self.all_events) >= config.MAX_EVENTS:
                print(f"\n✅ Reached maximum events limit: {config.MAX_EVENTS}")
                break

            if self.stats['pages_processed'] >= max_pages:
                print(f"\n⚠️ Stopped: POLYSTARS_KEYSET_MAX_PAGES={max_pages} safety cap")
                break

            events, next_cursor, error = None, None, None
            for attempt in range(1, 5):
                events, next_cursor, error = self.client.get_events_keyset(
                    limit=page_limit,
                    after_cursor=cursor,
                    closed=config.CLOSED_ONLY,
                    end_date_min=server_end_date_min,
                    end_date_max=server_end_date_max,
                    volume_min=server_volume_min,
                    volume_max=server_volume_max,
                    order=keyset_order,
                    ascending=keyset_ascending,
                )
                if error is None:
                    break
                with self.lock:
                    self.stats['failed_requests'] += 1
                time.sleep(min(0.5 * (2 ** (attempt - 1)), 6.0))

            if error is not None:
                print(f"\n❌ Stopped after repeated API errors at cursor={cursor!r}")
                print(f"   Last error: {error}")
                break

            assert events is not None
            if len(events) == 0 and not next_cursor:
                break

            filtered = self._filter_events(events)
            with self.lock:
                self.all_events.extend(filtered)
                self.stats['total_fetched'] += len(events)
                self.stats['total_filtered'] += len(filtered)
                self.stats['pages_processed'] += 1

            now = time.time()
            if now - last_progress >= 0.5:
                elapsed = (datetime.now() - self.stats['start_time']).total_seconds()
                rate = self.stats['pages_processed'] / elapsed if elapsed > 0 else 0
                rate_info = ""
                if self.rate_limiter:
                    rate_info = f" | API: {self.rate_limiter.get_current_rate()}/450"
                print(
                    f"📥 Progress: {self.stats['pages_processed']:,} pages | "
                    f"{self.stats['total_filtered']:,} matched | "
                    f"{rate:.2f} pages/s{rate_info} | "
                    f"{self.stats['failed_requests']} failed retries",
                    end="\r",
                    flush=True,
                )
                last_progress = now

            # Do NOT stop based on endDate vs START_DATE while paginating by id: id order
            # is not monotone with endDate (e.g. a page of pre-2024 outcomes can appear
            # before later pages that still contain 2024–2026 events with lower ids).

            if not next_cursor:
                break
            cursor = next_cursor

        print()
        print("-" * 70)
        print(f"📊 Completed {self.stats['pages_processed']:,} keyset page(s)")
        if self.stats['failed_requests']:
            print(f"   • Failed request attempts (retried): {self.stats['failed_requests']}")
        print("-" * 70)

        if self.is_genesis and config.GENESIS_INCLUDE_EVENT_IDS:
            existing_ids = {
                self._get_event_id(e) for e in self.all_events
            }
            existing_ids.discard(None)
            missing = sorted(
                set(config.GENESIS_INCLUDE_EVENT_IDS) - existing_ids
            )
            if missing:
                print(f"\n🧬 Genesis force-include: fetching {len(missing)} extra event(s) by id...")
                extras, err = self.client.get_events_by_ids(missing)
                if err:
                    print(f"   ⚠️  Force-include fetch failed: {err}")
                elif extras:
                    excluded_ids = config.GENESIS_EXCLUDE_EVENT_IDS or set()
                    added = 0
                    skipped_excluded: List[int] = []
                    skipped_out_of_window: List[int] = []
                    for ev in extras:
                        eid = self._get_event_id(ev)
                        if eid is None:
                            continue
                        if eid in excluded_ids:
                            skipped_excluded.append(eid)
                            continue
                        # Window check: out-of-Genesis-window force-includes will
                        # be picked up later via daily ingestion — don't pull twice.
                        if not self._check_date_range(ev):
                            skipped_out_of_window.append(eid)
                            continue
                        ev = self._filter_markets_by_volume(ev)
                        with self.lock:
                            self.all_events.append(ev)
                            self.stats['total_fetched'] += 1
                            self.stats['total_filtered'] += 1
                            self.stats['total_added_by_genesis_include'] += 1
                        added += 1
                    print(f"   ✅ Added {added} force-include event(s)")
                    if skipped_out_of_window:
                        print(
                            f"   ⏭️  Skipped {len(skipped_out_of_window)} force-include id(s) outside "
                            f"Genesis window (will be picked up by daily): {skipped_out_of_window}"
                        )
                    if skipped_excluded:
                        print(f"   ⏭️  Skipped {len(skipped_excluded)} force-include id(s) also in exclude list: {skipped_excluded}")
                    found_ids = {self._get_event_id(e) for e in extras}
                    not_found = [i for i in missing if i not in found_ids]
                    if not_found:
                        print(f"   ⚠️  Not returned by API: {not_found}")
                else:
                    print(f"   ⚠️  No events returned for force-include ids")
            else:
                print(f"\n🧬 Genesis force-include: all {len(config.GENESIS_INCLUDE_EVENT_IDS)} ids already present")

        # Apply MAX_EVENTS limit to final result
        # (Early stopping during pagination may have fetched more events in parallel)
        if config.MAX_EVENTS and len(self.all_events) > config.MAX_EVENTS:
            original_count = len(self.all_events)
            self.all_events = self.all_events[:config.MAX_EVENTS]
            self.stats['total_filtered'] = len(self.all_events)  # Update stats
            print(f"\n✂️  Trimmed to MAX_EVENTS limit: {original_count} → {config.MAX_EVENTS} events")
        
        self.stats['end_time'] = datetime.now()
        return self.all_events

    def _filter_events(self, events: List[Dict]) -> List[Dict]:
        """Filter events by configured criteria and filter markets by volume"""
        filtered = []
        
        for event in events:
            if self.is_genesis and config.GENESIS_EXCLUDE_EVENT_IDS:
                event_id = self._get_event_id(event)
                if event_id is not None and event_id in config.GENESIS_EXCLUDE_EVENT_IDS:
                    with self.lock:
                        self.stats['total_filtered_by_genesis_exclude'] += 1
                    continue

            if not self._check_date_range(event):
                with self.lock:
                    self.stats['total_filtered_by_date'] += 1
                continue
            
            volume = self._get_volume(event)
            if volume < config.MIN_VOLUME:
                with self.lock:
                    self.stats['total_filtered_by_volume'] += 1
                continue
            
            # Check maximum volume (for testing - exclude very large events)
            if config.MAX_VOLUME and volume > config.MAX_VOLUME:
                with self.lock:
                    self.stats['total_filtered_by_max_volume'] += 1
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
        """
        Check if event is within configured date range (FALLBACK)
        
        NOTE: With server-side filtering enabled, this should rarely filter anything.
        It serves as a safety net in case API returns unexpected data.
        """
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
    
    def _get_event_id(self, event: Dict) -> Optional[int]:
        """Extract numeric event id (Gamma returns it as string in some payloads)."""
        raw = event.get('id')
        if raw is None:
            return None
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None

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
        if not required_status:
            return True

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
        if self.stats['end_time'] is None:
            self.stats['end_time'] = datetime.now()
        
        duration = (self.stats['end_time'] - self.stats['start_time']).total_seconds()
        
        print("\n" + "=" * 70)
        print("📈 SUMMARY")
        print("=" * 70)
        print(f"⏱️  Duration: {duration:.2f} seconds ({duration/60:.1f} minutes)")
        print(f"📄 Pages processed: {self.stats['pages_processed']:,}")
        print(f"📊 Total events fetched: {self.stats['total_fetched']:,}")
        print(f"✅ Events matched all filters: {self.stats['total_filtered']:,}")
        print(f"❌ Failed request attempts (before success): {self.stats['failed_requests']}")
        
        if duration > 0:
            print(f"⚡ Speed: {self.stats['pages_processed']/duration:.1f} pages/s")
            print(f"   ({self.stats['total_fetched']/duration:.1f} events/s)")
        
        print(f"\n🔍 Filtering breakdown:")
        print(f"   • Excluded by date range: {self.stats['total_filtered_by_date']:,}")
        print(f"   • Excluded by min volume (<${config.MIN_VOLUME:,.0f}): {self.stats['total_filtered_by_volume']:,}")
        if config.MAX_VOLUME:
            print(f"   • Excluded by max volume (>${config.MAX_VOLUME:,.0f}): {self.stats['total_filtered_by_max_volume']:,}")
        print(f"   • Excluded by resolution status: {self.stats['total_filtered_by_status']:,}")
        if self.is_genesis:
            print(f"   • Excluded by Genesis exclude list: {self.stats['total_filtered_by_genesis_exclude']:,}")
            print(f"   • Added by Genesis include list: {self.stats['total_added_by_genesis_include']:,}")
        
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
                'max_event_volume': config.MAX_VOLUME,
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
        print("  --upload, -u       Upload events and markets to PostgreSQL")
        print("  --help, -h         Show this help message")
        print()
        print("Examples:")
        print("  python fetch_events_parallel_optimized.py")
        print("      Fetch only, save to JSON file")
        print()
        print("  python fetch_events_parallel_optimized.py --upload")
        print("      Fetch and upload to PostgreSQL + save to JSON")
        return
    
    print()
    
    # Initialize database uploader if auto_upload is enabled
    uploader = None
    if auto_upload:
        print(f"🔄 Auto-upload to PostgreSQL enabled")
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            parent_dir = os.path.dirname(script_dir)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)

            from db.db_uploader import DbUploader as SupabaseUploader
            uploader = SupabaseUploader()
            print(f"✅ Connected to PostgreSQL")
            print()
        except Exception as e:
            print(f"❌ Failed to connect to PostgreSQL: {e}")
            print("   Continuing without upload...")
            auto_upload = False
            uploader = None
            print()
    
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
                
                uploader.upload_events(events)
                uploader.upload_markets(events)
                uploader.print_summary()
                print(f"\n✅ Successfully uploaded to PostgreSQL!")
                
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

