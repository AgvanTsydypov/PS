"""
Polymarket Trades API Client

Documentation: https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets

API Parameters:
- limit: 0-10000 (default: 100)
- offset: 0-10000 (default: 0)
- takerOnly: boolean (default: true) - IMPORTANT: set to false to get all trades!
- market: condition ID(s)
- user: user profile address
- side: BUY or SELL
- filterType: CASH or TOKENS (optional)
- filterAmount: number (optional)

KNOWN ISSUES:
- API returns different number of trades depending on batch_size (pagination bug)
- Each trade has 2 records (maker + taker), so volume = sum(size) / 2
- For most complete data, use batch_size between 100-500
"""

import requests
import json
import time

def get_trades_for_condition(condition_id, batch_size=1000, taker_only=False):
    """
    Fetch ALL trades for a specific condition ID from Polymarket API using pagination
    
    Args:
        condition_id: The condition ID to fetch trades for
        batch_size: Number of trades to fetch per request (default: 1000, max: 10000)
        taker_only: If True, only fetch taker trades. If False, fetch both maker and taker trades (default: False)
    """
    url = "https://data-api.polymarket.com/trades"
    
    all_trades = []
    seen_trades = set()  # Track unique trades by (tx_hash, wallet, side) to avoid duplicates
    offset = 0
    MAX_OFFSET = 10000  # API limit according to documentation
    
    print(f"Fetching all trades for condition ID: {condition_id}")
    print(f"Mode: {'Taker only' if taker_only else 'All trades (maker + taker)'}")
    print("=" * 80)
    
    try:
        while offset <= MAX_OFFSET:
            params = {
                "market": condition_id,
                "limit": batch_size,
                "offset": offset,
                "takerOnly": str(taker_only).lower()  # API expects lowercase boolean string
            }
            
            print(f"Fetching batch at offset {offset}...", end=" ")
            
            response = requests.get(url, params=params)
            response.raise_for_status()
            
            trades = response.json()
            
            if not trades:
                print("No more trades found.")
                break
            
            # Filter out duplicates based on unique combination of tx_hash + wallet + side
            # This is important because one transaction can have both maker and taker sides
            new_trades = []
            duplicates = 0
            for trade in trades:
                tx_hash = trade.get('transactionHash')
                wallet = trade.get('proxyWallet')
                side = trade.get('side')
                # Create unique identifier for this specific trade
                trade_id = (tx_hash, wallet, side)
                
                if trade_id not in seen_trades:
                    seen_trades.add(trade_id)
                    new_trades.append(trade)
                else:
                    duplicates += 1
            
            print(f"Got {len(trades)} trades ({len(new_trades)} new, {duplicates} duplicates)")
            
            if not new_trades:
                print("No new unique trades found. Stopping.")
                break
            
            all_trades.extend(new_trades)
            
            # If we got fewer trades than the limit, we've reached the end
            if len(trades) < batch_size:
                print("Reached end of data.")
                break
            
            offset += batch_size
            
            # Stop if we've reached the API's maximum offset
            if offset > MAX_OFFSET:
                print(f"Reached maximum API offset ({MAX_OFFSET}). Stopping.")
                break
            
            # Small delay to avoid rate limiting
            time.sleep(0.1)
        
        print("=" * 80)
        print(f"\nTotal trades fetched: {len(all_trades)}\n")
        
        # Print summary
        if all_trades:
            print("Trade Summary (first 10):")
            print("-" * 80)
            for i, trade in enumerate(all_trades[:10], 1):
                print(f"\nTrade #{i}:")
                print(f"  Side: {trade.get('side')}")
                print(f"  Size: {trade.get('size')}")
                print(f"  Price: {trade.get('price')}")
                print(f"  Outcome: {trade.get('outcome')}")
                print(f"  User: {trade.get('proxyWallet')}")
                print(f"  Timestamp: {trade.get('timestamp')}")
                print(f"  TX Hash: {trade.get('transactionHash')}")
            
            if len(all_trades) > 10:
                print(f"\n... and {len(all_trades) - 10} more trades")
        
        # Save to file
        output_file = "output/trades_result.json"
        with open(output_file, 'w') as f:
            json.dump(all_trades, f, indent=2)
        print(f"\nFull results saved to: {output_file}")
        
        return all_trades
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching trades: {e}")
        if all_trades:
            print(f"Returning {len(all_trades)} trades fetched so far...")
            return all_trades
        return None

if __name__ == "__main__":
    # Specific condition ID
    CONDITION_ID = "0x3f792e24afd3c00da763487b5156a728138f52129d7ef303ad827d36bf4fee85"
    
    # Fetch all trades using pagination
    # IMPORTANT: Set taker_only=False to get BOTH maker and taker trades for correct volume
    # NOTE: batch_size=100 is recommended due to API pagination issues with larger sizes
    trades = get_trades_for_condition(CONDITION_ID, batch_size=100, taker_only=False)
    
    print(f"\n⚠️ WARNING: Polymarket API has pagination issues!")
    print(f"Different batch_size values may return different number of trades.")
    print(f"For most complete data, use smaller batch sizes (100-500).")