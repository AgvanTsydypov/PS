import requests
import json

# --- CONFIG ---
ALCHEMY_API_KEY = "cYLQXHLnoVjs9q4eaG0jM"
ALCHEMY_URL = f"https://polygon-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

def send_rpc_request(method, params):
    """Отправляет JSON-RPC запрос к Alchemy"""
    
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1
    }
    
    headers = {
        "Content-Type": "application/json"
    }
    
    print(f"📤 Отправляю запрос: {method}")
    print(f"📦 Параметры: {json.dumps(params, indent=2)}\n")
    
    response = requests.post(ALCHEMY_URL, headers=headers, json=payload)
    
    if response.status_code == 200:
        result = response.json()
        return result
    else:
        print(f"❌ Ошибка: {response.status_code}")
        print(f"Response: {response.text}")
        return None

def get_logs_example():
    """Пример: получить логи для конкретных блоков"""
    
    # Блоки в HEX формате
    from_block = "0x4d8c073"  # 81312371 в HEX
    to_block = "0x4d8c074"    # 81312372 в HEX
    
    # Или можно использовать decimal:
    # from_block = hex(81312371)  # Конвертация из decimal в HEX
    
    params = [{
        "fromBlock": from_block,
        "toBlock": to_block,
        "address": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",  # CTF Exchange
        "topics": []  # Пустой массив = все события
    }]
    
    result = send_rpc_request("eth_getLogs", params)
    
    if result and "result" in result:
        logs = result["result"]
        print(f"✅ Получено {len(logs)} логов\n")
        
        # Красивый вывод первых нескольких логов
        for i, log in enumerate(logs[:3]):  # Показываем первые 3
            print(f"📋 Лог #{i+1}:")
            print(f"   Блок: {int(log['blockNumber'], 16)}")  # Конвертируем из HEX в decimal
            print(f"   TX Hash: {log['transactionHash']}")
            print(f"   Topics: {len(log['topics'])} шт.")
            print(f"   Data: {log['data'][:66]}...\n")  # Первые 32 байта
        
        if len(logs) > 3:
            print(f"... и ещё {len(logs) - 3} логов")
        
        # Сохраняем полный результат в JSON
        with open("output/rpc_logs_result.json", "w") as f:
            json.dump(result, f, indent=2)
        print(f"\n💾 Полный результат сохранён в output/rpc_logs_result.json")
        
        return logs
    else:
        print("❌ Не удалось получить логи")
        print(f"Response: {json.dumps(result, indent=2)}")
        return None

def get_block_example():
    """Пример: получить информацию о блоке"""
    
    params = [
        "0x4d8c073",  # Номер блока в HEX
        False         # False = только хеши TX, True = полные данные TX
    ]
    
    result = send_rpc_request("eth_getBlockByNumber", params)
    
    if result and "result" in result:
        block = result["result"]
        print(f"✅ Блок #{int(block['number'], 16)}")
        print(f"   Timestamp: {int(block['timestamp'], 16)}")
        print(f"   Транзакций: {len(block['transactions'])}")
        print(f"   Gas Used: {int(block['gasUsed'], 16)}")
        return block
    else:
        print("❌ Не удалось получить блок")
        return None

def get_latest_block():
    """Получить последний блок"""
    
    params = ["latest", False]
    result = send_rpc_request("eth_getBlockByNumber", params)
    
    if result and "result" in result:
        block = result["result"]
        block_num = int(block['number'], 16)
        print(f"✅ Последний блок: #{block_num:,}")
        print(f"   HEX: {block['number']}")
        return block_num
    return None

def hex_to_dec(hex_str):
    """Конвертер HEX → Decimal"""
    return int(hex_str, 16)

def dec_to_hex(dec_num):
    """Конвертер Decimal → HEX (для блоков)"""
    return hex(dec_num)

if __name__ == "__main__":
    print("=" * 80)
    print("🔧 JSON-RPC Direct Request Tool")
    print("=" * 80)
    print()
    
    # 1. Получаем последний блок
    print("1️⃣ Получаю последний блок...\n")
    latest = get_latest_block()
    print("\n" + "-" * 80 + "\n")
    
    # 2. Получаем логи для конкретных блоков
    print("2️⃣ Получаю логи для блоков 81312371-81312372...\n")
    logs = get_logs_example()
    print("\n" + "-" * 80 + "\n")
    
    # 3. Дополнительные утилиты
    print("3️⃣ Конвертер HEX ↔ Decimal:\n")
    print(f"   81312371 (dec) = {dec_to_hex(81312371)} (hex)")
    print(f"   0x4d8c073 (hex) = {hex_to_dec('0x4d8c073'):,} (dec)")
    
    print("\n" + "=" * 80)
    print("✅ Готово!")
    print("=" * 80)
