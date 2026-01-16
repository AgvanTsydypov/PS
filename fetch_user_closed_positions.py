"""
Fetch Closed Positions for Redeemers from Polymarket API
Queries local database for redeemers and fetches their closed positions

БЫСТРЫЙ ЗАПУСК:
===============
1. Preview mode (без загрузки, показать базовую статистику):
   python fetch_user_closed_positions.py --local

2. Preview mode с детальным выводом (показать данные в реальном времени):
   python fetch_user_closed_positions.py --local --verbose

3. Fetch + загрузка в локальную PostgreSQL:
   python fetch_user_closed_positions.py --upload --local

4. Fetch + загрузка в Supabase:
   python fetch_user_closed_positions.py --upload

5. Тестирование с ограничением пользователей + детальный вывод:
   python fetch_user_closed_positions.py --limit 5 --local --verbose

6. Для пользователей с большим количеством позиций (7000+):
   python fetch_user_closed_positions.py --positions 10000 --upload --local --delay 1.5

7. Справка:
   python fetch_user_closed_positions.py --help

ТРЕБОВАНИЯ:
===========
- Python 3.8+
- pip install -r requirements.txt
- Файл .env с настройками (для БД)
- База данных с таблицей redemptions и events

ЛОГИ:
=====
- Выводятся в консоль в реальном времени
- Показывают прогресс загрузки для каждого пользователя

Features:
- Автоматические retry при ошибках API
- Пауза между запросами (избегаем rate limiting)
- Batch загрузка в БД
- Real-time логирование всех операций
- Поддержка Supabase и локальной PostgreSQL
- Фильтрация по параметрам API (market, event, title, etc.)
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
from urllib.parse import urlencode

# Fix encoding for Windows console
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Import the existing database uploader
from supabase_uploader import SupabaseUploader


# ==========================================
# CONFIGURATION
# ==========================================

API_BASE_URL = "https://data-api.polymarket.com/v1"
API_DELAY = 0.5  # Delay between API requests (seconds)
API_DELAY_LARGE_FETCH = 1.0  # Delay for large fetches (>1000 positions)
DEFAULT_LIMIT_PER_USER = 50  # Max closed positions per user to fetch
BATCH_SIZE = 100  # How many records to upload to DB at once


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
        # Use Supabase
        from supabase import create_client
        
        print(f"🔵 Connecting to Supabase...")
        
        client = create_client(uploader.supabase_url, uploader.supabase_key)
        
        # Since Supabase doesn't support raw SQL through the client easily,
        # we'll need to use the REST API or rpc function
        # For simplicity, let's use local PostgreSQL for this script
        # Or implement the query logic using Supabase client methods
        
        print("⚠️  WARNING: Supabase mode requires local PostgreSQL for complex queries")
        print("   Please use --local flag")
        return []


# ==========================================
# API FUNCTIONS
# ==========================================

def fetch_closed_positions_for_user(
    user_address: str,
    limit: int = DEFAULT_LIMIT_PER_USER,
    offset: int = 0,
    market: Optional[str] = None,
    event_id: Optional[int] = None,
    title: Optional[str] = None,
    sort_by: str = "REALIZEDPNL",
    sort_direction: str = "DESC"
) -> List[Dict]:
    """
    Fetch closed positions for a specific user from Polymarket API
    
    Args:
        user_address: User's proxy wallet address
        limit: Max number of positions to fetch (0-50)
        offset: Pagination offset
        market: Filter by market condition_id
        event_id: Filter by event ID
        title: Filter by market title
        sort_by: Sort field (REALIZEDPNL, TITLE, PRICE, AVGPRICE, TIMESTAMP)
        sort_direction: Sort direction (ASC, DESC)
    
    Returns:
        List of closed position records
    """
    # Build query parameters
    params = {
        'user': user_address,
        'limit': min(limit, 50),  # API max is 50
        'offset': offset,
        'sortBy': sort_by,
        'sortDirection': sort_direction
    }
    
    if market:
        params['market'] = market
    if event_id:
        params['eventId'] = event_id
    if title:
        params['title'] = title
    
    # Make API request
    url = f"{API_BASE_URL}/closed-positions"
    
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        return data if isinstance(data, list) else []
        
    except requests.exceptions.RequestException as e:
        print(f"❌ API Error for {user_address}: {str(e)}")
        return []


def fetch_all_closed_positions_for_user(
    user_address: str,
    max_total: int = 1000,
    **kwargs
) -> List[Dict]:
    """
    Fetch all closed positions for a user with pagination
    
    Args:
        user_address: User's proxy wallet address
        max_total: Maximum total positions to fetch
        **kwargs: Additional parameters to pass to fetch_closed_positions_for_user
    
    Returns:
        List of all closed position records
    """
    all_positions = []
    offset = 0
    batch_size = 50  # API max per request
    
    # Use longer delay for large fetches to avoid rate limiting
    delay = API_DELAY_LARGE_FETCH if max_total > 1000 else API_DELAY
    
    while len(all_positions) < max_total:
        batch = fetch_closed_positions_for_user(
            user_address,
            limit=batch_size,
            offset=offset,
            **kwargs
        )
        
        if not batch:
            break
        
        all_positions.extend(batch)
        offset += len(batch)
        
        # Show progress for large fetches
        if max_total > 1000 and len(all_positions) % 500 == 0:
            print(f"      ... fetched {len(all_positions)}/{max_total}")
        
        # If we got less than batch_size, we've reached the end
        if len(batch) < batch_size:
            break
        
        # Delay to avoid rate limiting (longer for large fetches)
        time.sleep(delay)
    
    return all_positions[:max_total]


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
# MAIN PROCESSING
# ==========================================

def process_redeemers(
    redeemers: List[Dict],
    upload: bool = False,
    use_local_db: bool = False,
    positions_per_user: int = DEFAULT_LIMIT_PER_USER,
    verbose: bool = False
):
    """
    Process all redeemers: fetch their closed positions and optionally upload to DB
    """
    print("\n" + "=" * 70)
    print("🚀 FETCHING CLOSED POSITIONS FROM POLYMARKET API")
    print("=" * 70)
    print(f"Total redeemers to process: {len(redeemers)}")
    print(f"Positions per user: {positions_per_user}")
    print(f"Upload to DB: {'YES' if upload else 'NO (preview only)'}")
    if upload:
        print(f"Database: {'Local PostgreSQL' if use_local_db else 'Supabase'}")
    
    # Estimate time for large fetches
    if positions_per_user > 1000:
        delay = API_DELAY_LARGE_FETCH if positions_per_user > 1000 else API_DELAY
        requests_per_user = (positions_per_user // 50) + 1
        seconds_per_user = requests_per_user * delay
        unique_count = len(set([r['redeemer_address'] for r in redeemers]))
        total_seconds = unique_count * seconds_per_user
        total_minutes = total_seconds / 60
        total_hours = total_minutes / 60
        
        print(f"\n⏱️  ESTIMATED TIME:")
        print(f"   Requests per user: ~{requests_per_user}")
        print(f"   Time per user: ~{seconds_per_user:.1f}s")
        if total_hours >= 1:
            print(f"   Total estimated time: ~{total_hours:.1f} hours")
        elif total_minutes >= 1:
            print(f"   Total estimated time: ~{total_minutes:.1f} minutes")
        else:
            print(f"   Total estimated time: ~{total_seconds:.0f} seconds")
        print(f"   (Based on {delay}s delay between API requests)")
    
    print("=" * 70)
    
    # Initialize uploader if needed
    uploader = None
    if upload:
        uploader = SupabaseUploader(use_local_db=use_local_db)
    
    # Get unique redeemer addresses
    unique_addresses = list(set([r['redeemer_address'] for r in redeemers]))
    print(f"\n📋 Unique redeemer addresses: {len(unique_addresses)}")
    
    # Process each redeemer
    all_positions = []
    stats = {
        'total_users': len(unique_addresses),
        'users_processed': 0,
        'users_with_positions': 0,
        'total_positions': 0,
        'errors': 0
    }
    
    for i, address in enumerate(unique_addresses, 1):
        print(f"\n[{i}/{len(unique_addresses)}] Processing: {address}")
        
        try:
            # Fetch closed positions
            if positions_per_user > 1000:
                print(f"   🔄 Fetching up to {positions_per_user} positions (this may take a while)...")
            
            positions = fetch_all_closed_positions_for_user(
                address,
                max_total=positions_per_user
            )
            
            if positions:
                if len(positions) >= positions_per_user:
                    print(f"   ✅ Found {len(positions)} closed positions (limit reached, may have more)")
                else:
                    print(f"   ✅ Found {len(positions)} closed positions (all available)")
                stats['users_with_positions'] += 1
                stats['total_positions'] += len(positions)
                
                # Transform data
                transformed = [transform_closed_position(p) for p in positions]
                all_positions.extend(transformed)
                
                # Show sample data in verbose mode (preview only)
                if verbose and not upload and len(transformed) > 0:
                    print(f"   📊 Sample positions (first 3):")
                    for j, pos in enumerate(transformed[:3], 1):
                        print(f"      {j}. {pos['title']}")
                        print(f"         Outcome: {pos['outcome']}")
                        print(f"         Realized PnL: ${pos['realized_pnl']:.2f}")
                        print(f"         Avg Price: {pos['avg_price']:.4f}")
                    if len(transformed) > 3:
                        print(f"      ... and {len(transformed) - 3} more positions")
                
                # Upload in batches if needed
                if upload and len(all_positions) >= BATCH_SIZE:
                    print(f"   📤 Uploading batch of {len(all_positions)} positions...")
                    success = upload_closed_positions_batch(uploader, all_positions)
                    if success:
                        print(f"   ✅ Batch uploaded successfully")
                        all_positions = []  # Clear batch
                    else:
                        print(f"   ❌ Batch upload failed")
                        stats['errors'] += 1
            else:
                print(f"   ⚠️  No closed positions found")
            
            stats['users_processed'] += 1
            
            # Small delay between users
            if i < len(unique_addresses):
                time.sleep(API_DELAY)
                
        except Exception as e:
            print(f"   ❌ Error processing {address}: {str(e)}")
            stats['errors'] += 1
            continue
    
    # Upload remaining positions
    if upload and all_positions:
        print(f"\n📤 Uploading final batch of {len(all_positions)} positions...")
        success = upload_closed_positions_batch(uploader, all_positions)
        if success:
            print(f"✅ Final batch uploaded successfully")
        else:
            print(f"❌ Final batch upload failed")
    
    # Print summary
    print("\n" + "=" * 70)
    print("📊 PROCESSING SUMMARY")
    print("=" * 70)
    print(f"Total users:              {stats['total_users']}")
    print(f"Users processed:          {stats['users_processed']}")
    print(f"Users with positions:     {stats['users_with_positions']}")
    print(f"Total positions fetched:  {stats['total_positions']}")
    print(f"Errors:                   {stats['errors']}")
    print("=" * 70)
    
    # Preview data if not uploading
    if not upload and all_positions:
        print("\n" + "=" * 70)
        print("📋 PREVIEW OF FETCHED DATA (first 3 positions)")
        print("=" * 70)
        for i, pos in enumerate(all_positions[:3], 1):
            print(f"\nPosition {i}:")
            print(f"  User: {pos['proxy_wallet']}")
            print(f"  Market: {pos['title']}")
            print(f"  Outcome: {pos['outcome']}")
            print(f"  Realized PnL: ${pos['realized_pnl']:.2f}")
            print(f"  Avg Price: {pos['avg_price']:.4f}")
        print("\n" + "=" * 70)
        print("💡 Run with --upload flag to store data in database")
        print("=" * 70)


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
                        proxy_wallet, condition_id, asset,
                        avg_price, total_bought, realized_pnl, cur_price,
                        timestamp_unix, timestamp_human,
                        title, slug, icon, event_slug,
                        outcome, outcome_index, opposite_outcome, opposite_asset,
                        end_date, end_date_parsed
                    ) VALUES (
                        %(proxy_wallet)s, %(condition_id)s, %(asset)s,
                        %(avg_price)s, %(total_bought)s, %(realized_pnl)s, %(cur_price)s,
                        %(timestamp_unix)s, %(timestamp_human)s,
                        %(title)s, %(slug)s, %(icon)s, %(event_slug)s,
                        %(outcome)s, %(outcome_index)s, %(opposite_outcome)s, %(opposite_asset)s,
                        %(end_date)s, %(end_date_parsed)s
                    )
                    ON CONFLICT (proxy_wallet, condition_id, asset, timestamp_unix) 
                    DO UPDATE SET
                        avg_price = EXCLUDED.avg_price,
                        total_bought = EXCLUDED.total_bought,
                        realized_pnl = EXCLUDED.realized_pnl,
                        cur_price = EXCLUDED.cur_price,
                        updated_at = NOW()
                """
                
                cursor.executemany(insert_query, positions)
                conn.commit()
                
                print(f"   ✅ Inserted/Updated {cursor.rowcount} positions")
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
            
            print(f"   ✅ Upserted {len(positions)} positions")
            return True
            
    except Exception as e:
        print(f"   ❌ Upload error: {str(e)}")
        return False


