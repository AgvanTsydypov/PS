"""
Goldsky Subgraph Load Testing Tool
==================================
Тестирует ваш subgraph на нагрузку и определяет rate limits

ИСПОЛЬЗОВАНИЕ:
-------------
1. Легкая нагрузка (безопасно):
   python test_subgraph_load.py --mode light

2. Средняя нагрузка (найти лимиты):
   python test_subgraph_load.py --mode medium

3. Тяжелая нагрузка (агрессивно):
   python test_subgraph_load.py --mode heavy

4. Кастомная нагрузка:
   python test_subgraph_load.py --requests 100 --concurrent 10 --delay 0.1

5. Пошаговое увеличение (найти точный лимит):
   python test_subgraph_load.py --mode ramp

ПАРАМЕТРЫ:
----------
--mode          Режим: light, medium, heavy, ramp
--requests      Количество запросов (default: 100)
--concurrent    Параллельных запросов (default: 5)
--delay         Задержка между запросами в секундах (default: 0.1)
--endpoint      Кастомный эндпоинт (опционально)
"""

import asyncio
import aiohttp
import time
import sys
from typing import List, Dict, Optional
from datetime import datetime
import statistics

# ==========================================
# CONFIGURATION
# ==========================================

# Ваш Goldsky subgraph endpoint
# DEFAULT_ENDPOINT = "https://api.goldsky.com/api/public/project_cml139dsnqdb101w4e60p78mt/subgraphs/polystars-redemptions-only/0.1/gn"
DEFAULT_ENDPOINT = "https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/activity-subgraph/0.0.4/gn"

# Тестовый GraphQL запрос (легкий)
LIGHT_QUERY = """
{
  redemptions(first: 1) {
    id
  }
}
"""

# Средний запрос
MEDIUM_QUERY = """
{
  redemptions(first: 10, orderBy: timestamp, orderDirection: desc) {
    id
    redeemer
    payout
    timestamp
  }
}
"""

# Тяжелый запрос
HEAVY_QUERY = """
{
  redemptions(first: 100) {
    id
    redeemer
    payout
    timestamp
    condition
  }
}
"""

# Предустановленные режимы
MODES = {
    'light': {
        'requests': 50,
        'concurrent': 3,
        'delay': 0.5,
        'query': LIGHT_QUERY,
        'description': 'Легкая нагрузка (безопасно для любого API)'
    },
    'medium': {
        'requests': 100,
        'concurrent': 5,
        'delay': 0.2,
        'query': MEDIUM_QUERY,
        'description': 'Средняя нагрузка (поиск комфортных лимитов)'
    },
    'heavy': {
        'requests': 200,
        'concurrent': 10,
        'delay': 0.05,
        'query': HEAVY_QUERY,
        'description': 'Тяжелая нагрузка (агрессивный тест лимитов)'
    },
    'ramp': {
        'requests': 300,
        'concurrent': 1,
        'delay': 0,
        'query': MEDIUM_QUERY,
        'description': 'Постепенное увеличение нагрузки'
    }
}

# ==========================================
# STATISTICS TRACKER
# ==========================================
class LoadTestStats:
    def __init__(self):
        self.total_requests = 0
        self.successful = 0
        self.failed = 0
        self.timeouts = 0
        self.rate_limited = 0  # HTTP 429
        self.latencies: List[float] = []
        self.errors: Dict[str, int] = {}
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
    
    def add_success(self, latency: float):
        self.successful += 1
        self.latencies.append(latency)
    
    def add_failure(self, error_type: str):
        self.failed += 1
        self.errors[error_type] = self.errors.get(error_type, 0) + 1
        
        if '429' in error_type or 'rate' in error_type.lower():
            self.rate_limited += 1
        elif 'timeout' in error_type.lower():
            self.timeouts += 1
    
    def get_summary(self) -> Dict:
        duration = (self.end_time or time.time()) - (self.start_time or time.time())
        
        summary = {
            'total': self.total_requests,
            'successful': self.successful,
            'failed': self.failed,
            'rate_limited': self.rate_limited,
            'timeouts': self.timeouts,
            'duration': duration,
            'requests_per_sec': self.total_requests / duration if duration > 0 else 0,
            'success_rate': (self.successful / self.total_requests * 100) if self.total_requests > 0 else 0
        }
        
        if self.latencies:
            summary.update({
                'avg_latency': statistics.mean(self.latencies),
                'min_latency': min(self.latencies),
                'max_latency': max(self.latencies),
                'median_latency': statistics.median(self.latencies),
                'p95_latency': self._percentile(self.latencies, 95),
                'p99_latency': self._percentile(self.latencies, 99),
            })
        
        return summary
    
    @staticmethod
    def _percentile(data: List[float], percentile: int) -> float:
        sorted_data = sorted(data)
        index = int(len(sorted_data) * percentile / 100)
        return sorted_data[min(index, len(sorted_data) - 1)]

