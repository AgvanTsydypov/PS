import requests
import json
import time

# ==========================================
# CONFIGURATION
# ==========================================
GRAPH_URL = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/activity-subgraph/0.0.4/gn"

TARGET_MARKET = {
    "name": "Oregon vs. Indiana",
    "id": "0x99cc3bbbe311e157297c096850255c688c60c3b30eeeb9d673c67cfc0b49ed24"
}

# ==========================================
# PAGINATION LOGIC
# ==========================================
def fetch_all_redemptions(condition_id):
    all_events = []
    last_id = "0x00" # Start from the beginning
    
    print(f"📥 Starting download for market: {condition_id[:10]}...")
    
    while True:
        # We sort by ID ascending and ask for IDs > last_id
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
        
        try:
            response = requests.post(GRAPH_URL, json={'query': query, 'variables': variables})
            data = response.json()
            
            # Check for errors
            if data.get('errors'):
                print(f"❌ API Error: {data.get('errors')[0]['message']}")
                break
                
            batch = data.get('data', {}).get('redemptions', [])
            
            if not batch:
                print("   ✅ Reached end of data.")
                break
                
            # Add batch to main list
            all_events.extend(batch)
            count = len(batch)
            print(f"   + Fetched {count} events (Total: {len(all_events)})")
            
            # Update cursor for next loop
            last_id = batch[-1]['id']
            
            # Safety break for huge datasets (optional, remove if you want millions)
            if len(all_events) > 100000:
                print("⚠️ Safety limit reached (100k). Stopping.")
                break
                
        except Exception as e:
            print(f"🚨 Connection Failed: {e}")
            time.sleep(1) # Retry wait
            
    return all_events

# ==========================================
# MAIN EXECUTION
# ==========================================
print(f"🚀 Processing: {TARGET_MARKET['name']}")

# 1. Fetch All Data
raw_data = fetch_all_redemptions(TARGET_MARKET['id'])

# 2. Process & Clean
cleaned_data = []
unique_winners = set()
total_payout = 0.0

print("\n🧹 Cleaning data...")

for event in raw_data:
    # Extract clean TX hash
    raw_id = event.get('id', "")
    tx_hash = raw_id.split('-')[0] if '-' in raw_id else raw_id
    
    # Format amount
    amount = float(event['payout']) / 1e6
    
    # Add to totals
    unique_winners.add(event['redeemer'])
    total_payout += amount
    
    # Create clean record for JSON
    cleaned_data.append({
        "transaction_hash": tx_hash,
        "condition_id": TARGET_MARKET['id'],
        "market_question": TARGET_MARKET['name'],
        "redeemer_address": event['redeemer'],
        "payout_usdc": amount,
        "timestamp_unix": event['timestamp'],
        "timestamp_human": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(event['timestamp'])))
    })

# 3. Print Stats
print(f"\n📊 FINAL STATS:")
print(f"   Total Events:   {len(cleaned_data)}")
print(f"   Unique Wallets: {len(unique_winners)}")
print(f"   Total Volume:   ${total_payout:,.2f}")

# 4. Save to JSON
filename = "market_winners.json"
print(f"\n💾 Saving to {filename}...")
with open(filename, 'w') as f:
    json.dump(cleaned_data, f, indent=4)

print("🎉 Done! Open 'market_winners.json' to see the list.")