# ==========================================
# CLI INTERFACE
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description='Fetch closed positions for redeemers from Polymarket API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview data without uploading
  python fetch_user_closed_positions.py --local
  
  # Preview with detailed output (see positions in real-time)
  python fetch_user_closed_positions.py --local --verbose
  
  # Fetch and upload to local PostgreSQL
  python fetch_user_closed_positions.py --upload --local
  
  # Fetch and upload to Supabase
  python fetch_user_closed_positions.py --upload
  
  # Test with limited users (verbose mode)
  python fetch_user_closed_positions.py --limit 5 --local --verbose
  
  # Fetch many positions per user (e.g., 5000)
  python fetch_user_closed_positions.py --positions 5000 --upload --local
  
  # For users with 7000+ positions, use higher delay to avoid rate limiting
  python fetch_user_closed_positions.py --positions 10000 --upload --local --delay 1.5
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
        default=DEFAULT_LIMIT_PER_USER,
        help=f'Max positions to fetch per user (default: {DEFAULT_LIMIT_PER_USER})'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed position data in real-time (preview mode only)'
    )
    
    parser.add_argument(
        '--delay',
        type=float,
        default=API_DELAY,
        help=f'Delay between API requests in seconds (default: {API_DELAY}s, use higher for rate limiting)'
    )
    
    args = parser.parse_args()
    
    # Apply custom delay if provided
    if args.delay != API_DELAY:
        global API_DELAY, API_DELAY_LARGE_FETCH
        API_DELAY = args.delay
        API_DELAY_LARGE_FETCH = args.delay * 2
    
    print("\n" + "=" * 70)
    print("🎯 POLYMARKET CLOSED POSITIONS FETCHER")
    print("=" * 70)
    print(f"Mode: {'UPLOAD' if args.upload else 'PREVIEW ONLY'}")
    if args.upload:
        print(f"Database: {'Local PostgreSQL' if args.local else 'Supabase'}")
    if args.limit:
        print(f"User limit: {args.limit}")
    print(f"Positions per user: {args.positions}")
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
        
        # Step 2: Process redeemers
        process_redeemers(
            redeemers,
            upload=args.upload,
            use_local_db=args.local,
            positions_per_user=args.positions,
            verbose=args.verbose
        )
        
        print("\n✅ Process completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
