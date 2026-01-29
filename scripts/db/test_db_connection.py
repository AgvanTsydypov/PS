"""
Test database connections (Supabase and local PostgreSQL)
Quick script to verify database setup

ИСПОЛЬЗОВАНИЕ:
==============
python test_db_connection.py

ЧТО ПРОВЕРЯЕТ:
==============
✓ Подключение к Supabase
✓ Подключение к локальной PostgreSQL
✓ Тестовая загрузка данных в PostgreSQL
✓ Вывод статистики по обеим БД

ТРЕБОВАНИЯ:
===========
- Файл .env с настройками обеих БД
- pip install supabase psycopg2-binary python-dotenv
- Для PostgreSQL: база 'polymarket' и таблица 'redemptions' созданы
"""

import sys
import io

# Fix encoding for Windows console
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from supabase_uploader import SupabaseUploader


def test_supabase():
    """Test Supabase connection"""
    print("=" * 70)
    print("🔵 TESTING SUPABASE CONNECTION")
    print("=" * 70)
    
    try:
        uploader = SupabaseUploader(use_local_db=False)
        print("✅ Successfully connected to Supabase!")
        print(f"   Stats: {uploader.stats}")
        return True
    except Exception as e:
        print(f"❌ Failed to connect to Supabase")
        print(f"   Error: {str(e)}")
        return False


def test_local_postgres():
    """Test local PostgreSQL connection"""
    print("\n" + "=" * 70)
    print("🟢 TESTING LOCAL POSTGRESQL CONNECTION")
    print("=" * 70)
    
    try:
        uploader = SupabaseUploader(use_local_db=True)
        print("✅ Successfully connected to local PostgreSQL!")
        
        # Test upload with sample data
        print("\n🧪 Testing upload with sample data...")
        sample_data = [{
            'transaction_hash': '0xtest_' + str(int(__import__('time').time())),
            'condition_id': '0xcondition_test',
            'event_id': 'event_test',
            'market_id': 'market_test',
            'market_question': 'Test Market Question?',
            'event_title': 'Test Event',
            'redeemer_address': '0xredeemer_test',
            'payout_usdc': 50.25,
            'timestamp_unix': 1704985200,
            'timestamp_human': '2024-01-11 10:00:00'
        }]
        
        success = uploader.upload_redemptions_batch(sample_data)
        if success:
            print("✅ Test upload successful!")
            print(f"   Uploaded: {uploader.stats['redemptions_inserted']} records")
        else:
            print("❌ Test upload failed!")
            
        return True
        
    except Exception as e:
        print(f"❌ Failed to connect to local PostgreSQL")
        print(f"   Error: {str(e)}")
        print("\n💡 Make sure:")
        print("   1. PostgreSQL is installed and running")
        print("   2. Database 'polymarket' exists")
        print("   3. Table 'redemptions' is created")
        print("   4. .env file has correct LOCAL_DB_* settings")
        return False


def main():
    print("\n🧪 DATABASE CONNECTION TEST\n")
    
    # Test both databases
    supabase_ok = test_supabase()
    postgres_ok = test_local_postgres()
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    print(f"Supabase:         {'✅ OK' if supabase_ok else '❌ FAILED'}")
    print(f"Local PostgreSQL: {'✅ OK' if postgres_ok else '❌ FAILED'}")
    print("=" * 70)
    
    if postgres_ok:
        print("\n🎯 You can now run:")
        print("   python fetch_redemptions.py --upload --local  # Use local PostgreSQL")
        print("   python fetch_redemptions.py --upload          # Use Supabase")
    
    print()


if __name__ == '__main__':
    main()
