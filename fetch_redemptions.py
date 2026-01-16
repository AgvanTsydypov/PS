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

4. Справка:
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
- Параллельная обработка маркетов (3 для обоих режимов - API лимит!)
- Автоматические retry при ошибках API и БД
- Повторная загрузка неудавшихся данных после завершения:
  * Ожидание 10 секунд перед retry (API/БД "отдыхают")
  * Пауза 3 секунды между повторными попытками
  * Сохранение chunk_size для быстрой загрузки в БД
- Сохранение финально неудавшихся данных в JSON для ручной retry
- Real-time логирование всех операций
- Поддержка Supabase и локальной PostgreSQL
- TURBO MODE для локальной БД:
  * API фетчинг: консервативный (3 маркета, батчи по 20, пауза 3с)
  * БД загрузка: СУПЕР БЫСТРАЯ (chunk 5000, PostgreSQL COPY)
  * 150ms пауза между пагинацией (избегаем rate limiting)
  * Баланс: API стабильный, БД на максимуме
  * Результат: 3-5x прирост за счет БД, не упираясь в API
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

# Parallel processing settings (will be adjusted based on database type)
# Conservative settings for Supabase (cloud, has rate limits)
MAX_CONCURRENT_MARKETS_CLOUD = 3
BATCH_SIZE_CLOUD = 20
BATCH_DELAY_CLOUD = 5

# Optimized settings for local PostgreSQL
# БД может быть супер-быстрой, но API очень чувствителен к перегрузке!
MAX_CONCURRENT_MARKETS_LOCAL = 3  # Консервативно! API Goldsky rate limiting жесткий
BATCH_SIZE_LOCAL = 20  # Не увеличиваем, чтобы не перегружать API
BATCH_DELAY_LOCAL = 3  # Больше время для "отдыха" API между батчами

# Active settings (will be set based on database type)
MAX_CONCURRENT_MARKETS = MAX_CONCURRENT_MARKETS_CLOUD  # Default to conservative
BATCH_SIZE = BATCH_SIZE_CLOUD
BATCH_DELAY = BATCH_DELAY_CLOUD

REQUEST_TIMEOUT = 60  # Timeout for each request in seconds
MAX_RETRIES = 5  # Maximum retry attempts for failed requests
RETRY_DELAY = 2  # Delay between retries in seconds

