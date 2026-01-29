"""
Database Uploader for Polymarket Events
Uploads events and markets data to Supabase or local PostgreSQL

ПРЯМОЙ ЗАПУСК (для загрузки events/markets из JSON):
====================================================
1. Загрузить последний JSON файл в Supabase:
   python supabase_uploader.py

2. Загрузить конкретный файл в Supabase:
   python supabase_uploader.py путь/к/файлу.json

3. Загрузить в локальную PostgreSQL:
   python supabase_uploader.py --local
   python supabase_uploader.py путь/к/файлу.json --local

4. Загрузить redemptions (конкретный файл):
   python supabase_uploader.py redemptions_data.json --redemptions
   python supabase_uploader.py redemptions_data.json --redemptions --local

5. Повторить загрузку неудавшихся данных:
   python supabase_uploader.py output/failed_redemptions_*.json --redemptions --local
   (автоматически распознает формат failed_redemptions)

ПРОГРАММНОЕ ИСПОЛЬЗОВАНИЕ (в Python коде):
==========================================
   # Supabase (по умолчанию)
   from supabase_uploader import SupabaseUploader
   uploader = SupabaseUploader()
   uploader.upload_redemptions_batch(redemptions_list)
   
   # Локальная PostgreSQL (явно указать)
   uploader = SupabaseUploader(use_local_db=True)
   uploader.upload_redemptions_batch(redemptions_list)

ДРУГИЕ СКРИПТЫ:
===============
- Тестирование подключения:
  python test_db_connection.py

- Основной скрипт (fetch redemptions):
  python fetch_redemptions.py --upload          # → Supabase
  python fetch_redemptions.py --upload --local  # → PostgreSQL

ТРЕБОВАНИЯ:
===========
- Python 3.8+
- pip install supabase psycopg2-binary python-dotenv
- Файл .env с настройками:
  * Для Supabase: SUPABASE_URL, SUPABASE_KEY
  * Для PostgreSQL: LOCAL_DB_HOST, LOCAL_DB_PORT, LOCAL_DB_NAME, 
                     LOCAL_DB_USER, LOCAL_DB_PASSWORD

ВАЖНО:
======
- Выбор БД ТОЛЬКО через параметр use_local_db (True/False) или флаг --local
- По умолчанию: Supabase (use_local_db=False)
- Нет автоматического определения БД!

CHUNK SIZE & PERFORMANCE:
=========================
- Supabase: 1000 записей на chunk (с автоматической деградацией при таймаутах)
- PostgreSQL: 5000 записей на chunk (TURBO MODE!)
  * Использует PostgreSQL COPY (10-50x быстрее обычных INSERT)
  * БД загрузка без ограничений (моментально!)
  * API фетчинг: консервативно (3 маркета, пауза 3с, 150ms между запросами)
  * Узкое место: GraphQL API, а не БД!
  * Результат: 3-5x ускорение за счет мгновенной загрузки в БД
"""

