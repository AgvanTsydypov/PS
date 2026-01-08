"""
Advanced script with configurable filters for fetching historical events
Uses settings from fetch_events_config.py
"""

import json
import time
import os
from datetime import datetime
from typing import List, Dict, Optional
from polymarket_client import PolymarketClient
import fetch_events_config as config


class EventFetcher:
    """Class to handle fetching and filtering Polymarket events"""
    
    def __init__(self):
        self.client = PolymarketClient()
        self.stats = {
            'total_fetched': 0,
            'total_filtered': 0,
            'total_filtered_by_date': 0,
            'total_filtered_by_volume': 0,
            'total_filtered_by_status': 0,
            'pages_processed': 0,
            'start_time': None,
            'end_time': None
        }
    
    def fetch_all_events(self) -> List[Dict]:
        """
        Fetch all events matching configured criteria
        
        Returns:
            List of filtered event dictionaries
        """
        all_events = []
        offset = 0
        page_num = 1
        
        self.stats['start_time'] = datetime.now()
        
        print("🌟 PolyStars - Historical Events Fetcher (Advanced)")
        print("=" * 70)
        print(f"📋 Configuration:")
        print(f"   • Minimum Volume: ${config.MIN_VOLUME:,.0f}")
        print(f"   • Closed Only: {config.CLOSED_ONLY}")
        print(f"   • Resolution Status: {config.RESOLUTION_STATUS}")
        print(f"   • Batch Size: {config.BATCH_SIZE}")
        print(f"   • Max Events: {config.MAX_EVENTS or 'Unlimited'}")
        
        # Display date range
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
        
        while True:
            # Check if we've reached max events limit
            if config.MAX_EVENTS and len(all_events) >= config.MAX_EVENTS:
                print(f"✅ Reached maximum events limit: {config.MAX_EVENTS}")
                break
            
            try:
                print(f"📥 Page {page_num} (offset: {offset})...", end=" ", flush=True)
                
                # Fetch batch
                events = self.client.get_events(
                    limit=config.BATCH_SIZE,
                    offset=offset,
                    closed=config.CLOSED_ONLY,
                    order='id',
                    ascending=False
                )
                
                if not events or len(events) == 0:
                    print("✅ No more events")
                    break
                
                self.stats['total_fetched'] += len(events)
                print(f"fetched {len(events)}", end=" → ", flush=True)
                
                # Filter events
                filtered = self._filter_events(events)
                all_events.extend(filtered)
                self.stats['total_filtered'] += len(filtered)
                
                print(f"✓ {len(filtered)} matched")
                
                self.stats['pages_processed'] += 1
                
                # Check if we should continue
                if len(events) < config.BATCH_SIZE:
                    print("✅ Reached end of results")
                    break
                
                # Move to next page
                offset += config.BATCH_SIZE
                page_num += 1
                
                # Rate limiting delay
                if config.REQUEST_DELAY > 0:
                    time.sleep(config.REQUEST_DELAY)
                
            except Exception as e:
                print(f"\n❌ Error on page {page_num}: {e}")
                print(f"⚠️  Stopping pagination. Collected {len(all_events)} events so far.")
                # Save what we have collected so far instead of failing completely
                break
        
        self.stats['end_time'] = datetime.now()
        return all_events
    
    def _filter_events(self, events: List[Dict]) -> List[Dict]:
        """
        Filter events by configured criteria
        
        Args:
            events: List of events to filter
            
        Returns:
            List of events matching criteria
        """
        filtered = []
        
        for event in events:
            # Date range check
            if not self._check_date_range(event):
                self.stats['total_filtered_by_date'] += 1
                continue
            
            # Volume check
            volume = self._get_volume(event)
            if volume < config.MIN_VOLUME:
                self.stats['total_filtered_by_volume'] += 1
                continue
            
            # Resolution status check
            if not self._check_resolution_status(event, config.RESOLUTION_STATUS):
                self.stats['total_filtered_by_status'] += 1
                continue
            
            filtered.append(event)
        
        return filtered
    
    def _get_volume(self, event: Dict) -> float:
        """Extract and convert volume to float"""
        volume = event.get('volume', 0)
        if isinstance(volume, str):
            try:
                volume = float(volume)
            except (ValueError, TypeError):
                volume = 0
        return float(volume) if volume else 0.0
    
    def _check_resolution_status(self, event: Dict, required_status: str) -> bool:
        """
        Check if event has the required resolution status
        Checks both event-level and market-level status
        """
        # Check event-level status
        event_status = event.get('umaResolutionStatus', '')
        if event_status == required_status:
            return True
        
        # Check market-level status
        markets = event.get('markets', [])
        for market in markets:
            market_status = market.get('umaResolutionStatus', '')
            if market_status == required_status:
                return True
        
        return False
    
    def _check_date_range(self, event: Dict) -> bool:
        """
        Check if event falls within the configured date range
        Uses endDate field from event
        """
        # If no date filtering is configured, accept all
        if config.START_DATE is None and config.END_DATE is None:
            return True
        
        # Get event end date
        end_date_str = event.get('endDate') or event.get('endDateIso')
        if not end_date_str:
            # If no date available, include the event
            return True
        
        try:
            # Parse the date string (format: "2024-12-31T23:59:59Z" or "2024-12-31")
            if 'T' in end_date_str:
                event_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            else:
                event_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            
            # Make event_date timezone-naive for comparison
            if event_date.tzinfo is not None:
                event_date = event_date.replace(tzinfo=None)
            
            # Check against start date
            if config.START_DATE and event_date < config.START_DATE:
                return False
            
            # Check against end date
            if config.END_DATE and event_date > config.END_DATE:
                return False
            
            return True
            
        except (ValueError, AttributeError) as e:
            # If date parsing fails, include the event
            print(f"⚠️  Warning: Could not parse date '{end_date_str}': {e}")
            return True
    
    def save_to_json(self, events: List[Dict], filename: Optional[str] = None) -> str:
        """
        Save events to JSON file
        
        Args:
            events: List of events to save
            filename: Optional custom filename
            
        Returns:
            Path to saved file
        """
        # Generate filename if not provided
        if filename is None:
            filename = config.OUTPUT_FILENAME
        
        if filename is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'polymarket_events_{timestamp}.json'
        
        # Ensure output directory exists
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        filepath = os.path.join(config.OUTPUT_DIR, filename)
        
        # Calculate statistics
        total_volume = sum(self._get_volume(e) for e in events)
        
        # Prepare stats with serializable values
        serializable_stats = self.stats.copy()
        if serializable_stats.get('start_time'):
            serializable_stats['start_time'] = serializable_stats['start_time'].isoformat()
        if serializable_stats.get('end_time'):
            serializable_stats['end_time'] = serializable_stats['end_time'].isoformat()
        
        # Prepare output
        output = {
            'metadata': {
                'timestamp': datetime.now().isoformat(),
                'total_events': len(events),
                'total_volume': total_volume,
                'filters': {
                    'min_volume': config.MIN_VOLUME,
                    'closed': config.CLOSED_ONLY,
                    'resolution_status': config.RESOLUTION_STATUS,
                    'start_date': config.START_DATE.isoformat() if config.START_DATE else None,
                    'end_date': config.END_DATE.isoformat() if config.END_DATE else None
                },
                'stats': serializable_stats
            },
            'events': events
        }
        
        # Save to file
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        
        return filepath
    
    def print_summary(self, events: List[Dict]):
        """Print summary statistics"""
        print("\n" + "=" * 70)
        print("📈 SUMMARY")
        print("=" * 70)
        
        duration = (self.stats['end_time'] - self.stats['start_time']).total_seconds()
        
        print(f"⏱️  Duration: {duration:.2f} seconds")
        print(f"📄 Pages processed: {self.stats['pages_processed']}")
        print(f"📊 Total events fetched: {self.stats['total_fetched']}")
        print(f"✅ Events matched all filters: {self.stats['total_filtered']}")
        print(f"\n🔍 Filtering breakdown:")
        print(f"   • Excluded by date range: {self.stats['total_filtered_by_date']}")
        print(f"   • Excluded by volume: {self.stats['total_filtered_by_volume']}")
        print(f"   • Excluded by resolution status: {self.stats['total_filtered_by_status']}")
        
        if events:
            volumes = [self._get_volume(e) for e in events]
            print(f"\n💰 Volume Statistics:")
            print(f"   • Total: ${sum(volumes):,.2f}")
            print(f"   • Average: ${sum(volumes)/len(volumes):,.2f}")
            print(f"   • Max: ${max(volumes):,.2f}")
            print(f"   • Min: ${min(volumes):,.2f}")
            
            print(f"\n📋 Sample Events (first 5):")
            for i, event in enumerate(events[:5], 1):
                title = event.get('title', 'N/A')
                volume = self._get_volume(event)
                print(f"   {i}. {title[:55]}... (${volume:,.0f})")
        
        print("=" * 70)


def main():
    """Main execution function"""
    fetcher = EventFetcher()
    
    # Fetch events
    events = fetcher.fetch_all_events()
    
    # Print summary
    fetcher.print_summary(events)
    
    # Save to file
    if events:
        try:
            filepath = fetcher.save_to_json(events)
            print(f"\n💾 Saved {len(events)} events to: {filepath}")
            print(f"✅ Done!")
        except Exception as e:
            print(f"\n❌ Error saving to JSON: {e}")
            print(f"⚠️  Attempting to save to backup file...")
            try:
                # Try to save with a simpler format
                import json
                backup_filename = f'polymarket_events_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
                with open(backup_filename, 'w', encoding='utf-8') as f:
                    json.dump({'events': events, 'count': len(events)}, f, indent=2, default=str)
                print(f"💾 Saved to backup file: {backup_filename}")
            except Exception as backup_error:
                print(f"❌ Backup save also failed: {backup_error}")
    else:
        print("\n⚠️  No events found matching the criteria")


if __name__ == '__main__':
    main()

