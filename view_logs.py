"""
View and analyze redemption fetch logs
Quick script to view the latest log file

ИСПОЛЬЗОВАНИЕ:
==============
1. Последние 50 строк (по умолчанию):
   python view_logs.py

2. Последние N строк:
   python view_logs.py 100      # последние 100 строк
   python view_logs.py 500      # последние 500 строк

3. Весь лог:
   python view_logs.py all

4. Только ошибки:
   python view_logs.py errors

5. Поиск текста:
   python view_logs.py search "timeout"
   python view_logs.py search "Market 42"

6. Статистика:
   python view_logs.py analyze

РАСПОЛОЖЕНИЕ ЛОГОВ:
===================
logs/redemptions_fetch_YYYYMMDD_HHMMSS.log
"""

import os
import glob
from datetime import datetime

def find_latest_log():
    """Find the most recent log file"""
    log_dir = 'logs'
    if not os.path.exists(log_dir):
        print(f"❌ Log directory '{log_dir}' not found")
        return None
    
    pattern = os.path.join(log_dir, 'redemptions_fetch_*.log')
    files = glob.glob(pattern)
    
    if not files:
        print(f"❌ No log files found in {log_dir}/")
        return None
    
    # Get most recent file
    latest = max(files, key=os.path.getmtime)
    return latest

def view_log(filepath, lines=50, search=None, errors_only=False):
    """View log file with optional filtering"""
    if not filepath or not os.path.exists(filepath):
        print(f"❌ File not found: {filepath}")
        return
    
    print(f"📄 Log file: {filepath}")
    
    # Get file stats
    size_mb = os.path.getsize(filepath) / (1024 * 1024)
    mod_time = datetime.fromtimestamp(os.path.getmtime(filepath))
    print(f"   Size: {size_mb:.2f} MB")
    print(f"   Modified: {mod_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
        
        # Filter if needed
        if errors_only:
            filtered = [line for line in all_lines if any(marker in line for marker in ['❌', '⚠️', 'ERROR', 'Failed', 'timeout'])]
            lines_to_show = filtered
            print(f"🔍 Showing {len(filtered)} error/warning lines (out of {len(all_lines)} total)")
        elif search:
            filtered = [line for line in all_lines if search.lower() in line.lower()]
            lines_to_show = filtered
            print(f"🔍 Showing {len(filtered)} lines matching '{search}'")
        else:
            lines_to_show = all_lines[-lines:] if lines else all_lines
            if lines and len(all_lines) > lines:
                print(f"📋 Showing last {lines} lines (out of {len(all_lines)} total)")
            else:
                print(f"📋 Showing all {len(all_lines)} lines")
        
        print("=" * 70)
        for line in lines_to_show:
            print(line.rstrip())
        print("=" * 70)
        
    except Exception as e:
        print(f"❌ Error reading file: {e}")

def analyze_log(filepath):
    """Analyze log file for statistics"""
    if not filepath or not os.path.exists(filepath):
        print(f"❌ File not found: {filepath}")
        return
    
    print(f"📊 Analyzing: {filepath}\n")
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Count different types of messages
        stats = {
            'total_lines': len(lines),
            'errors': len([l for l in lines if '❌' in l or 'ERROR' in l]),
            'warnings': len([l for l in lines if '⚠️' in l or 'WARNING' in l]),
            'successes': len([l for l in lines if '✅' in l]),
            'timeouts': len([l for l in lines if 'timeout' in l.lower()]),
            'retries': len([l for l in lines if 'retry' in l.lower()]),
            'markets_processed': len([l for l in lines if 'Found' in l and 'redemptions' in l]),
            'uploads': len([l for l in lines if 'Uploading to Supabase' in l or 'Uploading' in l and 'redemptions' in l]),
        }
        
        print("=" * 70)
        print("STATISTICS")
        print("=" * 70)
        print(f"Total lines:          {stats['total_lines']:,}")
        print(f"Markets processed:    {stats['markets_processed']:,}")
        print(f"Upload attempts:      {stats['uploads']:,}")
        print(f"Successes (✅):       {stats['successes']:,}")
        print(f"Errors (❌):          {stats['errors']:,}")
        print(f"Warnings (⚠️):        {stats['warnings']:,}")
        print(f"Timeouts:             {stats['timeouts']:,}")
        print(f"Retries:              {stats['retries']:,}")
        print("=" * 70)
        
        # Show recent errors
        error_lines = [l.strip() for l in lines if '❌' in l or 'ERROR' in l.upper()]
        if error_lines:
            print(f"\n🚨 Recent errors (last 5):")
            for line in error_lines[-5:]:
                print(f"   • {line[:100]}")
        
    except Exception as e:
        print(f"❌ Error analyzing file: {e}")

if __name__ == '__main__':
    import sys
    
    # Find latest log
    latest_log = find_latest_log()
    
    if not latest_log:
        print("No logs found. Run fetch_redemptions.py first.")
        sys.exit(1)
    
    # Parse command line arguments
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == 'analyze' or command == 'stats':
            analyze_log(latest_log)
        elif command == 'errors':
            view_log(latest_log, lines=None, errors_only=True)
        elif command == 'search' and len(sys.argv) > 2:
            search_term = sys.argv[2]
            view_log(latest_log, lines=None, search=search_term)
        elif command == 'all':
            view_log(latest_log, lines=None)
        elif command.isdigit():
            view_log(latest_log, lines=int(command))
        else:
            print("Usage:")
            print("  python view_logs.py              # Show last 50 lines")
            print("  python view_logs.py 100          # Show last 100 lines")
            print("  python view_logs.py all          # Show entire log")
            print("  python view_logs.py errors       # Show only errors/warnings")
            print("  python view_logs.py analyze      # Show statistics")
            print("  python view_logs.py search TEXT  # Search for TEXT")
    else:
        # Default: show last 50 lines
        view_log(latest_log, lines=50)