# ==========================================
# LOAD TESTING
# ==========================================

async def make_request(
    session: aiohttp.ClientSession,
    endpoint: str,
    query: str,
    stats: LoadTestStats,
    request_id: int
) -> Dict:
    """Make a single GraphQL request and track stats"""
    start_time = time.time()
    
    try:
        async with session.post(
            endpoint,
            json={'query': query},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            latency = time.time() - start_time
            
            if response.status == 200:
                data = await response.json()
                
                # Check for GraphQL errors
                if data.get('errors'):
                    error_msg = str(data['errors'][0].get('message', 'Unknown GraphQL error'))
                    stats.add_failure(f"GraphQL Error: {error_msg[:50]}")
                    return {
                        'id': request_id,
                        'status': 'error',
                        'latency': latency,
                        'error': error_msg
                    }
                
                stats.add_success(latency)
                return {
                    'id': request_id,
                    'status': 'success',
                    'latency': latency
                }
            
            elif response.status == 429:
                stats.add_failure('HTTP 429 Rate Limited')
                return {
                    'id': request_id,
                    'status': 'rate_limited',
                    'latency': latency
                }
            
            else:
                stats.add_failure(f'HTTP {response.status}')
                return {
                    'id': request_id,
                    'status': 'error',
                    'latency': latency,
                    'error': f'HTTP {response.status}'
                }
    
    except asyncio.TimeoutError:
        latency = time.time() - start_time
        stats.add_failure('Timeout')
        return {
            'id': request_id,
            'status': 'timeout',
            'latency': latency
        }
    
    except Exception as e:
        latency = time.time() - start_time
        error_type = type(e).__name__
        stats.add_failure(error_type)
        return {
            'id': request_id,
            'status': 'error',
            'latency': latency,
            'error': f'{error_type}: {str(e)[:50]}'
        }


async def run_load_test(
    endpoint: str,
    query: str,
    total_requests: int,
    concurrent: int,
    delay: float,
    mode_name: str = "custom"
) -> LoadTestStats:
    """Run load test with specified parameters"""
    
    stats = LoadTestStats()
    stats.total_requests = total_requests
    stats.start_time = time.time()
    
    print("=" * 70)
    print(f"🚀 LOAD TEST: {mode_name.upper()}")
    print("=" * 70)
    print(f"Endpoint:     {endpoint[:60]}...")
    print(f"Total:        {total_requests} requests")
    print(f"Concurrent:   {concurrent} parallel")
    print(f"Delay:        {delay}s between requests")
    print(f"Started:      {datetime.now().strftime('%H:%M:%S')}")
    print("-" * 70)
    
    async with aiohttp.ClientSession() as session:
        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(concurrent)
        
        async def bounded_request(request_id: int):
            async with semaphore:
                if delay > 0:
                    await asyncio.sleep(delay)
                return await make_request(session, endpoint, query, stats, request_id)
        
        # Create all tasks
        tasks = [bounded_request(i) for i in range(total_requests)]
        
        # Execute with progress
        completed = 0
        for coro in asyncio.as_completed(tasks):
            result = await coro
            completed += 1
            
            # Show progress every 10 requests
            if completed % 10 == 0 or completed == total_requests:
                progress = completed / total_requests * 100
                print(f"Progress: {completed}/{total_requests} ({progress:.1f}%) - "
                      f"✅ {stats.successful} | ❌ {stats.failed} | ⏱️  {stats.rate_limited} rate limited", 
                      end='\r', flush=True)
        
        print()  # New line after progress
    
    stats.end_time = time.time()
    return stats


async def run_ramp_test(endpoint: str, query: str) -> LoadTestStats:
    """
    Постепенно увеличивает нагрузку чтобы найти точный лимит
    """
    print("=" * 70)
    print("📈 RAMP TEST - Постепенное увеличение нагрузки")
    print("=" * 70)
    print("Будем увеличивать RPS пока не получим rate limit...")
    print()
    
    stats = LoadTestStats()
    stats.start_time = time.time()
    
    # Начинаем с 5 RPS, увеличиваем до 50 RPS
    rps_levels = [5, 10, 15, 20, 25, 30, 40, 50]
    
    for rps in rps_levels:
        print(f"\n🎯 Testing {rps} requests/second (10 seconds)...")
        delay = 1.0 / rps
        requests_count = rps * 10  # 10 секунд на каждый уровень
        
        level_stats = LoadTestStats()
        level_stats.start_time = time.time()
        
        async with aiohttp.ClientSession() as session:
            tasks = []
            for i in range(requests_count):
                await asyncio.sleep(delay)
                task = asyncio.create_task(
                    make_request(session, endpoint, query, level_stats, i)
                )
                tasks.append(task)
                
                # Show live stats
                if (i + 1) % rps == 0:
                    elapsed = time.time() - level_stats.start_time
                    current_rps = (i + 1) / elapsed
                    print(f"   {i + 1}/{requests_count} - "
                          f"RPS: {current_rps:.1f} - "
                          f"✅ {level_stats.successful} | ❌ {level_stats.failed}", 
                          end='\r', flush=True)
            
            # Wait for remaining
            await asyncio.gather(*tasks)
        
        level_stats.end_time = time.time()
        
        # Merge stats
        stats.total_requests += level_stats.total_requests
        stats.successful += level_stats.successful
        stats.failed += level_stats.failed
        stats.rate_limited += level_stats.rate_limited
        stats.timeouts += level_stats.timeouts
        stats.latencies.extend(level_stats.latencies)
        
        # Check if we hit rate limit
        rate_limit_pct = (level_stats.rate_limited / requests_count * 100) if requests_count > 0 else 0
        
        print(f"\n   Result: ✅ {level_stats.successful} | ❌ {level_stats.failed} | "
              f"⏱️  {level_stats.rate_limited} ({rate_limit_pct:.1f}% rate limited)")
        
        if rate_limit_pct > 10:
            print(f"\n⚠️  Rate limit detected at {rps} RPS!")
            print(f"   Safe limit: ~{rps - 5} requests/second")
            break
        
        if rate_limit_pct > 0:
            print(f"   ⚠️  Starting to see rate limits...")
    
    stats.end_time = time.time()
    return stats


# ==========================================
# RESULTS DISPLAY
# ==========================================

def print_results(stats: LoadTestStats, mode_name: str):
    """Print detailed test results"""
    summary = stats.get_summary()
    
    print("\n" + "=" * 70)
    print("📊 TEST RESULTS")
    print("=" * 70)
    
    # Basic stats
    print(f"\n📈 Request Statistics:")
    print(f"   Total requests:      {summary['total']}")
    print(f"   Successful:          {summary['successful']} ({summary['success_rate']:.1f}%)")
    print(f"   Failed:              {summary['failed']}")
    if stats.rate_limited > 0:
        print(f"   Rate limited (429):  {stats.rate_limited} ⚠️")
    if stats.timeouts > 0:
        print(f"   Timeouts:            {stats.timeouts}")
    
    # Performance
    print(f"\n⚡ Performance:")
    print(f"   Duration:            {summary['duration']:.2f}s")
    print(f"   Requests/second:     {summary['requests_per_sec']:.2f}")
    
    if 'avg_latency' in summary:
        print(f"\n⏱️  Latency:")
        print(f"   Average:             {summary['avg_latency']*1000:.0f}ms")
        print(f"   Median:              {summary['median_latency']*1000:.0f}ms")
        print(f"   Min:                 {summary['min_latency']*1000:.0f}ms")
        print(f"   Max:                 {summary['max_latency']*1000:.0f}ms")
        print(f"   95th percentile:     {summary['p95_latency']*1000:.0f}ms")
        print(f"   99th percentile:     {summary['p99_latency']*1000:.0f}ms")
    
    # Errors breakdown
    if stats.errors:
        print(f"\n❌ Errors Breakdown:")
        for error_type, count in sorted(stats.errors.items(), key=lambda x: x[1], reverse=True):
            print(f"   {error_type}: {count}")
    
    # Recommendations
    print(f"\n💡 Recommendations:")
    
    if summary['success_rate'] >= 99:
        print(f"   ✅ Excellent! {summary['requests_per_sec']:.1f} RPS безопасно")
        print(f"   ✅ Можно немного увеличить нагрузку")
    elif summary['success_rate'] >= 95:
        print(f"   ✅ Хорошо! {summary['requests_per_sec']:.1f} RPS работает надёжно")
        print(f"   ⚠️  Это близко к лимиту, будьте осторожны")
    elif summary['success_rate'] >= 90:
        print(f"   ⚠️  {summary['requests_per_sec']:.1f} RPS - много ошибок")
        print(f"   💡 Уменьшите нагрузку на 20-30%")
    else:
        print(f"   ❌ {summary['requests_per_sec']:.1f} RPS - слишком много!")
        print(f"   💡 Уменьшите нагрузку в 2 раза")
    
    if stats.rate_limited > 0:
        safe_rps = summary['requests_per_sec'] * 0.7
        print(f"   ⏱️  Обнаружен rate limiting!")
        print(f"   💡 Безопасная скорость: ~{safe_rps:.1f} RPS")
    
    print("\n" + "=" * 70)


# ==========================================
# CLI
# ==========================================

def parse_args():
    """Parse command line arguments"""
    args = {
        'mode': None,
        'requests': None,
        'concurrent': None,
        'delay': None,
        'endpoint': DEFAULT_ENDPOINT
    }
    
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        
        if arg in ['--mode', '-m']:
            if i + 1 < len(sys.argv):
                args['mode'] = sys.argv[i + 1]
                i += 2
            else:
                print("Error: --mode requires a value")
                sys.exit(1)
        
        elif arg in ['--requests', '-r']:
            if i + 1 < len(sys.argv):
                args['requests'] = int(sys.argv[i + 1])
                i += 2
            else:
                print("Error: --requests requires a value")
                sys.exit(1)
        
        elif arg in ['--concurrent', '-c']:
            if i + 1 < len(sys.argv):
                args['concurrent'] = int(sys.argv[i + 1])
                i += 2
            else:
                print("Error: --concurrent requires a value")
                sys.exit(1)
        
        elif arg in ['--delay', '-d']:
            if i + 1 < len(sys.argv):
                args['delay'] = float(sys.argv[i + 1])
                i += 2
            else:
                print("Error: --delay requires a value")
                sys.exit(1)
        
        elif arg in ['--endpoint', '-e']:
            if i + 1 < len(sys.argv):
                args['endpoint'] = sys.argv[i + 1]
                i += 2
            else:
                print("Error: --endpoint requires a value")
                sys.exit(1)
        
        elif arg in ['--help', '-h']:
            print(__doc__)
            sys.exit(0)
        
        else:
            print(f"Unknown argument: {arg}")
            print("Use --help for usage information")
            sys.exit(1)
    
    return args


async def main():
    """Main entry point"""
    args = parse_args()
    
    # Show header
    print("\n" + "🔥" * 35)
    print("  GOLDSKY SUBGRAPH LOAD TESTING TOOL")
    print("🔥" * 35 + "\n")
    
    # Determine mode
    if args['mode'] and args['mode'] in MODES:
        mode = MODES[args['mode']]
        print(f"Mode: {args['mode'].upper()}")
        print(f"Description: {mode['description']}\n")
        
        if args['mode'] == 'ramp':
            stats = await run_ramp_test(args['endpoint'], mode['query'])
            print_results(stats, args['mode'])
        else:
            stats = await run_load_test(
                endpoint=args['endpoint'],
                query=mode['query'],
                total_requests=mode['requests'],
                concurrent=mode['concurrent'],
                delay=mode['delay'],
                mode_name=args['mode']
            )
            print_results(stats, args['mode'])
    
    elif args['requests'] and args['concurrent'] and args['delay'] is not None:
        # Custom mode
        print(f"Mode: CUSTOM\n")
        stats = await run_load_test(
            endpoint=args['endpoint'],
            query=MEDIUM_QUERY,
            total_requests=args['requests'],
            concurrent=args['concurrent'],
            delay=args['delay'],
            mode_name='custom'
        )
        print_results(stats, 'custom')
    
    else:
        print("❌ Error: Must specify either --mode or all of (--requests, --concurrent, --delay)")
        print("\nQuick start:")
        print("  python test_subgraph_load.py --mode light")
        print("\nAvailable modes:", ", ".join(MODES.keys()))
        print("\nUse --help for full documentation")
        sys.exit(1)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
