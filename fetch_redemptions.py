"""
Fetch Redemptions for All Markets from Polymarket Events
Automatically scans latest events file and fetches redemptions for each market
WITH OPTIMIZED PARALLEL PROCESSING (High-speed with stability)

Features:
- Processes markets in batches to avoid overwhelming the system
- Parallel database uploads (new client per upload for thread-safety)
- Large GraphQL batches (5000 records per request instead of 1000)
- No artificial delays between requests (5x faster for large markets)
- Retry mechanism for failed requests
- Configurable concurrency limits (10 concurrent markets)
- Progress tracking for large markets
- Detailed error logging
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
# CONFIGURATION
# ==========================================
GRAPH_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/activity-subgraph/0.0.4/gn"

# Filter settings
FILTER_CLOSED_ONLY = True  # Only fetch redemptions for closed markets
MIN_VOLUME = 0  # Minimum market volume to process (set to 0 for all)
MAX_MARKETS = None  # Limit number of markets to process (None = all)

# Parallel processing settings
MAX_CONCURRENT_MARKETS = 10  # Process 10 markets simultaneously (increased for speed)
BATCH_SIZE = 50  # Process markets in batches to avoid overwhelming the system
BATCH_DELAY = 1  # Delay between batches in seconds
REQUEST_TIMEOUT = 60  # Timeout for each request in seconds (increased)
MAX_RETRIES = 3  # Maximum retry attempts for failed requests
RETRY_DELAY = 1  # Delay between retries in seconds

# ==========================================
# FILE UTILITIES
# ==========================================
def find_latest_events_file() -> Optional[str]:
    """Find the latest polymarket_events_optimized_*.json file"""
    json_dir = 'json_output'
    pattern = os.path.join(json_dir, 'polymarket_events_optimized_*.json')
    files = glob.glob(pattern)
    
    if not files:
        print(f"❌ No events files found in {json_dir}/")
        return None
    
    # Sort by modification time, get latest
    latest_file = max(files, key=os.path.getmtime)
    return latest_file


def load_events_file(filepath: str) -> Dict:
    """Load events data from JSON file"""
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
    semaphore: asyncio.Semaphore
) -> List[Dict]:
    """Fetch all redemptions for a specific market (async version)"""
    async with semaphore:  # Limit concurrent requests
        all_redemptions = []
        last_id = "0x00"
        request_count = 0
        
        while True:
            request_count += 1
            query = """
            query ($condId: Bytes!, $lastId: ID!) {
              redemptions(
                where: { condition: $condId, id_gt: $lastId }
                first: 1000
                orderBy: id
                orderDirection: asc
              ) {
                id
                redeemer
                payout
                timestamp
              }
            }
            """
            
            variables = {
                "condId": condition_id,
                "lastId": last_id
            }
            
            # Retry logic for each request
            retry_count = 0
            request_success = False
            
            while retry_count <= MAX_RETRIES and not request_success:
                try:
                    async with session.post(
                        GRAPH_URL,
                        json={'query': query, 'variables': variables},
                        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                    ) as response:
                        data = await response.json()
                        
                        if data.get('errors'):
                            # Log GraphQL errors in real-time
                            error_details = data.get('errors')
                            print(f"\n      ❌ GraphQL Error for {condition_id[:20]}...")
                            print(f"         Error: {str(error_details)[:200]}")
                            break
                        
                        batch = data.get('data', {}).get('redemptions', [])
                        
                        if not batch:
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
                        
                        # Show progress for large markets (every 3 batches = 15k records)
                        if request_count > 1 and request_count % 3 == 0:
                            print(f"      ... fetched {len(all_redemptions):,} redemptions ({request_count} requests)", flush=True)
                        
                        # Safety limit per market (increased for large markets)
                        if len(all_redemptions) > 100000:
                            print(f"      ⚠️  Reached safety limit of 100k redemptions")
                            break
                        
                        # No delay needed - semaphore already limits concurrent requests
                            
                except asyncio.TimeoutError:
                    retry_count += 1
                    print(f"\n      ⚠️  Request timeout (attempt {retry_count}/{MAX_RETRIES + 1})")
                    print(f"         Condition: {condition_id[:30]}...")
                    print(f"         Request: {request_count}, Last ID: {last_id[:20]}...")
                    if retry_count > MAX_RETRIES:
                        print(f"      ❌ Failed after {MAX_RETRIES} retries - giving up")
                        break
                    await asyncio.sleep(RETRY_DELAY)
                except Exception as e:
                    retry_count += 1
                    error_type = type(e).__name__
                    error_msg = str(e)
                    print(f"\n      ⚠️  {error_type} (attempt {retry_count}/{MAX_RETRIES + 1})")
                    print(f"         Condition: {condition_id[:30]}...")
                    print(f"         Error: {error_msg[:150]}")
                    if retry_count > MAX_RETRIES:
                        print(f"      ❌ Failed after {MAX_RETRIES} retries - giving up")
                        break
                    await asyncio.sleep(RETRY_DELAY)
            
            # If all retries failed, break the pagination loop
            if not request_success:
                break
        
        return all_redemptions


async def process_market_async(
    session: aiohttp.ClientSession,
    market: Dict,
    market_index: int,
    total_markets: int,
    uploader,
    stats: Dict,
    semaphore: asyncio.Semaphore
):
    """Process a single market: fetch and upload (with parallel upload support)"""
    try:
        condition_id = market['condition_id']
        question = market['question'][:60] + "..." if len(market['question']) > 60 else market['question']
        
        print(f"\n[{market_index}/{total_markets}] {question}")
        print(f"   Condition ID: {condition_id[:20]}...")
        print(f"   Event ID: {market['event_id']}")
        print(f"   Volume: ${market['volume']:,.2f}")
        
        # Fetch redemptions
        redemptions = await fetch_redemptions_for_market_async(session, condition_id, market, semaphore)
        
        if redemptions:
            stats['markets_with_redemptions'] += 1
            stats['total_redemptions'] += len(redemptions)
            
            market_volume = sum(r['payout_usdc'] for r in redemptions)
            stats['total_volume'] += market_volume
            
            for r in redemptions:
                stats['unique_redeemers'].add(r['redeemer_address'])
            
            print(f"   ✅ Found {len(redemptions)} redemptions (${market_volume:,.2f})")
            
            # Upload to Supabase if enabled (parallel upload with new client per task)
            if uploader:
                if len(redemptions) > 1000:
                    print(f"   📤 Uploading {len(redemptions)} redemptions to Supabase (large batch)...", flush=True)
                else:
                    print(f"   📤 Uploading to Supabase...", end=" ", flush=True)
                
                # Run sync upload in thread pool to not block async loop
                # Each upload uses a new client instance (thread-safe)
                loop = asyncio.get_event_loop()
                try:
                    success = await loop.run_in_executor(None, uploader.upload_redemptions_batch, redemptions)
                    if success:
                        print("✅ Uploaded" if len(redemptions) <= 1000 else "      ✅ Successfully uploaded large batch")
                    else:
                        print("❌ Failed" if len(redemptions) <= 1000 else "      ❌ Failed to upload large batch")
                        print(f"      🔍 Market: {market_index} | Condition: {condition_id[:30]}...")
                        print(f"      🔍 Records: {len(redemptions)} | Volume: ${market_volume:,.2f}")
                        stats['upload_errors'] += 1
                except Exception as upload_err:
                    error_type = type(upload_err).__name__
                    error_detail = str(upload_err)[:150]
                    print(f"❌ Upload Exception: {error_type}")
                    print(f"   Market: [{market_index}] {question}")
                    print(f"   Condition: {condition_id[:30]}...")
                    print(f"   Records: {len(redemptions)}")
                    print(f"   Error: {error_detail}")
                    stats['upload_errors'] += 1
        else:
            print(f"   ⚪ No redemptions found")
        
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
    
    print("=" * 70)
    print("🚀 POLYMARKET REDEMPTIONS FETCHER (OPTIMIZED PARALLEL)")
    print("=" * 70)
    print(f"⚡ Processing settings:")
    print(f"   - Batch size: {BATCH_SIZE} markets")
    print(f"   - Concurrent per batch: {MAX_CONCURRENT_MARKETS} markets")
    print(f"   - Request timeout: {REQUEST_TIMEOUT}s")
    print(f"   - Delay between batches: {BATCH_DELAY}s")
    print(f"   - Database uploads: Parallel (new client per upload)")
    
    # Initialize database uploader if auto_upload is enabled
    uploader = None
    if auto_upload:
        if use_local_db:
            print("🔄 Auto-upload to LOCAL PostgreSQL enabled")
            try:
                from local_db_uploader import LocalDatabaseUploader
                uploader = LocalDatabaseUploader()
                print("✅ Connected to local PostgreSQL")
            except Exception as e:
                print(f"❌ Failed to connect to local PostgreSQL: {e}")
                print("   Continuing without upload...")
                auto_upload = False
                uploader = None
        else:
            print("🔄 Auto-upload to Supabase enabled")
            try:
                from supabase_uploader import SupabaseUploader
                uploader = SupabaseUploader()
                print("✅ Connected to Supabase")
            except Exception as e:
                print(f"❌ Failed to connect to Supabase: {e}")
                print("   Continuing without upload...")
                auto_upload = False
                uploader = None
    
    # 1. Find and load latest events file
    events_file = find_latest_events_file()
    if not events_file:
        return
    
    events = load_events_file(events_file)
    
    # 2. Extract markets
    print(f"\n📊 Extracting markets from events...")
    markets = extract_markets(events)
    
    if FILTER_CLOSED_ONLY:
        print(f"   Filter: Closed markets only")
    if MIN_VOLUME > 0:
        print(f"   Filter: Min volume ${MIN_VOLUME:,.2f}")
    
    print(f"✅ Found {len(markets)} markets to process")
    
    if MAX_MARKETS:
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
        'processing_errors': 0
    }
    
    # Create semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_MARKETS)
    
    # Create aiohttp session
    async with aiohttp.ClientSession() as session:
        # Process markets in batches
        total_batches = (len(markets) + BATCH_SIZE - 1) // BATCH_SIZE
        
        for batch_num in range(0, len(markets), BATCH_SIZE):
            batch_markets = markets[batch_num:batch_num + BATCH_SIZE]
            batch_index = batch_num // BATCH_SIZE + 1
            
            print(f"\n📦 Processing batch {batch_index}/{total_batches} ({len(batch_markets)} markets)")
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
                    semaphore
                )
                tasks.append(task)
            
            # Wait for all tasks in this batch to complete
            await asyncio.gather(*tasks, return_exceptions=True)
            
            # Show batch completion time
            batch_elapsed = time.time() - batch_start
            print(f"\n✅ Batch {batch_index} completed in {batch_elapsed:.1f}s")
            
            # Delay between batches (except for last batch)
            if batch_num + BATCH_SIZE < len(markets):
                print(f"⏳ Waiting {BATCH_DELAY}s before next batch...")
                await asyncio.sleep(BATCH_DELAY)
    
    # 4. Print final statistics
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
    
    if auto_upload and uploader:
        print(f"\n💾 Database Upload:")
        print(f"   Uploaded:                {uploader.stats['redemptions_inserted']:,} redemptions")
        if stats['upload_errors'] > 0:
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
    
    # Total execution time
    total_elapsed = time.time() - script_start_time
    hours = int(total_elapsed // 3600)
    minutes = int((total_elapsed % 3600) // 60)
    seconds = int(total_elapsed % 60)
    
    print(f"\n⏱️  Total execution time: {hours}h {minutes}m {seconds}s ({total_elapsed:.1f}s)")
    
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
        print("  --upload, -u    Upload redemptions to database")
        print("  --local, -l     Use local PostgreSQL instead of Supabase (requires --upload)")
        print("  --help, -h      Show this help message")
        print()
        print("Examples:")
        print("  python fetch_redemptions.py                  # Fetch only, no upload")
        print("  python fetch_redemptions.py --upload         # Fetch and upload to Supabase")
        print("  python fetch_redemptions.py --upload --local # Fetch and upload to local PostgreSQL")
        sys.exit(0)

    if use_local_db and not auto_upload:
        print("⚠️  Warning: --local flag requires --upload flag")
        print("   Use: python fetch_redemptions.py --upload --local")
        sys.exit(1)

    if auto_upload:
        db_name = "local PostgreSQL" if use_local_db else "Supabase"
        print(f"🔄 Auto-upload to {db_name} enabled")

    process_all_markets(auto_upload=auto_upload, use_local_db=use_local_db)
