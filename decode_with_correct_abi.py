import json
from web3 import Web3
from eth_abi import decode

def clean_hex(h):
    return h.replace("0x", "")

def decode_address_from_topic(topic):
    """Extract address from a 32-byte padded topic"""
    topic_hex = topic.hex() if isinstance(topic, bytes) else topic
    return Web3.to_checksum_address("0x" + topic_hex[-40:])

def decode_log_by_structure(log):
    """Decode based on observed structure patterns"""
    event_sig = log['topics'][0]
    num_topics = len(log['topics'])
    raw_data = bytes.fromhex(clean_hex(log['data']))
    data_len = len(raw_data)
    
    try:
        # Pattern 1: 2 topics, 64 bytes data (conditionId + amount)
        # This appears to be some kind of simple redemption/payout
        if num_topics == 2 and data_len == 64:
            redeemer = decode_address_from_topic(log['topics'][1])
            decoded = decode(['bytes32', 'uint256'], raw_data)
            
            return {
                "event": "SimpleRedemption",
                "redeemer": redeemer,
                "conditionId": "0x" + decoded[0].hex(),
                "amount": decoded[1],
                "_eventSignature": event_sig
            }
        
        # Pattern 2: 4 topics, 160 bytes data (probably PayoutRedemption or PositionsMerge)
        # Data structure: 5 uint256 values
        elif num_topics == 4 and data_len == 160:
            # Extract indexed params from topics
            stakeholder = decode_address_from_topic(log['topics'][1])
            collateral = decode_address_from_topic(log['topics'][2])
            parent_id = log['topics'][3]
            
            # Decode data as 5 separate uint256 values (not arrays)
            decoded = decode(['uint256', 'bytes32', 'uint256', 'uint256', 'uint256'], raw_data)
            
            return {
                "event": "ComplexRedemption",
                "stakeholder": stakeholder,
                "collateralToken": collateral,
                "parentCollectionId": parent_id,
                "partition": decoded[0],
                "conditionId": "0x" + decoded[1].hex(),
                "value1": decoded[2],
                "value2": decoded[3],
                "value3": decoded[4],
                "_eventSignature": event_sig
            }
        
        # Pattern 3: 4 topics, 128 bytes data
        elif num_topics == 4 and data_len == 128:
            stakeholder = decode_address_from_topic(log['topics'][1])
            collateral = decode_address_from_topic(log['topics'][2])
            parent_id = log['topics'][3]
            
            decoded = decode(['uint256', 'bytes32', 'uint256', 'uint256'], raw_data)
            
            return {
                "event": "MediumRedemption",
                "stakeholder": stakeholder,
                "collateralToken": collateral,
                "parentCollectionId": parent_id,
                "partition": decoded[0],
                "conditionId": "0x" + decoded[1].hex(),
                "value1": decoded[2],
                "value2": decoded[3],
                "_eventSignature": event_sig
            }
        
        # Pattern 4: 3 topics, 128 bytes data
        elif num_topics == 3 and data_len == 128:
            operator = decode_address_from_topic(log['topics'][1])
            from_addr = decode_address_from_topic(log['topics'][2])
            
            decoded = decode(['bytes32', 'uint256', 'uint256', 'uint256'], raw_data)
            
            return {
                "event": "TransferOrMerge",
                "operator": operator,
                "from": from_addr,
                "conditionId": "0x" + decoded[0].hex(),
                "tokenId": decoded[1],
                "amount1": decoded[2],
                "amount2": decoded[3],
                "_eventSignature": event_sig
            }
        
        else:
            return {
                "event": "Unknown",
                "numTopics": num_topics,
                "dataLength": data_len,
                "eventSignature": event_sig,
                "rawData": log['data']
            }
            
    except Exception as e:
        return {
            "event": "DecodeError",
            "error": str(e),
            "numTopics": num_topics,
            "dataLength": data_len,
            "eventSignature": event_sig
        }

def main():
    # Load logs
    print("Loading output/rpc_logs_result.json...")
    with open('output/rpc_logs_result.json', 'r') as f:
        data = json.load(f)
    
    logs = data['result']
    print(f"Found {len(logs)} logs\n")
    
    # Decode all
    decoded_logs = []
    for i, log in enumerate(logs):
        decoded = decode_log_by_structure(log)
        
        # Add metadata
        decoded['_meta'] = {
            "index": i,
            "address": log['address'],
            "blockNumber": int(log['blockNumber'], 16),
            "transactionHash": log['transactionHash'],
            "logIndex": int(log['logIndex'], 16),
            "blockTimestamp": int(log['blockTimestamp'], 16) if 'blockTimestamp' in log else None
        }
        
        decoded_logs.append(decoded)
    
    print(f"Decoded {len(decoded_logs)} logs\n")
    
    # Save
    with open('output/decoded_logs_final.json', 'w') as f:
        json.dump(decoded_logs, f, indent=2, default=str)
    
    print("Saved to: output/decoded_logs_final.json")
    
    # Summary
    print("\n=== Event Summary ===")
    event_types = {}
    for log in decoded_logs:
        event = log.get('event', 'Unknown')
        event_types[event] = event_types.get(event, 0) + 1
    
    for event, count in sorted(event_types.items(), key=lambda x: x[1], reverse=True):
        print(f"  {event}: {count}")
    
    # Show samples
    print("\n=== Sample Decoded Events ===\n")
    for event_type in ['SimpleRedemption', 'ComplexRedemption', 'MediumRedemption']:
        sample = next((log for log in decoded_logs if log.get('event') == event_type), None)
        if sample:
            print(f"{event_type}:")
            for key, value in sample.items():
                if key not in ['_meta', '_eventSignature']:
                    print(f"  {key}: {value}")
            print(f"  Transaction: {sample['_meta']['transactionHash']}")
            print()

if __name__ == "__main__":
    main()