import json
import os
from datetime import datetime
from typing import List, Dict, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class SupabaseUploader:
    """Handles uploading Polymarket data to Supabase or local PostgreSQL"""
    
    # Table names configuration
    TABLE_EVENTS = 'events'
    TABLE_MARKETS = 'markets'
    TABLE_REDEMPTIONS = 'redemptions' # 5.248.075 5,260,530
    TABLE_METADATA = 'fetch_metadata'
    
    def __init__(self, use_local_db: bool = False):
        """
        Initialize database client
        
        Args:
            use_local_db: If True, use local PostgreSQL; if False (default), use Supabase
                         MUST be explicitly set - no automatic detection!
        
        Examples:
            # Use Supabase (default)
            uploader = SupabaseUploader()
            uploader = SupabaseUploader(use_local_db=False)
            
            # Use local PostgreSQL (explicit)
            uploader = SupabaseUploader(use_local_db=True)
        """
        self.use_local_db = use_local_db
        self.stats = {
            'events_inserted': 0,
            'events_updated': 0,
            'markets_inserted': 0,
            'markets_updated': 0,
            'redemptions_inserted': 0,
            'errors': []
        }
        
        if use_local_db:
            # Initialize local PostgreSQL connection
            try:
                import psycopg2
                self.psycopg2 = psycopg2
            except ImportError:
                raise ImportError(
                    "psycopg2 is required for local PostgreSQL. "
                    "Install it with: pip install psycopg2-binary"
                )
            
            self.connection_params = {
                'host': os.getenv('LOCAL_DB_HOST', 'localhost'),
                'port': os.getenv('LOCAL_DB_PORT', '5432'),
                'database': os.getenv('LOCAL_DB_NAME', 'polymarket'),
                'user': os.getenv('LOCAL_DB_USER', 'postgres'),
                'password': os.getenv('LOCAL_DB_PASSWORD', '')
            }
            
            # Test connection
            self._test_local_connection()
            self.client = None
        else:
            # Initialize Supabase client
            from supabase import create_client, Client
            
            self.supabase_url = os.getenv('SUPABASE_URL')
            self.supabase_key = os.getenv('SUPABASE_KEY')
            
            if not self.supabase_url or not self.supabase_key:
                raise ValueError(
                    "Missing Supabase credentials. "
                    "Please set SUPABASE_URL and SUPABASE_KEY in .env file"
                )
            
            self.client: Client = create_client(self.supabase_url, self.supabase_key)
    
    def _test_local_connection(self):
        """Test local PostgreSQL connection"""
        try:
            conn = self.psycopg2.connect(**self.connection_params)
            conn.close()
        except Exception as e:
            raise ConnectionError(
                f"Failed to connect to local PostgreSQL: {str(e)}\n"
                f"Host: {self.connection_params['host']}:{self.connection_params['port']}\n"
                f"Database: {self.connection_params['database']}\n"
                f"User: {self.connection_params['user']}"
            )
    
    def create_new_client(self):
        """Create a new database client instance (thread-safe for parallel uploads)"""
        if self.use_local_db:
            # For PostgreSQL, we'll create connection in each upload method
            return None
        else:
            from supabase import create_client
            return create_client(self.supabase_url, self.supabase_key)
    
    def load_json_data(self, filepath: str) -> Dict:
        """Load data from JSON file"""
        print(f"[*] Loading data from {filepath}...")
        with open(filepath, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
        
        # Handle different JSON structures
        # If the data has 'events' key, use it directly
        if 'events' in raw_data:
            data = raw_data
        # If the data is a list, wrap it in the expected structure
        elif isinstance(raw_data, list):
            data = {
                'events': raw_data,
                'metadata': {}
            }
        # If the data is a single event object (has 'id' and 'markets' keys)
        elif isinstance(raw_data, dict) and 'id' in raw_data:
            data = {
                'events': [raw_data],
                'metadata': {}
            }
        else:
            # Unknown structure, try to use as-is
            data = raw_data
        
        print(f"[OK] Loaded {len(data.get('events', []))} events")
        return data
    
    def prepare_event_data(self, event: Dict) -> Dict:
        """
        Transform event data for Supabase insertion
        Removes nested 'markets' array and prepares flat structure
        """
        # Helper function to convert camelCase to snake_case
        def camel_to_snake(name):
            import re
            name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
            return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()
        
        # Define allowed fields based on our schema
        allowed_fields = {
            'id', 'ticker', 'slug', 'title', 'description',
            'start_date', 'creation_date', 'end_date', 'created_at', 'updated_at', 'closed_time',
            'image', 'icon',
            'active', 'closed', 'archived', 'new', 'featured', 'restricted', 'neg_risk', 'enable_order_book',
            'volume', 'volume24hr', 'volume1wk', 'volume1mo', 'volume1yr',
            'liquidity', 'open_interest', 'liquidity_amm', 'liquidity_clob',
            'competitive', 'comment_count'
        }
        
        # Create a copy without the markets array, converting keys to snake_case
        event_data = {}
        for k, v in event.items():
            if k == 'markets':
                continue
            snake_key = camel_to_snake(k)
            if snake_key in allowed_fields:
                event_data[snake_key] = v
        
        # Handle date fields - convert to ISO format strings
        for date_field in ['start_date', 'creation_date', 'end_date', 'created_at', 'updated_at', 'closed_time']:
            if date_field in event_data and event_data[date_field]:
                # Ensure it's a string in ISO format
                date_val = event_data[date_field]
                if isinstance(date_val, str):
                    # Remove 'Z' and ensure consistent format
                    event_data[date_field] = date_val.replace('Z', '+00:00')
        
        # Convert numeric fields that might be strings
        if 'volume' in event_data:
            event_data['volume'] = float(event_data['volume']) if event_data['volume'] else 0.0
        
        for vol_field in ['volume24hr', 'volume1wk', 'volume1mo', 'volume1yr']:
            if vol_field in event_data:
                event_data[vol_field] = float(event_data[vol_field]) if event_data[vol_field] else 0.0
        
        # Handle liquidity fields
        for liq_field in ['liquidity', 'liquidityAmm', 'liquidityClob', 'openInterest']:
            if liq_field in event_data:
                event_data[liq_field] = float(event_data[liq_field]) if event_data[liq_field] else 0.0
        
        # Convert integer fields
        integer_fields = ['comment_count', 'competitive']
        for field in integer_fields:
            if field in event_data and event_data[field] is not None:
                try:
                    event_data[field] = int(float(event_data[field]))  # Convert to float first, then int
                except (ValueError, TypeError):
                    event_data[field] = 0
        
        return event_data
    
    def prepare_market_data(self, market: Dict, event_id: str) -> Dict:
        """
        Transform market data for Supabase insertion
        Adds event_id as foreign key
        """
        # Helper function to convert camelCase to snake_case
        def camel_to_snake(name):
            import re
            name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
            return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()
        
        # Define allowed fields based on our schema
        allowed_fields = {
            'id', 'question', 'condition_id', 'slug', 'question_id',
            'end_date', 'start_date', 'created_at', 'updated_at', 'closed_time', 
            'uma_end_date', 'accepting_orders_timestamp', 'deploying_timestamp',
            'image', 'icon', 'description', 'outcomes', 'outcome_prices',
            'volume', 'volume_num', 'volume24hr', 'volume1wk', 'volume1mo', 'volume1yr',
            'volume_clob', 'volume24hr_clob', 'volume1wk_clob', 'volume1mo_clob', 'volume1yr_clob',
            'liquidity', 'liquidity_num', 'liquidity_amm', 'liquidity_clob',
            'active', 'closed', 'new', 'featured', 'archived', 'restricted', 'enable_order_book',
            'neg_risk', 'ready', 'funded', 'cyom', 'pager_duty_notification_enabled', 'approved',
            'automatically_resolved', 'automatically_active', 'clear_book_on_start', 'manual_activation',
            'neg_risk_other', 'pending_deployment', 'deploying', 'rfq_enabled', 'holding_rewards_enabled',
            'fees_enabled', 'requires_translation', 'accepting_orders', 'has_reviewed_dates',
            'resolved_by', 'uma_resolution_status', 'uma_resolution_statuses', 'uma_bond', 'uma_reward',
            'market_maker_address', 'submitted_by', 'group_item_title', 'group_item_threshold',
            'clob_token_ids', 'neg_risk_request_id', 'end_date_iso', 'start_date_iso',
            'order_price_min_tick_size', 'order_min_size', 'rewards_min_size', 'rewards_max_spread', 'spread',
            'one_day_price_change', 'one_week_price_change', 'last_trade_price', 'best_bid', 'best_ask',
            'competitive', 'custom_liveness'
        }
        
        # Convert keys to snake_case and filter to allowed fields
        market_data = {}
        for k, v in market.items():
            snake_key = camel_to_snake(k)
            if snake_key in allowed_fields:
                market_data[snake_key] = v
        
        market_data['event_id'] = event_id  # Foreign key to events table
        
        # Handle date fields (using snake_case names as keys are already converted)
        for date_field in ['end_date', 'start_date', 'created_at', 'updated_at', 'uma_end_date', 'closed_time', 'accepting_orders_timestamp', 'deploying_timestamp']:
            if date_field in market_data and market_data[date_field]:
                date_val = market_data[date_field]
                if isinstance(date_val, str):
                    # Handle different date formats
                    if 'Z' in date_val:
                        market_data[date_field] = date_val.replace('Z', '+00:00')
                    elif '+00' in date_val:
                        market_data[date_field] = date_val
                    else:
                        # Try to parse and reformat
                        try:
                            dt = datetime.fromisoformat(date_val.replace('Z', '+00:00'))
                            market_data[date_field] = dt.isoformat()
                        except:
                            pass  # Keep original if parsing fails
        
        # Convert numeric fields (float fields)
        float_fields = [
            'volume_num', 'liquidity_num',
            'volume24hr', 'volume1wk', 'volume1mo', 'volume1yr',
            'volume24hr_clob', 'volume1wk_clob', 'volume1mo_clob', 'volume1yr_clob',
            'volume_clob', 'liquidity_amm', 'liquidity_clob',
            'order_price_min_tick_size', 'order_min_size', 'rewards_min_size', 'rewards_max_spread',
            'spread', 'one_day_price_change', 'one_week_price_change', 'last_trade_price',
            'best_bid', 'best_ask'
        ]
        
        # Convert integer fields
        integer_fields = ['competitive', 'custom_liveness']
        
        for field in float_fields:
            if field in market_data and market_data[field] is not None:
                try:
                    market_data[field] = float(market_data[field])
                except (ValueError, TypeError):
                    market_data[field] = 0.0
        
        for field in integer_fields:
            if field in market_data and market_data[field] is not None:
                try:
                    market_data[field] = int(float(market_data[field]))  # Convert to float first, then int
                except (ValueError, TypeError):
                    market_data[field] = 0
        
        return market_data
    
    def upload_events(self, events: List[Dict], batch_size: int = 100) -> None:
        """
        Upload events to database in batches
        Uses upsert to handle duplicates
        """
        if self.use_local_db:
            return self._upload_events_to_postgres(events, batch_size)
        else:
            return self._upload_events_to_supabase(events, batch_size)
    
    def _upload_events_to_supabase(self, events: List[Dict], batch_size: int = 100) -> None:
        """Upload events to Supabase"""
        print(f"\n[*] Uploading {len(events)} events to Supabase...")

        for i in range(0, len(events), batch_size):
            batch = events[i:i + batch_size]
            prepared_batch = [self.prepare_event_data(event) for event in batch]
            
            try:
                # Upsert: insert or update if exists (based on 'id' primary key)
                response = self.client.table(self.TABLE_EVENTS).upsert(
                    prepared_batch,
                    on_conflict='id'
                ).execute()
                
                self.stats['events_inserted'] += len(prepared_batch)
                print(f"  [OK] Batch {i//batch_size + 1}: {len(prepared_batch)} events")
                
            except Exception as e:
                error_msg = f"Error uploading events batch {i//batch_size + 1}: {e}"
                print(f"  [ERROR] {error_msg}")
                self.stats['errors'].append(error_msg)
    
    def _upload_events_to_postgres(self, events: List[Dict], batch_size: int = 500) -> None:
        """Upload events to local PostgreSQL"""
        from psycopg2.extras import execute_batch
        
        print(f"\n[*] Uploading {len(events)} events to PostgreSQL...")
        
        conn = None
        cursor = None
        
        try:
            conn = self.psycopg2.connect(**self.connection_params)
            cursor = conn.cursor()
            
            # Prepare SQL for upsert
            insert_sql = f"""
                INSERT INTO {self.TABLE_EVENTS} (
                    id, ticker, slug, title, description,
                    start_date, creation_date, end_date, closed_time, created_at, updated_at,
                    image, icon,
                    active, closed, archived, new, featured, restricted, neg_risk, enable_order_book,
                    volume, volume24hr, volume1wk, volume1mo, volume1yr,
                    liquidity, open_interest, liquidity_amm, liquidity_clob,
                    competitive, comment_count
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    ticker = EXCLUDED.ticker,
                    slug = EXCLUDED.slug,
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    start_date = EXCLUDED.start_date,
                    creation_date = EXCLUDED.creation_date,
                    end_date = EXCLUDED.end_date,
                    closed_time = EXCLUDED.closed_time,
                    updated_at = EXCLUDED.updated_at,
                    image = EXCLUDED.image,
                    icon = EXCLUDED.icon,
                    active = EXCLUDED.active,
                    closed = EXCLUDED.closed,
                    archived = EXCLUDED.archived,
                    new = EXCLUDED.new,
                    featured = EXCLUDED.featured,
                    restricted = EXCLUDED.restricted,
                    neg_risk = EXCLUDED.neg_risk,
                    enable_order_book = EXCLUDED.enable_order_book,
                    volume = EXCLUDED.volume,
                    volume24hr = EXCLUDED.volume24hr,
                    volume1wk = EXCLUDED.volume1wk,
                    volume1mo = EXCLUDED.volume1mo,
                    volume1yr = EXCLUDED.volume1yr,
                    liquidity = EXCLUDED.liquidity,
                    open_interest = EXCLUDED.open_interest,
                    liquidity_amm = EXCLUDED.liquidity_amm,
                    liquidity_clob = EXCLUDED.liquidity_clob,
                    competitive = EXCLUDED.competitive,
                    comment_count = EXCLUDED.comment_count
            """
            
            for i in range(0, len(events), batch_size):
                batch = events[i:i + batch_size]
                prepared_batch = []
                
                for event in batch:
                    event_data = self.prepare_event_data(event)
                    # Convert to tuple in correct order
                    prepared_batch.append((
                        event_data.get('id'),
                        event_data.get('ticker'),
                        event_data.get('slug'),
                        event_data.get('title'),
                        event_data.get('description'),
                        event_data.get('start_date'),
                        event_data.get('creation_date'),
                        event_data.get('end_date'),
                        event_data.get('closed_time'),
                        event_data.get('created_at'),
                        event_data.get('updated_at'),
                        event_data.get('image'),
                        event_data.get('icon'),
                        event_data.get('active', False),
                        event_data.get('closed', False),
                        event_data.get('archived', False),
                        event_data.get('new', False),
                        event_data.get('featured', False),
                        event_data.get('restricted', False),
                        event_data.get('neg_risk', False),
                        event_data.get('enable_order_book', False),
                        event_data.get('volume'),
                        event_data.get('volume24hr'),
                        event_data.get('volume1wk'),
                        event_data.get('volume1mo'),
                        event_data.get('volume1yr'),
                        event_data.get('liquidity'),
                        event_data.get('open_interest'),
                        event_data.get('liquidity_amm'),
                        event_data.get('liquidity_clob'),
                        event_data.get('competitive'),
                        event_data.get('comment_count')
                    ))
                
                try:
                    execute_batch(cursor, insert_sql, prepared_batch, page_size=100)
                    conn.commit()
                    
                    self.stats['events_inserted'] += len(prepared_batch)
                    print(f"  [OK] Batch {i//batch_size + 1}: {len(prepared_batch)} events")
                    
                except Exception as e:
                    conn.rollback()
                    error_msg = f"Error uploading events batch {i//batch_size + 1}: {e}"
                    print(f"  [ERROR] {error_msg}")
                    self.stats['errors'].append(error_msg)
            
        except Exception as e:
            if conn:
                conn.rollback()
            error_msg = f"Failed to upload events to PostgreSQL: {e}"
            print(f"  [ERROR] {error_msg}")
            self.stats['errors'].append(error_msg)
            
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def upload_markets(self, events: List[Dict], batch_size: int = 100) -> None:
        """
        Upload markets to database in batches
        Extracts markets from events and links them via event_id
        """
        if self.use_local_db:
            return self._upload_markets_to_postgres(events, batch_size)
        else:
            return self._upload_markets_to_supabase(events, batch_size)
    
    def _upload_markets_to_supabase(self, events: List[Dict], batch_size: int = 100) -> None:
        """Upload markets to Supabase"""
        all_markets = []
        for event in events:
            event_id = event.get('id')
            markets = event.get('markets', [])
            for market in markets:
                market_data = self.prepare_market_data(market, event_id)
                all_markets.append(market_data)
        
        print(f"\n[*] Uploading {len(all_markets)} markets to Supabase...")
        
        for i in range(0, len(all_markets), batch_size):
            batch = all_markets[i:i + batch_size]
            
            try:
                # Upsert: insert or update if exists (based on 'id' primary key)
                response = self.client.table(self.TABLE_MARKETS).upsert(
                    batch,
                    on_conflict='id'
                ).execute()
                
                self.stats['markets_inserted'] += len(batch)
                print(f"  [OK] Batch {i//batch_size + 1}: {len(batch)} markets")
                
            except Exception as e:
                error_msg = f"Error uploading markets batch {i//batch_size + 1}: {e}"
                print(f"  [ERROR] {error_msg}")
                self.stats['errors'].append(error_msg)
    
    def _upload_markets_to_postgres(self, events: List[Dict], batch_size: int = 500) -> None:
        """Upload markets to local PostgreSQL"""
        from psycopg2.extras import execute_batch
        import json
        
        # Extract all markets from events
        all_markets = []
        for event in events:
            event_id = event.get('id')
            markets = event.get('markets', [])
            for market in markets:
                market_data = self.prepare_market_data(market, event_id)
                all_markets.append(market_data)
        
        print(f"\n[*] Uploading {len(all_markets)} markets to PostgreSQL...")
        
        conn = None
        cursor = None
        
        try:
            conn = self.psycopg2.connect(**self.connection_params)
            cursor = conn.cursor()
            
            # Prepare SQL for upsert (simplified - key fields only)
            insert_sql = f"""
                INSERT INTO {self.TABLE_MARKETS} (
                    id, event_id, question, condition_id, slug, question_id,
                    end_date, start_date, created_at, updated_at, closed_time,
                    image, icon, description, outcomes, outcome_prices,
                    volume, volume_num, volume24hr, liquidity, liquidity_num,
                    active, closed, new, featured, archived, restricted, enable_order_book,
                    neg_risk, ready, funded
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    question = EXCLUDED.question,
                    condition_id = EXCLUDED.condition_id,
                    updated_at = EXCLUDED.updated_at,
                    closed_time = EXCLUDED.closed_time,
                    outcome_prices = EXCLUDED.outcome_prices,
                    volume_num = EXCLUDED.volume_num,
                    volume24hr = EXCLUDED.volume24hr,
                    liquidity_num = EXCLUDED.liquidity_num,
                    active = EXCLUDED.active,
                    closed = EXCLUDED.closed
            """
            
            for i in range(0, len(all_markets), batch_size):
                batch = all_markets[i:i + batch_size]
                prepared_batch = []
                
                for market in batch:
                    # Convert arrays/objects to JSON strings
                    outcomes_str = json.dumps(market.get('outcomes')) if market.get('outcomes') else None
                    outcome_prices_str = json.dumps(market.get('outcome_prices')) if market.get('outcome_prices') else None
                    
                    prepared_batch.append((
                        market.get('id'),
                        market.get('event_id'),
                        market.get('question'),
                        market.get('condition_id'),
                        market.get('slug'),
                        market.get('question_id'),
                        market.get('end_date'),
                        market.get('start_date'),
                        market.get('created_at'),
                        market.get('updated_at'),
                        market.get('closed_time'),
                        market.get('image'),
                        market.get('icon'),
                        market.get('description'),
                        outcomes_str,
                        outcome_prices_str,
                        market.get('volume'),
                        market.get('volume_num'),
                        market.get('volume24hr'),
                        market.get('liquidity'),
                        market.get('liquidity_num'),
                        market.get('active', False),
                        market.get('closed', False),
                        market.get('new', False),
                        market.get('featured', False),
                        market.get('archived', False),
                        market.get('restricted', False),
                        market.get('enable_order_book', False),
                        market.get('neg_risk', False),
                        market.get('ready', False),
                        market.get('funded', False)
                    ))
                
                try:
                    execute_batch(cursor, insert_sql, prepared_batch, page_size=100)
                    conn.commit()
                    
                    self.stats['markets_inserted'] += len(prepared_batch)
                    print(f"  [OK] Batch {i//batch_size + 1}: {len(prepared_batch)} markets")
                    
                except Exception as e:
                    conn.rollback()
                    error_msg = f"Error uploading markets batch {i//batch_size + 1}: {e}"
                    print(f"  [ERROR] {error_msg}")
                    self.stats['errors'].append(error_msg)
            
        except Exception as e:
            if conn:
                conn.rollback()
            error_msg = f"Failed to upload markets to PostgreSQL: {e}"
            print(f"  [ERROR] {error_msg}")
            self.stats['errors'].append(error_msg)
            
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def upload_redemptions_batch(self, redemptions: List[Dict], chunk_size: int = None, use_new_client: bool = True) -> bool:
        """
        Upload a batch of redemptions to database (Supabase or local PostgreSQL)
        Splits large batches into smaller chunks to avoid timeouts
        
        Args:
            redemptions: List of redemption records to upload
            chunk_size: Size of each upload chunk (default: 100 for Supabase, 1000 for local PostgreSQL)
            use_new_client: If True, creates a new client instance (thread-safe for parallel uploads)
        
        Returns True if successful, False otherwise
        """
        if not redemptions:
            return True
        
        # Set default chunk_size based on database type
        if chunk_size is None:
            if self.use_local_db:
                # Локальная БД может быть СУПЕР быстрой - большие чанки!
                chunk_size = 5000  # TURBO MODE для PostgreSQL COPY
            else:
                # Always start with large chunks for speed
                # Will fallback to smaller chunks if errors occur
                chunk_size = 1000
        
        # Route to appropriate upload method
        if self.use_local_db:
            return self._upload_to_local_postgres(redemptions, chunk_size)
        else:
            return self._upload_to_supabase(redemptions, chunk_size, use_new_client)
    
    def _upload_to_supabase(self, redemptions: List[Dict], chunk_size: int, use_new_client: bool) -> bool:
        """Upload redemptions to Supabase with progressive chunking fallback"""
        # Create new client for thread-safe parallel uploads
        client = self.create_new_client() if use_new_client else self.client
        
        # Failed chunks that need retry with smaller size
        failed_chunks_data = []
        
        try:
            # Prepare redemptions data
            prepared_batch = []
            for redemption in redemptions:
                redemption_data = {
                    'transaction_hash': redemption.get('transaction_hash'),
                    'condition_id': redemption.get('condition_id'),
                    'event_id': redemption.get('event_id'),
                    'market_id': redemption.get('market_id'),
                    'market_question': redemption.get('market_question'),
                    'event_title': redemption.get('event_title'),
                    'redeemer_address': redemption.get('redeemer_address'),
                    'payout_usdc': float(redemption.get('payout_usdc', 0)),
                    'timestamp_unix': int(redemption.get('timestamp_unix', 0)),
                }
                
                # Convert timestamp_human to ISO format
                timestamp_human = redemption.get('timestamp_human')
                if timestamp_human:
                    try:
                        dt = datetime.strptime(timestamp_human, '%Y-%m-%d %H:%M:%S')
                        redemption_data['timestamp_human'] = dt.isoformat()
                    except:
                        redemption_data['timestamp_human'] = timestamp_human
                
                prepared_batch.append(redemption_data)
            
            # Split into chunks if batch is large
            total_uploaded = 0
            num_chunks = (len(prepared_batch) + chunk_size - 1) // chunk_size
            show_progress = len(prepared_batch) > chunk_size
            
            # Show settings
            if len(prepared_batch) > 1000:
                print(f"      🔧 Starting upload: {len(prepared_batch)} records, chunk_size={chunk_size}", flush=True)
            
            for i in range(0, len(prepared_batch), chunk_size):
                chunk = prepared_batch[i:i + chunk_size]
                chunk_num = i // chunk_size + 1
                
                # Retry logic for statement timeout
                max_retries = 2
                retry_count = 0
                chunk_uploaded = False
                
                while retry_count <= max_retries and not chunk_uploaded:
                    try:
                        # Show progress for large batches
                        if show_progress:
                            retry_suffix = f" (retry {retry_count})" if retry_count > 0 else ""
                            print(f"\n      📦 Chunk {chunk_num}/{num_chunks} ({len(chunk)} records){retry_suffix}...", end=" ", flush=True)
                        
                        # Upsert to database using the appropriate client
                        response = client.table(self.TABLE_REDEMPTIONS).upsert(
                            chunk,
                            on_conflict='transaction_hash,redeemer_address'
                        ).execute()
                        
                        total_uploaded += len(chunk)
                        self.stats['redemptions_inserted'] += len(chunk)
                        chunk_uploaded = True
                        
                        if show_progress:
                            success_msg = "✅"
                            if retry_count > 0:
                                success_msg = f"✅ (succeeded after {retry_count} retry)"
                            print(success_msg, flush=True)
                        
                        # Small delay between chunks
                        if show_progress and chunk_num < num_chunks:
                            import time
                            time.sleep(0.05)  # 50ms delay
                        
                    except Exception as chunk_error:
                        error_str = str(chunk_error)
                        error_type = type(chunk_error).__name__
                        
                        # Check if it's a statement timeout
                        if '57014' in error_str or 'statement timeout' in error_str.lower():
                            retry_count += 1
                            if retry_count <= max_retries:
                                if show_progress:
                                    print(f"⏳ timeout, retrying...", flush=True)
                                print(f"         📊 Chunk info: {chunk_num}/{num_chunks}, {len(chunk)} records")
                                print(f"         ⏰ Timeout after attempt {retry_count}, waiting 1s...")
                                import time
                                time.sleep(1)  # Wait 1 second before retry
                                continue
                            else:
                                # Max retries reached for timeout - save chunk for retry with smaller size
                                error_msg = f"Chunk {chunk_num} timeout, will retry with smaller chunk size"
                                print(f"\n      ⚠️  {error_msg}", flush=True)
                                failed_chunks_data.extend(chunk)  # Save failed chunk data
                                break  # Exit retry loop, move to next chunk
                        
                        # If not timeout - save chunk for retry with smaller size
                        error_msg = f"Chunk {chunk_num} failed ({error_type}), will retry with smaller chunk size"
                        print(f"\n      ⚠️  {error_msg}", flush=True)
                        failed_chunks_data.extend(chunk)  # Save failed chunk data
                        break  # Exit retry loop, move to next chunk
            
            # Progressive retry: if some chunks failed, retry with smaller chunk sizes
            if failed_chunks_data:
                print(f"\n      🔄 Retrying {len(failed_chunks_data)} failed records with progressive chunking...", flush=True)
                
                # Try progressively smaller chunk sizes: 500 → 100 → 50 → 25 → 10 → 1
                for retry_chunk_size in [500, 100, 50, 25, 10, 1]:
                    if not failed_chunks_data:
                        break  # All uploaded successfully
                    
                    if retry_chunk_size <= 10:
                        print(f"      📦 Attempt with chunk_size={retry_chunk_size} (extreme fallback)", flush=True)
                    else:
                        print(f"      📦 Attempt with chunk_size={retry_chunk_size}", flush=True)
                    still_failed = []
                    
                    for i in range(0, len(failed_chunks_data), retry_chunk_size):
                        retry_chunk = failed_chunks_data[i:i + retry_chunk_size]
                        retry_chunk_num = i // retry_chunk_size + 1
                        total_retry_chunks = (len(failed_chunks_data) + retry_chunk_size - 1) // retry_chunk_size
                        
                        try:
                            print(f"         Chunk {retry_chunk_num}/{total_retry_chunks} ({len(retry_chunk)} records)...", end=" ", flush=True)
                            
                            response = client.table(self.TABLE_REDEMPTIONS).upsert(
                                retry_chunk,
                                on_conflict='transaction_hash,redeemer_address'
                            ).execute()
                            
                            total_uploaded += len(retry_chunk)
                            self.stats['redemptions_inserted'] += len(retry_chunk)
                            print("✅", flush=True)
                            
                            import time
                            time.sleep(0.1)  # Small delay
                            
                        except Exception as retry_error:
                            print(f"❌", flush=True)
                            still_failed.extend(retry_chunk)  # Save for next attempt
                    
                    failed_chunks_data = still_failed
                    
                    if not failed_chunks_data:
                        print(f"      ✅ All failed records uploaded successfully with chunk_size={retry_chunk_size}!", flush=True)
                        break
                
                # If still have failed data after all attempts, save to file
                if failed_chunks_data:
                    import json
                    from datetime import datetime
                    failed_file = f"failed_uploads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                    with open(failed_file, 'w') as f:
                        json.dump(failed_chunks_data, f, indent=2)
                    print(f"\n      ⚠️  {len(failed_chunks_data)} records still failed after all retries", flush=True)
                    print(f"      💾 Saved to: {failed_file}", flush=True)
                    self.stats['errors'].append(f"{len(failed_chunks_data)} records saved to {failed_file}")
            
            return total_uploaded == len(prepared_batch)
            
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            print(f"\n      ❌ ERROR PREPARING REDEMPTIONS")
            print(f"         Type: {error_type}")
            print(f"         Total records: {len(redemptions)}")
            print(f"         Error: {error_msg[:250]}")
            
            # Try to identify problematic record
            try:
                if prepared_batch:
                    print(f"         Last prepared: {len(prepared_batch)} records")
                    print(f"         Failed on record: ~{len(prepared_batch) + 1}")
            except:
                pass
            
            full_error = f"Error preparing redemptions: {error_msg[:200]}"
            self.stats['errors'].append(full_error)
            return False
    
    def _upload_to_local_postgres(self, redemptions: List[Dict], chunk_size: int = 10000) -> bool:
        """
        Upload redemptions to local PostgreSQL database using COPY (ultra-fast bulk insert)
        
        Args:
            redemptions: List of redemption records
            chunk_size: Size of each batch (default: 10000 for maximum speed)
            
        Returns True if successful, False otherwise
        
        Performance: Uses PostgreSQL COPY which is 10-50x faster than INSERT batches
        """
        import io
        from psycopg2 import sql
        
        conn = None
        cursor = None
        
        try:
            # Connect to database
            conn = self.psycopg2.connect(**self.connection_params)
            cursor = conn.cursor()
            
            # Create temporary table for bulk insert
            temp_table = f"{self.TABLE_REDEMPTIONS}_temp_{os.getpid()}"
            
            try:
                # Create temp table with same structure
                cursor.execute(f"""
                    CREATE TEMP TABLE {temp_table} (LIKE {self.TABLE_REDEMPTIONS} INCLUDING ALL)
                    ON COMMIT DROP
                """)
                
                # Prepare data for COPY
                total_uploaded = 0
                num_chunks = (len(redemptions) + chunk_size - 1) // chunk_size
                show_progress = len(redemptions) > 1000
                
                if show_progress:
                    print(f"      🚀 TURBO MODE: Using PostgreSQL COPY (10-50x faster)", flush=True)
                    print(f"      📦 Processing {len(redemptions)} records in chunks of {chunk_size}", flush=True)
                
                for i in range(0, len(redemptions), chunk_size):
                    chunk = redemptions[i:i + chunk_size]
                    chunk_num = i // chunk_size + 1
                    
                    try:
                        if show_progress and num_chunks > 1:
                            print(f"      📦 Chunk {chunk_num}/{num_chunks} ({len(chunk)} records)...", end=" ", flush=True)
                        
                        # Prepare CSV data in memory
                        csv_buffer = io.StringIO()
                        for r in chunk:
                            # Escape special characters for PostgreSQL COPY
                            def escape_field(val):
                                if val is None:
                                    return '\\N'
                                val_str = str(val).replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
                                return val_str
                            
                            csv_buffer.write('\t'.join([
                                escape_field(r.get('transaction_hash')),
                                escape_field(r.get('condition_id')),
                                escape_field(r.get('event_id')),
                                escape_field(r.get('market_id')),
                                escape_field(r.get('market_question')),
                                escape_field(r.get('event_title', '')),
                                escape_field(r.get('redeemer_address')),
                                escape_field(float(r.get('payout_usdc', 0))),
                                escape_field(int(r.get('timestamp_unix', 0))),
                                escape_field(r.get('timestamp_human'))
                            ]) + '\n')
                        
                        csv_buffer.seek(0)
                        
                        # COPY to temp table (ultra-fast!)
                        cursor.copy_from(
                            csv_buffer, 
                            temp_table,
                            columns=['transaction_hash', 'condition_id', 'event_id', 'market_id',
                                   'market_question', 'event_title', 'redeemer_address', 'payout_usdc',
                                   'timestamp_unix', 'timestamp_human']
                        )
                        
                        total_uploaded += len(chunk)
                        
                        if show_progress and num_chunks > 1:
                            print("✅", flush=True)
                        
                    except Exception as chunk_error:
                        conn.rollback()
                        error_str = str(chunk_error)
                        error_type = type(chunk_error).__name__
                        print(f"\n      ❌ COPY ERROR on chunk {chunk_num}")
                        print(f"         Type: {error_type}")
                        print(f"         Error: {error_str[:200]}")
                        self.stats['errors'].append(error_str[:200])
                        return False
                
                # Insert from temp table to main table with ON CONFLICT (upsert)
                if show_progress:
                    print(f"      🔄 Merging {total_uploaded} records into main table...", end=" ", flush=True)
                
                cursor.execute(f"""
                    INSERT INTO {self.TABLE_REDEMPTIONS}
                    SELECT * FROM {temp_table}
                    ON CONFLICT (transaction_hash, redeemer_address) 
                    DO UPDATE SET
                        payout_usdc = EXCLUDED.payout_usdc,
                        timestamp_unix = EXCLUDED.timestamp_unix,
                        timestamp_human = EXCLUDED.timestamp_human
                """)
                
                conn.commit()
                self.stats['redemptions_inserted'] += total_uploaded
                
                if show_progress:
                    print("✅", flush=True)
                
                return True
                
            except Exception as e:
                conn.rollback()
                # Fallback to slower method if COPY fails
                error_msg = f"COPY method failed: {str(e)[:100]}"
                print(f"\n      ⚠️  {error_msg}")
                print(f"      🔄 Falling back to standard INSERT method...")
                return self._upload_to_local_postgres_fallback(redemptions, cursor, conn, chunk_size)
            
        except Exception as e:
            if conn:
                conn.rollback()
            
            error_type = type(e).__name__
            error_msg = str(e)
            print(f"\n      ❌ UPLOAD FAILED")
            print(f"         Type: {error_type}")
            print(f"         Error: {error_msg[:200]}")
            self.stats['errors'].append(error_msg[:200])
            return False
            
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def _upload_to_local_postgres_fallback(self, redemptions: List[Dict], cursor, conn, chunk_size: int) -> bool:
        """Fallback method using standard INSERT batches if COPY fails"""
        from psycopg2.extras import execute_batch
        
        try:
            # Prepare data as tuples
            prepared_batch = []
            for r in redemptions:
                try:
                    prepared_batch.append((
                        r['transaction_hash'],
                        r['condition_id'],
                        r['event_id'],
                        r['market_id'],
                        r['market_question'],
                        r.get('event_title', ''),
                        r['redeemer_address'],
                        float(r['payout_usdc']),
                        int(r['timestamp_unix']),
                        r['timestamp_human']
                    ))
                except Exception as prep_error:
                    print(f"\n      ⚠️  Skipping invalid record: {str(prep_error)}")
                    continue
            
            if not prepared_batch:
                return False
            
            # SQL with ON CONFLICT
            insert_sql = f"""
                INSERT INTO {self.TABLE_REDEMPTIONS} (
                    transaction_hash, condition_id, event_id, market_id,
                    market_question, event_title, redeemer_address, payout_usdc,
                    timestamp_unix, timestamp_human
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (transaction_hash, redeemer_address) 
                DO UPDATE SET
                    payout_usdc = EXCLUDED.payout_usdc,
                    timestamp_unix = EXCLUDED.timestamp_unix,
                    timestamp_human = EXCLUDED.timestamp_human
            """
            
            total_uploaded = 0
            for i in range(0, len(prepared_batch), chunk_size):
                chunk = prepared_batch[i:i + chunk_size]
                execute_batch(cursor, insert_sql, chunk, page_size=1000)
                conn.commit()
                total_uploaded += len(chunk)
                self.stats['redemptions_inserted'] += len(chunk)
            
            return total_uploaded == len(prepared_batch)
            
        except Exception as e:
            if conn:
                conn.rollback()
            
            error_type = type(e).__name__
            error_msg = str(e)
            print(f"\n      ❌ UPLOAD FAILED")
            print(f"         Type: {error_type}")
            print(f"         Error: {error_msg[:200]}")
            self.stats['errors'].append(error_msg[:200])
            return False
            
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
    
    def upload_metadata(self, metadata: Dict) -> None:
        """
        Upload metadata to Supabase
        Stores fetch metadata for tracking
        
        NOTE: Only works with Supabase, not local PostgreSQL
        """
        if self.use_local_db:
            print(f"\n⚠️  WARNING: upload_metadata() only works with Supabase")
            print(f"   Skipping metadata upload for local PostgreSQL")
            return
        
        print(f"\n[*] Uploading metadata to Supabase...")

        # Skip if metadata is empty or missing required fields
        if not metadata or not metadata.get('timestamp'):
            print(f"  [SKIP] Metadata is empty or missing required fields (timestamp)")
            return
        
        try:
            # Add timestamp as unique identifier
            metadata_record = {
                'timestamp': metadata.get('timestamp'),
                'total_events': metadata.get('total_events'),
                'fetch_method': metadata.get('fetch_method'),
                'filters': json.dumps(metadata.get('filters', {})),  # Store as JSON string
            }
            
            response = self.client.table(self.TABLE_METADATA).insert(metadata_record).execute()
            print(f"  [OK] Metadata uploaded")
            
        except Exception as e:
            error_msg = f"Error uploading metadata: {e}"
            print(f"  [ERROR] {error_msg}")
            self.stats['errors'].append(error_msg)
    
    def upload_json_file(self, filepath: str, include_metadata: bool = True) -> None:
        """
        Main method to upload entire JSON file to database
        
        Args:
            filepath: Path to JSON file
            include_metadata: Whether to upload metadata table (Supabase only)
        """
        db_name = "PostgreSQL" if self.use_local_db else "Supabase"
        print(f"[*] Starting {db_name} upload...")
        print("=" * 70)
        
        # Load data
        data = self.load_json_data(filepath)
        events = data.get('events', [])
        metadata = data.get('metadata', {})
        
        # Upload events
        self.upload_events(events)
        
        # Upload markets
        self.upload_markets(events)
        
        # Upload metadata (optional)
        if include_metadata:
            self.upload_metadata(metadata)
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print upload summary statistics"""
        print("\n" + "=" * 70)
        print("UPLOAD SUMMARY")
        print("=" * 70)
        if self.stats['events_inserted'] > 0:
            print(f"[OK] Events inserted/updated: {self.stats['events_inserted']}")
        if self.stats['markets_inserted'] > 0:
            print(f"[OK] Markets inserted/updated: {self.stats['markets_inserted']}")
        if self.stats['redemptions_inserted'] > 0:
            print(f"[OK] Redemptions inserted/updated: {self.stats['redemptions_inserted']}")

        if self.stats['errors']:
            print(f"\n[WARN] Errors encountered: {len(self.stats['errors'])}")
            for error in self.stats['errors'][:5]:  # Show first 5 errors
                print(f"   - {error}")
            if len(self.stats['errors']) > 5:
                print(f"   ... and {len(self.stats['errors']) - 5} more")
        else:
            print(f"\n[SUCCESS] Upload completed successfully with no errors!")
        
        print("=" * 70)


def main():
    """
    Main execution function for direct script usage
    
    Usage:
        python supabase_uploader.py [filepath] [--local] [--redemptions]
    
    Examples:
        python supabase_uploader.py                           # Latest JSON → Supabase
        python supabase_uploader.py --local                   # Latest JSON → PostgreSQL
        python supabase_uploader.py data.json                 # data.json → Supabase
        python supabase_uploader.py data.json --local         # data.json → PostgreSQL
        python supabase_uploader.py redeem.json --redemptions # Upload redemptions
    """
    import sys
    
    # Parse command line arguments
    args = sys.argv[1:]
    use_local_db = '--local' in args or '-l' in args
    is_redemptions = '--redemptions' in args or '-r' in args
    show_help = '--help' in args or '-h' in args
    
    # Remove flags from args to get filepath
    filepath = None
    for arg in args:
        if not arg.startswith('-'):
            filepath = arg
            break
    
    # Show help
    if show_help:
        print("Usage: python supabase_uploader.py [filepath] [OPTIONS]")
        print()
        print("Options:")
        print("  --local, -l         Upload to local PostgreSQL instead of Supabase")
        print("  --redemptions, -r   Upload as redemptions data (not events/markets)")
        print("  --help, -h          Show this help message")
        print()
        print("Examples:")
        print("  python supabase_uploader.py")
        print("  python supabase_uploader.py --local")
        print("  python supabase_uploader.py data.json")
        print("  python supabase_uploader.py data.json --local")
        print("  python supabase_uploader.py redeem.json --redemptions")
        return
    
    # Get filepath
    if not filepath:
        # Use latest JSON file in json_output directory
        json_dir = 'json_output'
        if os.path.exists(json_dir):
            json_files = [f for f in os.listdir(json_dir) if f.endswith('.json')]
            if json_files:
                # Sort by modification time, get latest
                json_files.sort(key=lambda x: os.path.getmtime(os.path.join(json_dir, x)), reverse=True)
                filepath = os.path.join(json_dir, json_files[0])
                print(f"[*] Using latest file: {filepath}")
            else:
                print("[ERROR] No JSON files found in json_output directory")
                return
        else:
            print("[ERROR] json_output directory not found")
            return
    
    if not os.path.exists(filepath):
        print(f"[ERROR] File not found: {filepath}")
        return
    
    # Create uploader
    db_type = "local PostgreSQL" if use_local_db else "Supabase"
    print(f"[*] Target database: {db_type}")
    
    try:
        uploader = SupabaseUploader(use_local_db=use_local_db)
        
        if is_redemptions:
            # Upload redemptions data
            print(f"[*] Loading redemptions from: {filepath}")
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Support two formats:
            # 1. Simple list: [{"transaction_hash": ..., "condition_id": ..., ...}, ...]
            # 2. Failed uploads format: [{"market_info": {...}, "redemptions": [...]}, ...]
            
            all_redemptions = []
            
            if isinstance(data, list):
                if len(data) > 0 and 'redemptions' in data[0]:
                    # Failed uploads format - extract redemptions from each market
                    print(f"[*] Detected failed_redemptions format with {len(data)} markets")
                    for item in data:
                        market_info = item.get('market_info', {})
                        redemptions = item.get('redemptions', [])
                        print(f"   • Market #{market_info.get('market_index', '?')}: {len(redemptions)} redemptions")
                        all_redemptions.extend(redemptions)
                else:
                    # Simple list format
                    all_redemptions = data
            else:
                print(f"[ERROR] Expected list of redemptions, got {type(data)}")
                return
            
            print(f"\n[*] Total redemptions to upload: {len(all_redemptions)}")
            print(f"[*] Uploading to {db_type}...")
            success = uploader.upload_redemptions_batch(all_redemptions)
            
            if success:
                print(f"[OK] Successfully uploaded {uploader.stats['redemptions_inserted']} redemptions")
            else:
                print(f"[ERROR] Failed to upload redemptions")
                if uploader.stats['errors']:
                    print(f"[ERRORS] {len(uploader.stats['errors'])} errors:")
                    for error in uploader.stats['errors'][:3]:
                        print(f"   - {error}")
        else:
            # Upload events/markets data
            uploader.upload_json_file(filepath)
            uploader.print_summary()
            
    except ValueError as e:
        print(f"[ERROR] Configuration error: {e}")
        if use_local_db:
            print("\nPlease create a .env file with:")
            print("  LOCAL_DB_HOST=localhost")
            print("  LOCAL_DB_PORT=5432")
            print("  LOCAL_DB_NAME=polymarket")
            print("  LOCAL_DB_USER=postgres")
            print("  LOCAL_DB_PASSWORD=your_password")
        else:
            print("\nPlease create a .env file with:")
            print("  SUPABASE_URL=your_supabase_url")
            print("  SUPABASE_KEY=your_supabase_service_role_key")
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == '__main__':
    main()