# Database upload settings
# БД может быть ОЧЕНЬ быстрой - не ограничиваем!
MAX_CONCURRENT_DB_UPLOADS = 100  # Без ограничений - БД справится!

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
    semaphore: asyncio.Semaphore,
    use_local_db: bool = False
) -> List[Dict]:
    """Fetch all redemptions for a specific market (async version)"""
    async with semaphore:  # Limit concurrent requests
        # Small initial delay to spread out requests and avoid bursts
        if use_local_db:
            await asyncio.sleep(0.1)  # 100ms стартовая задержка для каждого маркета
        
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
                        if len(all_redemptions) > 500000:  # Увеличили до 500k
                            print(f"      ⚠️  Reached safety limit of 500k redemptions")
                            break
                        
                        # Delay between pagination requests to avoid API rate limiting
                        # GraphQL API очень чувствителен, нужны паузы!
                        if use_local_db and request_count > 1:
                            await asyncio.sleep(0.15)  # 150ms между запросами пагинации (было 50ms)
                        elif request_count > 1:
                            await asyncio.sleep(0.05)  # Для Supabase - 50ms
                            
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
    semaphore: asyncio.Semaphore,
    db_semaphore: asyncio.Semaphore,
    use_local_db: bool = False
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
        redemptions = await fetch_redemptions_for_market_async(session, condition_id, market, semaphore, use_local_db)
        
        if redemptions:
            stats['markets_with_redemptions'] += 1
            stats['total_redemptions'] += len(redemptions)
            
            market_volume = sum(r['payout_usdc'] for r in redemptions)
            stats['total_volume'] += market_volume
            
            for r in redemptions:
                stats['unique_redeemers'].add(r['redeemer_address'])
            
            print(f"   ✅ Found {len(redemptions)} redemptions (${market_volume:,.2f})", flush=True)
            
            # Upload to database if enabled (with concurrency control)
            if uploader:
                db_name = "local PostgreSQL" if use_local_db else "Supabase"
                if len(redemptions) > 1000:
                    print(f"   📤 Uploading {len(redemptions)} redemptions to {db_name} (large batch)...", flush=True)
                else:
                    print(f"   📤 Uploading to {db_name}...", end=" ", flush=True)
                
                # Use semaphore to limit concurrent DB uploads (не перегружаем БД!)
                async with db_semaphore:
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
                        error_type = type(upload_err).__name__
                        error_detail = str(upload_err)[:150]
                        print(f"❌ Upload Exception: {error_type}")
                        print(f"   Market: [{market_index}] {question}")
                        print(f"   Condition: {condition_id[:30]}...")
                        print(f"   Records: {len(redemptions)}")
                        print(f"   Error: {error_detail}")
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
            # Warn if high-volume market has no redemptions (might indicate API issue)
            if market['volume'] > 100000 and market['closed']:
                print(f"   ⚠️  No redemptions found (suspicious: high volume ${market['volume']:,.2f}, market closed)", flush=True)
                stats['suspicious_empty_markets'] += 1
            else:
                print(f"   ⚪ No redemptions found", flush=True)
        
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
    
    # Apply performance settings based on database type
    global MAX_CONCURRENT_MARKETS, BATCH_SIZE, BATCH_DELAY
    if use_local_db:
        MAX_CONCURRENT_MARKETS = MAX_CONCURRENT_MARKETS_LOCAL
        BATCH_SIZE = BATCH_SIZE_LOCAL
        BATCH_DELAY = BATCH_DELAY_LOCAL
        perf_mode = "🚀 TURBO MODE (Local PostgreSQL)"
    else:
        MAX_CONCURRENT_MARKETS = MAX_CONCURRENT_MARKETS_CLOUD
        BATCH_SIZE = BATCH_SIZE_CLOUD
        BATCH_DELAY = BATCH_DELAY_CLOUD
        perf_mode = "⚡ STANDARD MODE (Supabase Cloud)"
    
    print("=" * 70)
    print("🚀 POLYMARKET REDEMPTIONS FETCHER (OPTIMIZED PARALLEL)")
    print("=" * 70)
    print(f"{perf_mode}")
    print(f"⚡ Processing settings:")
    print(f"   - Batch size: {BATCH_SIZE} markets")
    print(f"   - Concurrent per batch: {MAX_CONCURRENT_MARKETS} markets")
    print(f"   - Request timeout: {REQUEST_TIMEOUT}s")
    print(f"   - Delay between batches: {BATCH_DELAY}s")
    print(f"   - Database uploads: Parallel (new client per upload)")
    if use_local_db:
        print(f"   - Performance: MAXIMUM (No rate limits!)")
    
    # Initialize database uploader if auto_upload is enabled
    uploader = None
    if auto_upload:
        db_name = "LOCAL PostgreSQL" if use_local_db else "Supabase"
        print(f"🔄 Auto-upload to {db_name} enabled")
        try:
            from supabase_uploader import SupabaseUploader
            uploader = SupabaseUploader(use_local_db=use_local_db)
            print(f"✅ Connected to {db_name}")
        except Exception as e:
            print(f"❌ Failed to connect to {db_name}: {e}")
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
        'processing_errors': 0,
        'suspicious_empty_markets': 0,  # High volume closed markets with no redemptions
        'failed_uploads': []  # List of (redemptions, market_info) tuples that failed to upload
    }
    
    # Create semaphores to limit concurrent operations
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_MARKETS)  # Ограничение на фетчинг
    db_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DB_UPLOADS)  # Ограничение на загрузку в БД
    
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
                    semaphore,
                    db_semaphore,
                    use_local_db
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
    
    # 4. Retry failed uploads
    if auto_upload and uploader and stats['failed_uploads']:
        print("\n" + "=" * 70)
        print("🔄 RETRYING FAILED UPLOADS")
        print("=" * 70)
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
            
            print(f"\n[Retry {i}/{len(stats['failed_uploads'])}] Market #{market_info['market_index']}")
            print(f"   Question: {market_info['question'][:60]}...")
            print(f"   Condition: {market_info['condition_id'][:30]}...")
            print(f"   Records: {len(redemptions)} | Volume: ${market_info['volume']:,.2f}")
            print(f"   🔄 Retrying upload...", end=" ", flush=True)
            
            try:
                # Use default chunk size (БД справляется, проблема была в API timing)
                # БД для локальной будет использовать chunk_size=5000 (быстро!)
                success = uploader.upload_redemptions_batch(redemptions)
                if success:
                    print("✅ Success!")
                    retry_success += 1
                else:
                    print("❌ Failed again")
                    retry_failed += 1
                    still_failed_items.append(failed_item)
            except Exception as e:
                print(f"❌ Exception: {type(e).__name__}")
                print(f"      Error: {str(e)[:100]}")
                retry_failed += 1
                still_failed_items.append(failed_item)
            
            # Longer delay between retries to let DB/API rest
            if i < len(stats['failed_uploads']):
                print(f"   ⏳ Waiting 3 seconds before next retry...")
                await asyncio.sleep(3)
        
        print(f"\n📊 Retry Results:")
        print(f"   ✅ Successful: {retry_success}/{len(stats['failed_uploads'])}")
        print(f"   ❌ Still failed: {retry_failed}/{len(stats['failed_uploads'])}")
        
        # Save still-failed data to file
        if still_failed_items:
            import json
            from datetime import datetime
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
            print(f"   You can retry later using: python supabase_uploader.py {failed_file} --redemptions {'--local' if use_local_db else ''}")
        
        # Update stats
        stats['upload_errors'] = retry_failed  # Update to reflect final count
    
    # 5. Print final statistics
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
    hours = int(total_elapsed // 3600)
    minutes = int((total_elapsed % 3600) // 60)
    seconds = int(total_elapsed % 60)
    
    print(f"\n⏱️  Total execution time: {hours}h {minutes}m {seconds}s ({total_elapsed:.1f}s)")
    
    # Performance metrics
    if stats['total_redemptions'] > 0 and total_elapsed > 0:
        redemptions_per_sec = stats['total_redemptions'] / total_elapsed
        markets_per_sec = stats['markets_processed'] / total_elapsed
        print(f"\n📈 Performance:")
        print(f"   Redemptions/sec:        {redemptions_per_sec:.1f}")
        print(f"   Markets/sec:            {markets_per_sec:.2f}")
        if auto_upload and uploader:
            db_name = "PostgreSQL" if use_local_db else "Supabase"
            print(f"   Database ({db_name}):   {uploader.stats['redemptions_inserted'] / total_elapsed:.1f} records/sec")
    
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
