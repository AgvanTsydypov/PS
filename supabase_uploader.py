"""
Supabase Data Uploader for Polymarket Events
Uploads events and markets data from JSON to Supabase tables
"""

import json
import os
from datetime import datetime
from typing import List, Dict, Optional
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class SupabaseUploader:
    """Handles uploading Polymarket data to Supabase"""
    
    def __init__(self):
        """Initialize Supabase client"""
        self.supabase_url = os.getenv('SUPABASE_URL')
        self.supabase_key = os.getenv('SUPABASE_KEY')
        
        if not self.supabase_url or not self.supabase_key:
            raise ValueError(
                "Missing Supabase credentials. "
                "Please set SUPABASE_URL and SUPABASE_KEY in .env file"
            )
        
        self.client: Client = create_client(self.supabase_url, self.supabase_key)
        self.stats = {
            'events_inserted': 0,
            'events_updated': 0,
            'markets_inserted': 0,
            'markets_updated': 0,
            'errors': []
        }
    
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
        Upload events to Supabase in batches
        Uses upsert to handle duplicates
        """
        print(f"\n[*] Uploading {len(events)} events to Supabase...")
        
        for i in range(0, len(events), batch_size):
            batch = events[i:i + batch_size]
            prepared_batch = [self.prepare_event_data(event) for event in batch]
            
            try:
                # Upsert: insert or update if exists (based on 'id' primary key)
                response = self.client.table('events').upsert(
                    prepared_batch,
                    on_conflict='id'
                ).execute()
                
                self.stats['events_inserted'] += len(prepared_batch)
                print(f"  [OK] Batch {i//batch_size + 1}: {len(prepared_batch)} events")
                
            except Exception as e:
                error_msg = f"Error uploading events batch {i//batch_size + 1}: {e}"
                print(f"  [ERROR] {error_msg}")
                self.stats['errors'].append(error_msg)
    
    def upload_markets(self, events: List[Dict], batch_size: int = 100) -> None:
        """
        Upload markets to Supabase in batches
        Extracts markets from events and links them via event_id
        """
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
                response = self.client.table('markets').upsert(
                    batch,
                    on_conflict='id'
                ).execute()
                
                self.stats['markets_inserted'] += len(batch)
                print(f"  [OK] Batch {i//batch_size + 1}: {len(batch)} markets")
                
            except Exception as e:
                error_msg = f"Error uploading markets batch {i//batch_size + 1}: {e}"
                print(f"  [ERROR] {error_msg}")
                self.stats['errors'].append(error_msg)
    
    def upload_metadata(self, metadata: Dict) -> None:
        """
        Upload metadata to Supabase
        Stores fetch metadata for tracking
        """
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
            
            response = self.client.table('fetch_metadata').insert(metadata_record).execute()
            print(f"  [OK] Metadata uploaded")
            
        except Exception as e:
            error_msg = f"Error uploading metadata: {e}"
            print(f"  [ERROR] {error_msg}")
            self.stats['errors'].append(error_msg)
    
    def upload_json_file(self, filepath: str, include_metadata: bool = True) -> None:
        """
        Main method to upload entire JSON file to Supabase
        
        Args:
            filepath: Path to JSON file
            include_metadata: Whether to upload metadata table
        """
        print("[*] Starting Supabase upload...")
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
        print(f"[OK] Events inserted/updated: {self.stats['events_inserted']}")
        print(f"[OK] Markets inserted/updated: {self.stats['markets_inserted']}")
        
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
    """Main execution function"""
    import sys
    
    # Get filepath from command line argument or use default
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    else:
        # Use latest JSON file in json_output directory
        json_dir = 'json_output'
        if os.path.exists(json_dir):
            json_files = [f for f in os.listdir(json_dir) if f.endswith('.json')]
            if json_files:
                # Sort by modification time, get latest
                json_files.sort(key=lambda x: os.path.getmtime(os.path.join(json_dir, x)), reverse=True)
                filepath = os.path.join(json_dir, json_files[0])
            else:
                print("[ERROR] No JSON files found in json_output directory")
                return
        else:
            print("[ERROR] json_output directory not found")
            return
    
    if not os.path.exists(filepath):
        print(f"[ERROR] File not found: {filepath}")
        return
    
    # Create uploader and upload
    try:
        uploader = SupabaseUploader()
        uploader.upload_json_file(filepath)
    except ValueError as e:
        print(f"[ERROR] Configuration error: {e}")
        print("\nPlease create a .env file with:")
        print("  SUPABASE_URL=your_supabase_url")
        print("  SUPABASE_KEY=your_supabase_service_role_key")
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        raise


if __name__ == '__main__':
    main()

