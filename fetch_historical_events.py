"""
Script to fetch historical resolved events from Polymarket with filters
- Fetches all closed events with volume > $500,000
- Filters for events with umaResolutionStatus = "resolved"
- Date range: Last 1 year from now (default)
- Saves results to JSON file
"""

import json
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from polymarket_client import PolymarketClient


def fetch_all_events_with_filters(
    client: PolymarketClient,
    min_volume: float = 500000,
    closed: bool = True,
    batch_size: int = 100,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None
) -> List[Dict]:
    """
    Fetch all events matching criteria using pagination
    
    Args:
        client: PolymarketClient instance
        min_volume: Minimum volume threshold (default: 500,000)
        closed: Only fetch closed events (default: True)
        batch_size: Number of events per API request (default: 100)
        start_date: Start date for filtering (default: 1 year ago)
        end_date: End date for filtering (default: now)
        
    Returns:
        List of filtered event dictionaries
    """
    all_events = []
    offset = 0
    page_num = 1
    
    # Set default date range if not provided
    if end_date is None:
        end_date = datetime.now()
    if start_date is None:
        start_date = end_date - timedelta(days=365)
    
    print(f"🔍 Fetching closed events with volume > ${min_volume:,.0f}")
    print(f"📅 Date range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    print(f"📊 Using batch size: {batch_size}")
    print("-" * 60)
    
    while True:
        try:
            print(f"📥 Fetching page {page_num} (offset: {offset})...", end=" ")
            
            # Fetch batch of events
            events = client.get_events(
                limit=batch_size,
                offset=offset,
                closed=closed,
                order='id',
                ascending=False  # Get newest first
            )
            
            # Check if we got any results
            if not events or len(events) == 0:
                print("✅ No more events")
                break
            
            print(f"Got {len(events)} events")
            
            # Filter events by criteria
            filtered_count = 0
            for event in events:
                # Check date range
                event_date = _get_event_date(event)
                if event_date:
                    if event_date < start_date or event_date > end_date:
                        continue
                
                # Check volume threshold
                volume = event.get('volume', 0)
                if isinstance(volume, str):
                    try:
                        volume = float(volume)
                    except (ValueError, TypeError):
                        volume = 0
                
                # Check if volume meets threshold
                if volume < min_volume:
                    continue
                
                # Check umaResolutionStatus
                # Some events have markets with this status, check both event and markets
                uma_status = event.get('umaResolutionStatus', '')
                
                # Also check markets within the event
                markets = event.get('markets', [])
                has_resolved_market = False
                
                if markets:
                    for market in markets:
                        market_uma_status = market.get('umaResolutionStatus', '')
                        if market_uma_status == 'resolved':
                            has_resolved_market = True
                            break
                
                # Include if event or any of its markets are resolved
                if uma_status == 'resolved' or has_resolved_market:
                    all_events.append(event)
                    filtered_count += 1
            
            print(f"   ✓ {filtered_count} events matched filters")
            
            # Check if we should continue
            if len(events) < batch_size:
                print("✅ Reached end of results")
                break
            
            # Move to next page
            offset += batch_size
            page_num += 1
            
            # Small delay to avoid rate limiting
            time.sleep(0.1)
            
        except Exception as e:
            print(f"❌ Error on page {page_num}: {e}")
            break
    
    return all_events


def _get_event_date(event: Dict) -> Optional[datetime]:
    """
    Extract and parse event date
    
    Args:
        event: Event dictionary
        
    Returns:
        Datetime object or None if parsing fails
    """
    end_date_str = event.get('endDate') or event.get('endDateIso')
    if not end_date_str:
        return None
    
    try:
        # Parse the date string (format: "2024-12-31T23:59:59Z" or "2024-12-31")
        if 'T' in end_date_str:
            event_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
        else:
            event_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        
        # Make timezone-naive for comparison
        if event_date.tzinfo is not None:
            event_date = event_date.replace(tzinfo=None)
        
        return event_date
    except (ValueError, AttributeError):
        return None


def save_events_to_json(events: List[Dict], filename: str = None):
    """
    Save events to JSON file with metadata
    
    Args:
        events: List of event dictionaries
        filename: Output filename (default: auto-generated with timestamp)
    """
    if filename is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'polymarket_events_{timestamp}.json'
    
    # Prepare output data with metadata
    output = {
        'metadata': {
            'timestamp': datetime.now().isoformat(),
            'total_events': len(events),
            'filters': {
                'closed': True,
                'min_volume': 500000,
                'umaResolutionStatus': 'resolved',
                'date_range': 'Last 1 year'
            }
        },
        'events': events
    }
    
    # Calculate total volume
    total_volume = sum(
        float(e.get('volume', 0)) if isinstance(e.get('volume'), (int, float, str)) else 0 
        for e in events
    )
    output['metadata']['total_volume'] = total_volume
    
    # Save to file
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 Saved {len(events)} events to: {filename}")
    print(f"📊 Total volume: ${total_volume:,.2f}")
    
    return filename


def print_summary(events: List[Dict]):
    """Print summary statistics of fetched events"""
    if not events:
        print("No events found matching criteria")
        return
    
    print("\n" + "=" * 60)
    print("📈 SUMMARY")
    print("=" * 60)
    print(f"Total events found: {len(events)}")
    
    # Calculate statistics
    volumes = []
    for e in events:
        vol = e.get('volume', 0)
        if isinstance(vol, str):
            try:
                vol = float(vol)
            except:
                vol = 0
        volumes.append(vol)
    
    if volumes:
        print(f"Total volume: ${sum(volumes):,.2f}")
        print(f"Average volume: ${sum(volumes)/len(volumes):,.2f}")
        print(f"Max volume: ${max(volumes):,.2f}")
        print(f"Min volume: ${min(volumes):,.2f}")
    
    # Sample events
    print(f"\n📋 Sample events (first 5):")
    for i, event in enumerate(events[:5], 1):
        title = event.get('title', 'N/A')
        volume = event.get('volume', 0)
        print(f"  {i}. {title[:60]}... (${float(volume):,.0f})")
    
    print("=" * 60)


def main():
    """Main execution function"""
    print("🌟 PolyStars - Historical Events Fetcher")
    print("=" * 60)
    
    # Initialize client
    client = PolymarketClient()
    
    # Fetch events with filters
    events = fetch_all_events_with_filters(
        client=client,
        min_volume=500000,  # $500,000 minimum volume
        closed=True,
        batch_size=100  # Fetch 100 events per request
    )
    
    # Print summary
    print_summary(events)
    
    # Save to JSON file
    if events:
        filename = save_events_to_json(events)
        print(f"\n✅ Done! Results saved to: {filename}")
    else:
        print("\n⚠️ No events found matching the criteria")


if __name__ == '__main__':
    main()

