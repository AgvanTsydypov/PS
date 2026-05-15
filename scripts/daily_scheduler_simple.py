"""
Simplified Daily Data Scheduler

Orchestrates daily data loading without seasons.

LOGIC:
======
- Genesis: Load once (2024-07-06 to 2026-01-05) with 100M filter
- Daily: Load every day with 5M filter
  - Events: EVENTS_LAG_DAYS day ago (default: 1 = yesterday)
  - Redemptions/Positions/Leaderboard: DATA_LAG_DAYS days ago (default: 3)

USAGE:
======
Check system state:
    python scripts/daily_scheduler_simple.py --check

Run daily pipeline (AUTO-CHECKS for missing data):
    python scripts/daily_scheduler_simple.py --run
    
    Note: --run automatically checks for missing dates and runs catch-up first
    if needed. This ensures no data gaps before loading today's data.

Load Genesis:
    python scripts/daily_scheduler_simple.py --historical
    
Genesis + Auto catch-up (RECOMMENDED for initial setup):
    python scripts/daily_scheduler_simple.py --historical --auto-catchup

Catch-up missing data (MANUAL):
    python scripts/daily_scheduler_simple.py --catch-up
    
    Note: If catch-up takes >24h and new day passes, it will automatically
    detect the gap and run another iteration (max 10 iterations).

Docker:
    docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --run
"""

import os
import sys
import math
import subprocess
import time
import argparse
import tempfile
import json
import requests
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
import psycopg2.errors
import psycopg2.extras

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.data_loading_manager import (
    DataLoadingManager,
    GENESIS_START_DATE,
    GENESIS_END_DATE,
    DATA_LAG_DAYS,
    EVENTS_LAG_DAYS,
    RESOLUTION_READY_OFFSET_DAYS,
)
from scripts.fetch.fetch_events_config import GENESIS_INCLUDE_EVENT_IDS
from scripts.ai import Agent1QuantCardGenerator, Agent2ColoristGenerator

STANDARD_SEASON_TOTAL_SUPPLY_TEST = 333
STANDARD_SEASON_ACTIVE_DAYS = 9
STANDARD_SEASON_CYCLE_DAYS = 10
ORIGIN_LOOKBACK_DAYS_STANDARD = 10
FIRST_STANDARD_BOOTSTRAP_DELAY_DAYS = DATA_LAG_DAYS
STANDARD_SNAPSHOT_PRIMARY_TAG_CAP = 5
STANDARD_SNAPSHOT_EVENT_LIMIT = 20
GENESIS_SEASON_TOTAL_SUPPLY_TEST = 2000
GENESIS_SEASON_FAR_FUTURE_END = datetime(9999, 12, 31, tzinfo=timezone.utc)
GAMMA_API_BASE_URL = "https://gamma-api.polymarket.com"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        return default
    return int(value)


def _snapshot_max_single_event_share() -> float:
    """Upper bound on one event's share of winner snapshot rows (default 0.5)."""
    raw = os.getenv("POLYSTARS_SNAPSHOT_MAX_SINGLE_EVENT_SHARE")
    if raw is None or not str(raw).strip():
        return 0.5
    try:
        v = float(str(raw).strip())
    except ValueError:
        return 0.5
    if v <= 0 or v > 1:
        return 0.5
    return v


def _snapshot_candidate_pool_multiplier() -> int:
    """Multiplier for per-event candidate prefetch cap (default 8)."""
    raw = os.getenv("POLYSTARS_SNAPSHOT_CANDIDATE_POOL_MULTIPLIER")
    if raw is None or not str(raw).strip():
        return 8
    try:
        value = int(str(raw).strip())
    except ValueError:
        return 8
    return max(2, min(value, 50))


def _snapshot_candidate_fetch_batch_size() -> int:
    """Server-side cursor batch size for candidate participant streaming."""
    raw = os.getenv("POLYSTARS_SNAPSHOT_CANDIDATE_FETCH_BATCH_SIZE")
    if raw is None or not str(raw).strip():
        return 2000
    try:
        value = int(str(raw).strip())
    except ValueError:
        return 2000
    return max(100, min(value, 20000))


# Standard snapshot window defaults to previous 10 days:
# [start - 10d, start) with defaults offset=0/lookback=10.
STANDARD_ORIGIN_SNAPSHOT_OFFSET_DAYS = _env_int(
    "POLYSTARS_ORIGIN_SNAPSHOT_OFFSET_DAYS_STANDARD",
    0,
)

GENESIS_START_OFFSET_FROM_END_DAYS = 10


# ==========================================
# MINT QUEUE WORKER CONSTANTS
# ==========================================
# Stable bigint key for pg_advisory_lock — guarantees only one mint queue
# worker is processing claims at a time (across containers/hosts), even if
# the hourly cron tick fires while a long batch is still running. Holding
# the lock is on a dedicated connection for the duration of the run.
MINT_QUEUE_ADVISORY_LOCK_KEY = 727461

# A claim row stuck in PROCESSING for longer than this is considered to
# have come from a crashed prior run. The recovery pass at the start of
# each worker invocation either requeues it (if no tx_hash → never made
# it on-chain) or auto-finalizes it to COMPLETED (if tx_hash is set →
# already minted, just never flipped). Default 30 min covers worst-case
# Pinata + EVM tx + cardgen for a single claim with plenty of slack.
MINT_QUEUE_STALE_PROCESSING_MINUTES = _env_int(
    "MINT_QUEUE_STALE_PROCESSING_MINUTES",
    30,
)

# RBF (Replace-By-Fee) tuning. Read at the start of each worker invocation.
#
# A PROCESSING row whose latest tx has been pending in the mempool longer
# than this is considered "stuck" and the worker bumps it via RBF. The
# threshold trades user-visible latency (lower → faster recovery from base
# fee spikes, but more replacements during normal congestion) against
# wallet costs (higher → fewer wasted bumps when the original would have
# eventually mined). 20 min is the sweet spot for safe-tier sends on
# mainnet — well above the median inclusion time but well below the 6h
# fuse on the dropped-tx requeue path.
MINT_QUEUE_REPLACE_AFTER_MINUTES = _env_int(
    "MINT_QUEUE_REPLACE_AFTER_MINUTES", 20,
)
# Hard cap on RBF attempts per claim. Hitting this leaves the row in
# PROCESSING with ``error_message='[stuck: max RBF attempts reached]'`` so
# the admin UI can surface it without paging anyone (no Telegram alert per
# user request). Operator inspects + chooses a manual remedy.
MINT_QUEUE_MAX_RBF_ATTEMPTS = _env_int(
    "MINT_QUEUE_MAX_RBF_ATTEMPTS", 5,
)
# Per-tx fee ceiling expressed as a multiple of the price-gate threshold.
# Replacement cost (max_fee × gas_limit, converted to USD) must not exceed
# this multiplier × MINT_QUEUE_MAX_USD_PER_MINT. With the default 5×, a
# $0.50 gate allows up to $2.50 of bump headroom per claim — generous
# enough for any realistic congestion spike, hard cap against runaway
# bumping if the gas tracker is broken.
MINT_QUEUE_RBF_FEE_CEILING_MULTIPLIER = float(
    (os.getenv("MINT_QUEUE_RBF_FEE_CEILING_MULTIPLIER") or "").strip() or "5"
)

# Price gate: skip pickup whenever the estimated rapid-tier cost of one mintTo()
# call exceeds this USD threshold. The cron now ticks every 5 minutes, so a
# pause here just means the next tick re-checks Etherscan. Within a single
# invocation the gate is re-evaluated on every iteration — after a successful
# mint the loop immediately re-fetches the tracker and either proceeds with
# the next QUEUED row or breaks out, so a cheap window drains as many claims
# as the batch size allows without waiting for cron.
def _mint_queue_max_usd_per_mint() -> float:
    raw = (os.getenv("MINT_QUEUE_MAX_USD_PER_MINT") or "").strip()
    if not raw:
        return 0.5
    try:
        value = float(raw)
    except ValueError:
        return 0.5
    # Allow 0 explicitly: treat it as "block every mint" (useful for testing
    # the gate and for emergency freezes). Only negative values are invalid.
    return value if value >= 0 else 0.5


# ==========================================
# LOGGING SETUP
# ==========================================
class DualLogger:
    """Logger that writes to both console and file in real-time"""
    def __init__(self, log_file, original_stdout=None):
        self.terminal = sys.stdout
        self.original_stdout = original_stdout or sys.stdout
        self.log_file = open(log_file, 'a', encoding='utf-8')
        # Try to also write to /dev/stdout for Docker logs visibility
        self.docker_stdout = None
        try:
            if os.path.exists('/dev/stdout'):
                self.docker_stdout = open('/dev/stdout', 'w', encoding='utf-8', buffering=1)
        except:
            pass
        
    def write(self, message):
        self.terminal.write(message)
        self.terminal.flush()
        self.log_file.write(message)
        self.log_file.flush()
        if self.docker_stdout:
            try:
                self.docker_stdout.write(message)
                self.docker_stdout.flush()
            except:
                pass
        
    def flush(self):
        self.terminal.flush()
        self.log_file.flush()
        if self.docker_stdout:
            try:
                self.docker_stdout.flush()
            except:
                pass
    
    def close(self):
        self.log_file.close()
        if self.docker_stdout:
            try:
                self.docker_stdout.close()
            except:
                pass

# Global logger instance
_logger = None
_log_file_handle = None
_original_print = print

def _custom_print(*args, **kwargs):
    """Custom print that also writes to log file in Docker mode"""
    global _log_file_handle
    _original_print(*args, **kwargs)
    if _log_file_handle:
        try:
            message = ' '.join(str(arg) for arg in args)
            end = kwargs.get('end', '\n')
            _log_file_handle.write(message + end)
            _log_file_handle.flush()
        except:
            pass

def setup_logging(operation_name: str):
    """Setup dual logging to console and file"""
    global _logger, _log_file_handle
    
    log_dir = 'logs'
    os.makedirs(log_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = os.path.join(log_dir, f'scheduler_{operation_name}_{timestamp}.log')
    
    original_stdout = sys.stdout
    _logger = DualLogger(log_file, original_stdout=original_stdout)
    _log_file_handle = _logger.log_file
    
    in_docker = os.path.exists('/dev/stdout')
    if not in_docker:
        sys.stdout = _logger
    else:
        import builtins
        builtins.print = _custom_print
    
    print(f"📝 Logging to: {log_file}")
    print(f"   All output will be saved to this file in real-time")
    if in_docker:
        print(f"   Docker mode: stdout not intercepted (visible in docker logs)")
    print()
    
    return log_file

def cleanup_logging():
    """Cleanup logging and restore stdout"""
    global _logger, _log_file_handle
    if _logger:
        sys.stdout = _logger.terminal
        _logger.close()
        _logger = None
        _log_file_handle = None
    import builtins
    builtins.print = _original_print


class ProcessLock:
    """Manages lock file to prevent concurrent script execution"""
    
    # Максимальное время жизни lock-файла (30 часов)
    # Защита от зависших процессов (нормальная работа: до 22 часов)
    MAX_LOCK_AGE_HOURS = 30
    
    def __init__(self, lock_name: str = "polystars_scheduler"):
        self.lock_dir = Path(tempfile.gettempdir())
        self.lock_file = self.lock_dir / f"{lock_name}.lock"
        self.is_locked_by_me = False
    
    def _is_process_running(self, pid: int) -> bool:
        """Check if process with given PID is running"""
        try:
            # Проверка на Unix/Linux
            if os.name != 'nt':  # Not Windows
                os.kill(pid, 0)  # Signal 0 - just check, don't kill
                return True
            else:
                # На Windows используем psutil если доступен, иначе считаем что работает
                try:
                    import psutil
                    return psutil.pid_exists(pid)
                except ImportError:
                    # Если psutil нет, проверяем через tasklist
                    import subprocess
                    result = subprocess.run(
                        ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                        capture_output=True, text=True
                    )
                    return str(pid) in result.stdout
        except (OSError, ProcessLookupError):
            return False
        except Exception:
            # В случае любой ошибки считаем что процесс работает (безопаснее)
            return True
    
    def _clean_stale_lock(self) -> bool:
        """Remove lock file if process is dead or lock is too old"""
        if not self.lock_file.exists():
            return True
        
        try:
            with open(self.lock_file, 'r') as f:
                lines = f.read().strip().split('\n')
                if len(lines) < 3:
                    # Неправильный формат - удаляем
                    print(f"⚠️  Invalid lock file format - removing")
                    self.lock_file.unlink()
                    return True
                
                operation = lines[0]
                lock_time_str = lines[1]
                pid_line = lines[2]
                
                # Извлечь PID
                pid = int(pid_line.split(':')[1].strip())
                
                # Проверить возраст lock-файла
                lock_time = datetime.strptime(lock_time_str, '%Y-%m-%d %H:%M:%S')
                age_hours = (datetime.now() - lock_time).total_seconds() / 3600
                
                if age_hours > self.MAX_LOCK_AGE_HOURS:
                    print(f"⚠️  Lock file is too old ({age_hours:.1f}h > {self.MAX_LOCK_AGE_HOURS}h) - removing")
                    print(f"   Operation: {operation}")
                    print(f"   Started: {lock_time_str}")
                    print(f"   Note: Normal operations take up to 22h, this looks like a stuck process")
                    self.lock_file.unlink()
                    return True
                
                # Проверить живость процесса
                if not self._is_process_running(pid):
                    print(f"⚠️  Process {pid} is not running - removing stale lock")
                    print(f"   Operation: {operation}")
                    print(f"   Started: {lock_time_str}")
                    self.lock_file.unlink()
                    return True
                
                # Процесс жив и lock свежий
                return False
                
        except Exception as e:
            print(f"⚠️  Error checking lock file: {e}")
            # В случае ошибки НЕ удаляем lock (безопаснее)
            return False
    
    def acquire(self, operation: str) -> bool:
        """
        Try to acquire lock
        
        Args:
            operation: Name of operation trying to acquire lock (e.g., 'catch-up', 'historical')
            
        Returns:
            True if lock acquired, False if already locked
        """
        # Сначала проверить и почистить устаревший lock
        if not self._clean_stale_lock():
            # Lock существует и валиден
            try:
                with open(self.lock_file, 'r') as f:
                    lock_info = f.read().strip().split('\n')
                    if len(lock_info) >= 2:
                        locked_operation = lock_info[0]
                        locked_time = lock_info[1]
                        print(f"\n❌ Cannot start: Another operation is already running!")
                        print(f"   Operation: {locked_operation}")
                        print(f"   Started at: {locked_time}")
                        print(f"   Lock file: {self.lock_file}")
                        return False
            except Exception as e:
                print(f"⚠️  Warning: Could not read lock file: {e}")
                return False
        
        # Создать новый lock file
        try:
            with open(self.lock_file, 'w') as f:
                f.write(f"{operation}\n")
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"PID: {os.getpid()}\n")
            self.is_locked_by_me = True
            print(f"🔒 Lock acquired for '{operation}' operation")
            print(f"   Lock file: {self.lock_file}")
            return True
        except Exception as e:
            print(f"⚠️  Warning: Could not create lock file: {e}")
            return False
    
    def release(self):
        """Release lock by removing lock file"""
        if self.is_locked_by_me and self.lock_file.exists():
            try:
                self.lock_file.unlink()
                self.is_locked_by_me = False
                print(f"🔓 Lock released")
            except Exception as e:
                print(f"⚠️  Warning: Could not remove lock file: {e}")
    
    def is_locked(self) -> bool:
        """Check if lock exists"""
        return self.lock_file.exists()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class SimplifiedScheduler:
    """Simplified scheduler without seasons"""
    
    def __init__(self, use_local_db: bool = True, dry_run: bool = False):
        self.manager = DataLoadingManager(use_local_db=use_local_db)
        self.use_local_db = use_local_db
        self.dry_run = dry_run
        # Closed-time pipeline is now the only supported mode.
        self.use_closed_time_pipeline = True
        self.pending_poll_limit = int(os.getenv("POLYSTARS_PENDING_POLL_LIMIT", "1000"))
        self.ready_batch_limit = int(os.getenv("POLYSTARS_READY_BATCH_LIMIT", "1000"))
        self.event_cards_post_downstream_enabled = _env_bool(
            "POLYSTARS_EVENT_CARDS_POST_DOWNSTREAM_ENABLED",
            _env_bool("POLYSTARS_EVENT_CARDS_PRE_SNAPSHOT_ENABLED", True),
        )
        self.event_cards_batch_size = int(os.getenv("POLYSTARS_EVENT_CARDS_BATCH_SIZE", "25"))
        self.event_cards_max_per_run = int(os.getenv("POLYSTARS_EVENT_CARDS_MAX_PER_RUN", "200"))
        self.event_cards_model = os.getenv("POLYSTARS_EVENT_CARDS_MODEL", "").strip()
        self.event_cards_prompt_version = os.getenv("POLYSTARS_EVENT_CARDS_PROMPT_VERSION", "v1").strip() or "v1"
        self.event_cards_agent_name = os.getenv("POLYSTARS_EVENT_CARDS_AGENT_NAME", "agent_1_quant").strip() or "agent_1_quant"
        self.tag_colors_model = os.getenv("POLYSTARS_TAG_COLORS_MODEL", "").strip()
        self.tag_colors_prompt_version = os.getenv("POLYSTARS_TAG_COLORS_PROMPT_VERSION", "v1").strip() or "v1"
        self._event_card_generator: Optional[Agent1QuantCardGenerator] = None
        self._tag_color_generator: Optional[Agent2ColoristGenerator] = None
        
        # Script configurations
        self.scripts = {
            'events': {
                'name': 'Events Fetcher',
                'script': 'scripts/fetch/fetch_events_parallel_optimized.py',
                'args': ['--upload', '--local'] if use_local_db else ['--upload']
            },
            'redemptions': {
                'name': 'Redemptions Fetcher',
                'script': 'scripts/fetch/fetch_redemptions.py',
                'args': ['--upload', '--local'] if use_local_db else ['--upload']
            },
            'positions': {
                'name': 'User Closed Positions',
                'script': 'scripts/fetch/fetch_user_closed_positions_parallel.py',
                'args': ['--upload', '--local'] if use_local_db else ['--upload']
            },
            'leaderboard': {
                'name': 'Trader Leaderboard',
                'script': 'scripts/fetch/fetch_trader_leaderboard_parallel.py',
                'args': ['--upload', '--local', '--from-db'] if use_local_db else ['--upload', '--from-db']
            }
        }

    @staticmethod
    def _utc_day_start(ts: datetime) -> datetime:
        """Normalize timestamp to 00:00:00 UTC."""
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        return datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)

    @staticmethod
    def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except Exception:
            return None

    def _get_standard_filtered_event_ids(self, cursor: Any, season_id: Optional[int] = None) -> List[str]:
        if season_id is None:
            cursor.execute(
                """
                WITH next_window AS (
                    SELECT
                        (
                            s.end_date
                            - make_interval(days => %s)
                            - make_interval(days => %s)
                        ) AS window_start,
                        (
                            s.end_date
                            - make_interval(days => %s)
                        ) AS window_end
                    FROM seasons s
                    WHERE s.type = 'standard'
                    ORDER BY s.start_date DESC, s.id DESC
                    LIMIT 1
                ),
                working_events AS (
                    SELECT
                        e.id AS event_id,
                        e.slug AS event_slug,
                        COALESCE(erq.resolution_ready_at, erq.closed_time) AS season_anchor_at,
                        COALESCE(e.volume, 0) AS event_volume,
                        COALESCE(NULLIF(BTRIM(ec.primary_tag), ''), '__UNTAGGED__') AS primary_tag
                    FROM events e
                    JOIN next_window nw ON TRUE
                    LEFT JOIN event_resolution_queue erq
                      ON erq.event_id = e.id
                    LEFT JOIN event_cards ec
                      ON ec.event_id = e.id
                    WHERE erq.status = 'processed'
                      AND erq.resolution_ready_at IS NOT NULL
                      AND erq.resolution_ready_at >= nw.window_start
                      AND erq.resolution_ready_at < nw.window_end
                      AND NOT EXISTS (
                          SELECT 1
                          FROM participants pp
                          WHERE (
                              (e.id IS NOT NULL AND pp.event_id = e.id)
                              OR (e.slug IS NOT NULL AND pp.event_slug = e.slug)
                          )
                      )
                ),
                tag_capped_events AS (
                    SELECT
                        ranked.event_id,
                        ranked.event_slug,
                        ranked.season_anchor_at,
                        ranked.event_volume
                    FROM (
                        SELECT
                            we.*,
                            ROW_NUMBER() OVER (
                                PARTITION BY we.primary_tag
                                ORDER BY
                                    we.event_volume DESC,
                                    we.season_anchor_at DESC NULLS LAST,
                                    COALESCE(we.event_id, we.event_slug) ASC
                            ) AS primary_tag_rank
                        FROM working_events we
                    ) ranked
                    WHERE ranked.primary_tag_rank <= %s
                ),
                final_events AS (
                    SELECT
                        ranked.event_id,
                        ranked.overall_rank
                    FROM (
                        SELECT
                            tce.event_id,
                            ROW_NUMBER() OVER (
                                ORDER BY
                                    tce.event_volume DESC,
                                    tce.season_anchor_at DESC NULLS LAST,
                                    COALESCE(tce.event_id, tce.event_slug) ASC
                            ) AS overall_rank
                        FROM tag_capped_events tce
                    ) ranked
                    WHERE ranked.overall_rank <= %s
                )
                SELECT event_id
                FROM final_events
                WHERE event_id IS NOT NULL
                ORDER BY overall_rank
                """,
                (
                    STANDARD_ORIGIN_SNAPSHOT_OFFSET_DAYS,
                    ORIGIN_LOOKBACK_DAYS_STANDARD,
                    STANDARD_ORIGIN_SNAPSHOT_OFFSET_DAYS,
                    STANDARD_SNAPSHOT_PRIMARY_TAG_CAP,
                    STANDARD_SNAPSHOT_EVENT_LIMIT,
                ),
            )
        else:
            cursor.execute(
                """
                WITH season_target AS (
                    SELECT id, start_date
                    FROM seasons
                    WHERE id = %s
                      AND type = 'standard'
                    LIMIT 1
                ),
                first_standard AS (
                    SELECT
                        s.id,
                        s.start_date,
                        (
                            s.start_date
                            - make_interval(days => %s)
                            - make_interval(days => %s)
                        ) AS window_start
                    FROM seasons s
                    WHERE s.type = 'standard'
                    ORDER BY s.start_date ASC, s.id ASC
                    LIMIT 1
                ),
                bounds AS (
                    SELECT
                        st.id AS season_id,
                        (
                            st.start_date
                            - make_interval(days => %s)
                            - make_interval(days => %s)
                        ) AS window_start,
                        (
                            st.start_date
                            - make_interval(days => %s)
                        ) AS window_end,
                        CASE
                            WHEN fs.id IS NOT NULL AND fs.id = st.id THEN TRUE
                            ELSE FALSE
                        END AS is_first_standard,
                        fs.window_start AS first_standard_window_start
                    FROM season_target st
                    LEFT JOIN first_standard fs ON TRUE
                ),
                working_events AS (
                    SELECT
                        e.id AS event_id,
                        e.slug AS event_slug,
                        MAX(COALESCE(erq.resolution_ready_at, erq.closed_time)) AS season_anchor_at,
                        COALESCE(e.volume, 0) AS event_volume,
                        COALESCE(NULLIF(BTRIM(ec.primary_tag), ''), '__UNTAGGED__') AS primary_tag
                    FROM events e
                    JOIN bounds b ON TRUE
                    LEFT JOIN event_resolution_queue erq
                      ON erq.event_id = e.id
                    LEFT JOIN event_cards ec
                      ON ec.event_id = e.id
                    WHERE (
                        (
                            COALESCE(erq.closed, FALSE) = TRUE
                            AND erq.status = 'processed'
                            AND erq.resolution_ready_at IS NOT NULL
                            AND erq.resolution_ready_at >= b.window_start
                            AND erq.resolution_ready_at < b.window_end
                        ) OR (
                            b.is_first_standard = TRUE
                            AND erq.status = 'processed'
                            AND erq.resolution_ready_at IS NOT NULL
                            AND erq.resolution_ready_at < b.first_standard_window_start
                            AND NOT (
                                COALESCE(e.end_date::date, e.creation_date::date, e.start_date::date)
                                BETWEEN %s AND %s
                            )
                        )
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM participants pp
                        WHERE pp.season_id <> b.season_id
                          AND (
                              (e.id IS NOT NULL AND pp.event_id = e.id)
                              OR (e.slug IS NOT NULL AND pp.event_slug = e.slug)
                          )
                    )
                    GROUP BY e.id, e.slug, e.volume, COALESCE(NULLIF(BTRIM(ec.primary_tag), ''), '__UNTAGGED__')
                ),
                tag_capped_events AS (
                    SELECT
                        ranked.event_id,
                        ranked.event_slug,
                        ranked.season_anchor_at,
                        ranked.event_volume
                    FROM (
                        SELECT
                            we.*,
                            ROW_NUMBER() OVER (
                                PARTITION BY we.primary_tag
                                ORDER BY
                                    we.event_volume DESC,
                                    we.season_anchor_at DESC NULLS LAST,
                                    COALESCE(we.event_id, we.event_slug) ASC
                            ) AS primary_tag_rank
                        FROM working_events we
                    ) ranked
                    WHERE ranked.primary_tag_rank <= %s
                ),
                final_events AS (
                    SELECT
                        ranked.event_id,
                        ranked.overall_rank
                    FROM (
                        SELECT
                            tce.event_id,
                            ROW_NUMBER() OVER (
                                ORDER BY
                                    tce.event_volume DESC,
                                    tce.season_anchor_at DESC NULLS LAST,
                                    COALESCE(tce.event_id, tce.event_slug) ASC
                            ) AS overall_rank
                        FROM tag_capped_events tce
                    ) ranked
                    WHERE ranked.overall_rank <= %s
                )
                SELECT event_id
                FROM final_events
                WHERE event_id IS NOT NULL
                ORDER BY overall_rank
                """,
                (
                    season_id,
                    STANDARD_ORIGIN_SNAPSHOT_OFFSET_DAYS,
                    ORIGIN_LOOKBACK_DAYS_STANDARD,
                    STANDARD_ORIGIN_SNAPSHOT_OFFSET_DAYS,
                    ORIGIN_LOOKBACK_DAYS_STANDARD,
                    STANDARD_ORIGIN_SNAPSHOT_OFFSET_DAYS,
                    GENESIS_START_DATE,
                    GENESIS_END_DATE,
                    STANDARD_SNAPSHOT_PRIMARY_TAG_CAP,
                    STANDARD_SNAPSHOT_EVENT_LIMIT,
                ),
            )
        rows = cursor.fetchall() or []
        event_ids: List[str] = []
        for row in rows:
            value = row.get("event_id") if isinstance(row, dict) else row[0]
            event_id = str(value or "").strip()
            if event_id:
                event_ids.append(event_id)
        return event_ids

    def _fetch_event_status_from_api(self, event_id: str) -> Dict[str, Optional[object]]:
        """
        Fetch event status from Gamma API.
        Returns: {'ok': bool, 'closed': bool|None, 'closed_time': datetime|None, 'error': str|None}
        """
        try:
            url = f"{GAMMA_API_BASE_URL}/events/{event_id}"
            response = requests.get(url, timeout=20)

            # Fallback in case direct endpoint is unavailable.
            if response.status_code == 404:
                list_url = f"{GAMMA_API_BASE_URL}/events"
                response = requests.get(list_url, params={"id": event_id, "limit": 1}, timeout=20)

            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                event = payload[0] if payload else {}
            else:
                event = payload or {}

            closed = bool(event.get("closed", False))
            closed_time_raw = event.get("closedTime") or event.get("closed_time")
            closed_time = self._parse_iso_datetime(closed_time_raw)

            return {"ok": True, "closed": closed, "closed_time": closed_time, "error": None}
        except Exception as exc:
            return {"ok": False, "closed": None, "closed_time": None, "error": str(exc)}

    def poll_pending_event_resolutions(self) -> Dict[str, int]:
        """Poll all pending events and update resolution queue."""
        pending_ids = self.manager.get_pending_resolution_event_ids(limit=self.pending_poll_limit)
        if not pending_ids:
            print("\n🟢 Resolution polling: no pending events")
            return {"checked": 0, "ready": 0, "pending": 0, "errors": 0}

        print(f"\n🔄 Resolution polling: checking {len(pending_ids):,} pending event(s)")
        checked = 0
        moved_to_ready = 0
        still_pending = 0
        errors = 0

        for event_id in pending_ids:
            status = self._fetch_event_status_from_api(event_id)
            checked += 1

            if not status["ok"]:
                errors += 1
                self.manager.update_resolution_from_api(
                    event_id=event_id,
                    closed=False,
                    closed_time=None,
                    error_text=status["error"],
                )
                continue

            closed = bool(status["closed"])
            closed_time = status["closed_time"]
            self.manager.update_resolution_from_api(
                event_id=event_id,
                closed=closed,
                closed_time=closed_time,
                error_text=None,
            )

            if closed and closed_time is not None:
                moved_to_ready += 1
            else:
                still_pending += 1

        print(
            f"   ✅ checked={checked:,}, ready={moved_to_ready:,}, "
            f"pending={still_pending:,}, errors={errors:,}"
        )
        return {"checked": checked, "ready": moved_to_ready, "pending": still_pending, "errors": errors}

    def _get_event_card_generator(self) -> Agent1QuantCardGenerator:
        if self._event_card_generator is None:
            self._event_card_generator = Agent1QuantCardGenerator(
                model=self.event_cards_model or None,
                prompt_version=self.event_cards_prompt_version,
            )
            # Persist resolved model name (including client default fallback).
            self.event_cards_model = self._event_card_generator.model
        return self._event_card_generator

    def _get_tag_color_generator(self) -> Agent2ColoristGenerator:
        if self._tag_color_generator is None:
            self._tag_color_generator = Agent2ColoristGenerator(
                model=self.tag_colors_model or None,
                prompt_version=self.tag_colors_prompt_version,
            )
            self.tag_colors_model = self._tag_color_generator.model
        return self._tag_color_generator

    def _assign_missing_primary_tag_colors(self, cursor: Any, event_ids: List[str], max_passes: int = 2) -> int:
        """
        Post-Agent1 check:
        after primary flags are set, assign colors to newly primary tags without color.
        """
        ids = [str(eid).strip() for eid in event_ids if str(eid or "").strip()]
        if not ids:
            return 0

        total_generated = 0
        generator = self._get_tag_color_generator()

        for _ in range(max(1, max_passes)):
            cursor.execute(
                """
                SELECT DISTINCT
                    t.id,
                    COALESCE(NULLIF(BTRIM(t.label), ''), t.id) AS effective_label
                FROM tags t
                JOIN event_tags et
                    ON et.tag_id = t.id
                WHERE et.event_id = ANY(%s)
                  AND COALESCE(t.is_primary, FALSE) = TRUE
                  AND t.hex_color IS NULL
                ORDER BY t.id ASC
                """,
                (ids,),
            )
            missing_rows = cursor.fetchall()
            if not missing_rows:
                break

            cursor.execute(
                """
                SELECT DISTINCT
                    COALESCE(NULLIF(BTRIM(label), ''), id) AS tag_label,
                    hex_color
                FROM tags
                WHERE COALESCE(is_primary, FALSE) = TRUE
                  AND hex_color IS NOT NULL
                ORDER BY tag_label ASC, hex_color ASC
                """
            )
            palette = [
                {"tag_label": str(row[0]), "hex_color": str(row[1])}
                for row in cursor.fetchall()
                if row and row[1]
            ]

            generated_this_pass = 0
            for tag_id, effective_label in missing_rows:
                try:
                    out = generator.generate(
                        {
                            "new_primary_tag": str(effective_label or tag_id),
                            "existing_palette": palette,
                        }
                    )
                except Exception as exc:
                    print(f"⚠️  Agent 2 failed for tag_id={tag_id}: {exc}")
                    continue

                cursor.execute(
                    """
                    UPDATE tags
                    SET hex_color = %s
                    WHERE id = %s
                      AND hex_color IS NULL
                    """,
                    (out.hex_color, tag_id),
                )
                if cursor.rowcount:
                    generated_this_pass += 1
                    palette.append(
                        {
                            "tag_label": str(effective_label or tag_id),
                            "hex_color": out.hex_color,
                        }
                    )

            total_generated += generated_this_pass
            if generated_this_pass == 0:
                break

        return total_generated

    def _fetch_event_card_payloads(self, cursor: Any, event_ids: List[str]) -> List[Dict[str, Any]]:
        cursor.execute(
            """
            SELECT
                e.id AS event_id,
                e.title,
                e.description,
                s.title AS series_title,
                s.recurrence AS series_recurrence,
                COALESCE(
                    ARRAY_REMOVE(ARRAY_AGG(DISTINCT t.label), NULL),
                    ARRAY[]::TEXT[]
                ) AS tags
            FROM events e
            LEFT JOIN series s
                ON s.id = e.series_id
            LEFT JOIN event_tags et
                ON et.event_id = e.id
            LEFT JOIN tags t
                ON t.id = et.tag_id
            WHERE e.id = ANY(%s)
            GROUP BY
                e.id,
                e.title,
                e.description,
                s.title,
                s.recurrence
            ORDER BY e.id ASC
            """,
            (event_ids,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def _get_genesis_event_ids_missing_cards(self, cursor: Any, limit: int) -> List[str]:
        force_include_ids = [str(i) for i in GENESIS_INCLUDE_EVENT_IDS]
        cursor.execute(
            """
            SELECT e.id
            FROM events e
            LEFT JOIN event_cards ec
                ON ec.event_id = e.id
               AND ec.status = 'ok'
            WHERE ec.event_id IS NULL
              AND (
                  COALESCE(e.end_date::date, e.creation_date::date, e.start_date::date)
                      BETWEEN %s AND %s
                  OR e.id = ANY(%s::text[])
              )
            ORDER BY COALESCE(e.end_date, e.creation_date, e.start_date) ASC, e.id ASC
            LIMIT %s
            """,
            (GENESIS_START_DATE, GENESIS_END_DATE, force_include_ids, limit),
        )
        rows = cursor.fetchall()
        event_ids: List[str] = []
        for row in rows:
            if isinstance(row, dict):
                event_ids.append(str(row.get("id")))
            else:
                event_ids.append(str(row[0]))
        return [eid for eid in event_ids if eid and eid.lower() != "none"]

    def _mark_event_card_success(
        self,
        cursor: Any,
        event_id: str,
        generated: Dict[str, Any],
    ) -> None:
        cursor.execute(
            """
            INSERT INTO event_cards (
                event_id,
                series_id,
                reccurence,
                manual_image_url,
                card_title,
                card_lore,
                primary_tag,
                secondary_tag,
                agent_name,
                model_name,
                prompt_version,
                status,
                error_text,
                generated_at,
                updated_at
            ) VALUES (
                %s,
                (SELECT e.series_id FROM events e WHERE e.id = %s),
                (
                    SELECT COALESCE(NULLIF(BTRIM(s.recurrence), ''), 'unique')
                    FROM events e
                    LEFT JOIN series s ON s.id = e.series_id
                    WHERE e.id = %s
                ),
                (
                    SELECT ec_existing.manual_image_url
                    FROM event_cards ec_existing
                    WHERE ec_existing.series_id = (SELECT e.series_id FROM events e WHERE e.id = %s)
                      AND ec_existing.event_id <> %s
                      AND ec_existing.manual_image_url IS NOT NULL
                      AND BTRIM(ec_existing.manual_image_url) <> ''
                    ORDER BY ec_existing.updated_at DESC NULLS LAST, ec_existing.generated_at DESC NULLS LAST, ec_existing.event_id ASC
                    LIMIT 1
                ),
                %s, %s, %s, %s, %s, %s, %s, 'ok', NULL, NOW(), NOW()
            )
            ON CONFLICT (event_id) DO UPDATE SET
                series_id = EXCLUDED.series_id,
                reccurence = EXCLUDED.reccurence,
                manual_image_url = COALESCE(event_cards.manual_image_url, EXCLUDED.manual_image_url),
                card_title = EXCLUDED.card_title,
                card_lore = EXCLUDED.card_lore,
                primary_tag = EXCLUDED.primary_tag,
                secondary_tag = EXCLUDED.secondary_tag,
                agent_name = EXCLUDED.agent_name,
                model_name = EXCLUDED.model_name,
                prompt_version = EXCLUDED.prompt_version,
                status = 'ok',
                error_text = NULL,
                generated_at = NOW(),
                updated_at = NOW()
            """,
            (
                event_id,
                event_id,
                event_id,
                event_id,
                event_id,
                generated.get("card_title"),
                generated.get("card_lore"),
                generated.get("primary_tag"),
                generated.get("secondary_tag"),
                self.event_cards_agent_name,
                self.event_cards_model,
                self.event_cards_prompt_version,
            ),
        )
        self._sync_event_tag_primary_flags(
            cursor=cursor,
            event_id=event_id,
            primary_tag=generated.get("primary_tag"),
        )

    def _mark_event_card_error(self, cursor: Any, event_id: str, error_text: str) -> None:
        err = (error_text or "unknown error").strip()
        if len(err) > 2000:
            err = err[:2000]

        cursor.execute(
            """
            INSERT INTO event_cards (
                event_id,
                series_id,
                reccurence,
                manual_image_url,
                card_title,
                card_lore,
                primary_tag,
                secondary_tag,
                agent_name,
                model_name,
                prompt_version,
                status,
                error_text,
                generated_at,
                updated_at
            ) VALUES (
                %s,
                (SELECT e.series_id FROM events e WHERE e.id = %s),
                (
                    SELECT COALESCE(NULLIF(BTRIM(s.recurrence), ''), 'unique')
                    FROM events e
                    LEFT JOIN series s ON s.id = e.series_id
                    WHERE e.id = %s
                ),
                (
                    SELECT ec_existing.manual_image_url
                    FROM event_cards ec_existing
                    WHERE ec_existing.series_id = (SELECT e.series_id FROM events e WHERE e.id = %s)
                      AND ec_existing.event_id <> %s
                      AND ec_existing.manual_image_url IS NOT NULL
                      AND BTRIM(ec_existing.manual_image_url) <> ''
                    ORDER BY ec_existing.updated_at DESC NULLS LAST, ec_existing.generated_at DESC NULLS LAST, ec_existing.event_id ASC
                    LIMIT 1
                ),
                NULL, NULL, NULL, NULL, %s, %s, %s, 'error', %s, NOW(), NOW()
            )
            ON CONFLICT (event_id) DO UPDATE SET
                series_id = EXCLUDED.series_id,
                reccurence = EXCLUDED.reccurence,
                manual_image_url = COALESCE(event_cards.manual_image_url, EXCLUDED.manual_image_url),
                status = 'error',
                error_text = EXCLUDED.error_text,
                agent_name = EXCLUDED.agent_name,
                model_name = EXCLUDED.model_name,
                prompt_version = EXCLUDED.prompt_version,
                updated_at = NOW()
            """,
            (
                event_id,
                event_id,
                event_id,
                event_id,
                event_id,
                self.event_cards_agent_name,
                self.event_cards_model,
                self.event_cards_prompt_version,
                err,
            ),
        )
        self._sync_event_tag_primary_flags(
            cursor=cursor,
            event_id=event_id,
            primary_tag=None,
        )

    def _sync_event_tag_primary_flags(self, cursor: Any, event_id: str, primary_tag: Optional[str]) -> None:
        normalized_primary = (primary_tag or "").strip() or None
        if not normalized_primary:
            return
        cursor.execute(
            """
            UPDATE tags t
            SET is_primary = TRUE
            FROM event_tags et
            WHERE et.event_id = %s
              AND et.tag_id = t.id
              AND LOWER(BTRIM(t.label)) = LOWER(BTRIM(%s))
              AND COALESCE(t.is_primary, FALSE) = FALSE
            """,
            (event_id, normalized_primary),
        )

    def generate_event_cards_for_event_ids(self, event_ids: List[str]) -> Dict[str, int]:
        """
        Generate/update event cards for specific event ids.
        Intended to run after downstream scripts finish for those events.
        """
        if not self.event_cards_post_downstream_enabled:
            return {"requested": 0, "processed": 0, "success": 0, "failed": 0}
        if self.dry_run:
            return {"requested": len(event_ids), "processed": 0, "success": 0, "failed": 0}

        cleaned_ids: List[str] = []
        seen: set[str] = set()
        for raw in event_ids:
            event_id = str(raw or "").strip()
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)
            cleaned_ids.append(event_id)
        if not cleaned_ids:
            return {"requested": 0, "processed": 0, "success": 0, "failed": 0}

        requested = len(cleaned_ids)
        if self.event_cards_max_per_run > 0:
            cleaned_ids = cleaned_ids[: self.event_cards_max_per_run]

        generator = self._get_event_card_generator()
        conn = self.manager.get_connection()
        try:
            processed = 0
            success = 0
            failed = 0
            tag_colors_generated = 0

            for idx in range(0, len(cleaned_ids), max(1, self.event_cards_batch_size)):
                batch_ids = cleaned_ids[idx : idx + max(1, self.event_cards_batch_size)]
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    rows = self._fetch_event_card_payloads(cursor, event_ids=batch_ids)
                    conn.commit()

                by_event_id = {str(row.get("event_id")): row for row in rows}
                for event_id in batch_ids:
                    row = by_event_id.get(event_id)
                    if not row:
                        failed += 1
                        continue
                    payload = {
                        "title": row.get("title"),
                        "description": row.get("description"),
                        "series": {
                            "title": row.get("series_title"),
                            "recurrence": row.get("series_recurrence"),
                        },
                        "tags": row.get("tags") or [],
                    }
                    try:
                        card = generator.generate(payload)
                        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                            self._mark_event_card_success(cursor, event_id=event_id, generated=card.model_dump())
                            conn.commit()
                        success += 1
                    except Exception as exc:
                        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                            self._mark_event_card_error(cursor, event_id=event_id, error_text=str(exc))
                            conn.commit()
                        failed += 1
                    processed += 1

                with conn.cursor() as cursor:
                    tag_colors_generated += self._assign_missing_primary_tag_colors(
                        cursor,
                        event_ids=batch_ids,
                    )
                    conn.commit()

            return {
                "requested": requested,
                "processed": processed,
                "success": success,
                "failed": failed,
                "tag_colors_generated": tag_colors_generated,
            }
        finally:
            conn.close()

    def _create_standard_season(self, cursor, start_date: datetime, season_number: int) -> int:
        """Create a new standard season and return its id."""
        start_date = self._utc_day_start(start_date)
        end_date = start_date + timedelta(days=STANDARD_SEASON_CYCLE_DAYS)
        cursor.execute("""
            INSERT INTO seasons (
                type,
                season_number,
                start_date,
                end_date,
                total_supply,
                remaining_supply,
                is_active,
                is_completed,
                created_at,
                updated_at
            )
            VALUES (
                'standard',
                %s,
                %s,
                %s,
                %s,
                %s,
                TRUE,
                FALSE,
                NOW(),
                NOW()
            )
            RETURNING id
        """, (
            season_number,
            start_date,
            end_date,
            STANDARD_SEASON_TOTAL_SUPPLY_TEST,
            STANDARD_SEASON_TOTAL_SUPPLY_TEST,
        ))
        inserted = cursor.fetchone()
        if isinstance(inserted, dict):
            return inserted["id"]
        return inserted[0]

    def _create_genesis_season(self, cursor, start_date: datetime, season_number: int) -> int:
        """Create a new genesis season and return its id."""
        start_date = self._utc_day_start(start_date)
        cursor.execute("""
            INSERT INTO seasons (
                type,
                season_number,
                start_date,
                end_date,
                total_supply,
                remaining_supply,
                is_active,
                is_completed,
                created_at,
                updated_at
            )
            VALUES (
                'genesis',
                %s,
                %s,
                %s,
                %s,
                %s,
                TRUE,
                FALSE,
                NOW(),
                NOW()
            )
            RETURNING id
        """, (
            season_number,
            start_date,
            GENESIS_SEASON_FAR_FUTURE_END,
            GENESIS_SEASON_TOTAL_SUPPLY_TEST,
            GENESIS_SEASON_TOTAL_SUPPLY_TEST,
        ))
        inserted = cursor.fetchone()
        if isinstance(inserted, dict):
            return inserted["id"]
        return inserted[0]

    def _snapshot_origin_wallets_for_season(self, cursor, season_id: int, season_start_date: datetime) -> int:
        """
        Materialize the season's ``participants`` partition.

        Under the queue model this is the only side-effect that matters at
        season-creation time. Returns the number of rows loaded into the
        partition so callers can keep their existing logging.

        Window selection:
        - standard: [season_start - STANDARD_ORIGIN_SNAPSHOT_OFFSET_DAYS - 10d,
                     season_start - STANDARD_ORIGIN_SNAPSHOT_OFFSET_DAYS)
                    anchored by event_resolution_queue.resolution_ready_at
        - genesis:  [GENESIS_START_DATE, GENESIS_END_DATE + 1 day)
                    anchored by events.end_date / creation_date / start_date
        """
        season_start_date = self._utc_day_start(season_start_date)
        cursor.execute("SELECT type FROM seasons WHERE id = %s", (season_id,))
        season_row = cursor.fetchone()
        season_type = (season_row or {}).get("type") if isinstance(season_row, dict) else (season_row[0] if season_row else None)
        if season_type == "standard":
            window_end = season_start_date - timedelta(days=STANDARD_ORIGIN_SNAPSHOT_OFFSET_DAYS)
            window_start = window_end - timedelta(days=ORIGIN_LOOKBACK_DAYS_STANDARD)
            use_resolution_anchor = True
        else:
            window_start = datetime.combine(GENESIS_START_DATE, datetime.min.time(), tzinfo=timezone.utc)
            window_end = datetime.combine(
                GENESIS_END_DATE + timedelta(days=1),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
            use_resolution_anchor = False

        cursor.execute("SELECT participants_ensure_partition(%s)", (season_id,))
        cursor.execute(
            "SELECT refresh_participants_for_season(%s, %s, %s, %s) AS n",
            (season_id, window_start, window_end, use_resolution_anchor),
        )
        result = cursor.fetchone()
        if isinstance(result, dict):
            return int(result.get("n") or 0)
        return int((result or [0])[0] or 0)

    def ensure_genesis_season(self, cursor, now: datetime):
        """
        Ensure there is at most one Genesis season in total.

        Genesis is a one-time stream. If any Genesis season already exists
        (active or completed), do not create a new one.
        """
        cursor.execute("""
            SELECT id, season_number
            FROM seasons
            WHERE type = 'genesis'
            ORDER BY start_date DESC, id DESC
            LIMIT 1
        """)
        existing_genesis = cursor.fetchone()
        if existing_genesis:
            return int(existing_genesis["id"]), False

        cursor.execute("""
            SELECT COALESCE(MAX(season_number), 0) AS max_season_number
            FROM seasons
            WHERE type = 'genesis'
        """)
        max_row = cursor.fetchone()
        next_season_number = int(max_row["max_season_number"]) + 1

        genesis_start = self._utc_day_start(
            datetime.combine(
                GENESIS_END_DATE + timedelta(days=GENESIS_START_OFFSET_FROM_END_DAYS),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
        )
        new_genesis_id = self._create_genesis_season(
            cursor,
            start_date=genesis_start,
            season_number=next_season_number,
        )
        return new_genesis_id, True

    def run_standard_season_update(self):
        """
        Manage standard season lifecycle:
        - Auto-create first season if none exists
        - First standard season starts at genesis_start + DATA_LAG_DAYS
        - Hard Stop burn at day 9 if supply remains
        - Ghost State wait if sold out early
        - Start next season exactly at day 10 boundary
        """
        if self.dry_run:
            print("\n🎮 Season update skipped in DRY RUN mode")
            return

        conn = self.manager.get_connection()
        advisory_lock_key = 90421017
        advisory_lock_acquired = False
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                # Prevent duplicate season transitions when multiple scheduler runs overlap.
                cursor.execute("SELECT pg_try_advisory_lock(%s) AS locked", (advisory_lock_key,))
                lock_row = cursor.fetchone() or {}
                advisory_lock_acquired = bool(lock_row.get("locked")) if isinstance(lock_row, dict) else bool(lock_row[0])
                if not advisory_lock_acquired:
                    print("\n⏭️ Season update skipped: another process is already running season rotation")
                    return

                now = datetime.now(timezone.utc)
                print("\n" + "=" * 70)
                print("🎮 SEASON LIFECYCLE UPDATE")
                print("=" * 70)
                print(f"🕒 Now (UTC): {now.isoformat()}")

                genesis_id, created_genesis = self.ensure_genesis_season(cursor, now)
                if created_genesis:
                    origins_count = self._snapshot_origin_wallets_for_season(
                        cursor,
                        genesis_id,
                        self._utc_day_start(now),
                    )
                    conn.commit()
                    self.manager.log_season_update(
                        event_name="genesis_created",
                        season_id=genesis_id,
                        details=(
                            f"season_number=auto "
                            f"start_date={now.isoformat()} "
                            f"end_date={GENESIS_SEASON_FAR_FUTURE_END.isoformat()} "
                            f"origin_snapshot_count={origins_count}"
                        ),
                    )
                    print(f"\n🧬 Created Genesis Season (id={genesis_id})")
                    print(f"   🏛️  Origin snapshot saved: {origins_count} wallets")
                else:
                    cursor.execute("""
                        SELECT id, season_number, start_date, end_date, total_supply, remaining_supply, is_active
                        FROM seasons
                        WHERE id = %s
                    """, (genesis_id,))
                    genesis = cursor.fetchone()
                    if genesis:
                        print(
                            f"\n🧬 Genesis found: id={genesis['id']} "
                            f"season_number={genesis['season_number']} "
                            f"supply={genesis['remaining_supply']}/{genesis['total_supply']} "
                            f"active={genesis['is_active']}"
                        )
                    cursor.execute(
                        """
                        SELECT COUNT(*)
                        FROM participants
                        WHERE season_id = %s
                        """,
                        (genesis_id,),
                    )
                    snapshot_row = cursor.fetchone() or {}
                    if isinstance(snapshot_row, dict):
                        genesis_snapshot_count = int(snapshot_row.get("count", 0))
                    else:
                        genesis_snapshot_count = int(snapshot_row[0] if snapshot_row else 0)
                    if genesis_snapshot_count == 0:
                        snapshot_ref = self._utc_day_start(
                            genesis["start_date"] if genesis and genesis.get("start_date") else now
                        )
                        origins_count = self._snapshot_origin_wallets_for_season(
                            cursor,
                            genesis_id,
                            snapshot_ref,
                        )
                        conn.commit()
                        self.manager.log_season_update(
                            event_name="genesis_snapshot_backfilled",
                            season_id=genesis_id,
                            details=(
                                f"reason=missing_snapshot "
                                f"origin_snapshot_count={origins_count}"
                            ),
                        )
                        print(
                            f"   🏛️  Genesis snapshot backfilled: {origins_count} wallets"
                        )

                cursor.execute("""
                    SELECT
                        id,
                        type,
                        season_number,
                        start_date,
                        end_date,
                        total_supply,
                        remaining_supply,
                        is_active,
                        is_completed
                    FROM seasons
                    WHERE type = 'standard'
                    ORDER BY start_date DESC, id DESC
                    LIMIT 1
                """)
                latest = cursor.fetchone()

                # Bootstrap: create first standard season if table has no standard seasons.
                if not latest:
                    cursor.execute(
                        """
                        SELECT start_date
                        FROM seasons
                        WHERE id = %s
                        """,
                        (genesis_id,),
                    )
                    genesis_row = cursor.fetchone()
                    if not genesis_row:
                        raise RuntimeError(
                            f"Genesis season {genesis_id} not found while bootstrapping standard season"
                        )
                    genesis_start = self._utc_day_start(genesis_row["start_date"])
                    season_start = genesis_start + timedelta(days=FIRST_STANDARD_BOOTSTRAP_DELAY_DAYS)

                    if now < season_start:
                        print(
                            "\nℹ️ No standard season found yet. "
                            "Waiting for genesis+DATA_LAG_DAYS boundary to start Standard #1..."
                        )
                        print(f"   genesis_start={genesis_start.isoformat()}")
                        print(f"   data_lag_days={DATA_LAG_DAYS}")
                        print(f"   standard_start_at={season_start.isoformat()}")
                        print("=" * 70)
                        return

                    print("\nℹ️ No standard season found. Bootstrapping all standard seasons up to now...")
                    bootstrap_start = season_start
                    bootstrap_number = 1
                    created_count = 0
                    reused_count = 0
                    snapped_count = 0
                    current_season_id: Optional[int] = None
                    current_season_number: Optional[int] = None
                    current_season_start: Optional[datetime] = None
                    latest_cursor_start = bootstrap_start
                    while latest_cursor_start <= now:
                        cursor.execute(
                            """
                            SELECT id, season_number
                            FROM seasons
                            WHERE type = 'standard'
                              AND (start_date = %s OR season_number = %s)
                            ORDER BY id DESC
                            LIMIT 1
                            """,
                            (latest_cursor_start, bootstrap_number),
                        )
                        existing_for_start = cursor.fetchone()
                        if existing_for_start:
                            season_id_for_step = int(existing_for_start["id"])
                            season_number_for_step = int(existing_for_start["season_number"])
                            reused_count += 1
                        else:
                            season_id_for_step = self._create_standard_season(
                                cursor,
                                latest_cursor_start,
                                bootstrap_number,
                            )
                            season_number_for_step = bootstrap_number
                            created_count += 1
                        cursor.execute(
                            """
                            SELECT COUNT(*)
                            FROM participants
                            WHERE season_id = %s
                            """,
                            (season_id_for_step,),
                        )
                        snapshot_row = cursor.fetchone() or {}
                        if isinstance(snapshot_row, dict):
                            snapshot_count = int(snapshot_row.get("count", 0))
                        else:
                            snapshot_count = int(snapshot_row[0] if snapshot_row else 0)
                        if snapshot_count == 0:
                            self._snapshot_origin_wallets_for_season(
                                cursor,
                                season_id_for_step,
                                latest_cursor_start,
                            )
                            snapped_count += 1
                        step_end = latest_cursor_start + timedelta(days=STANDARD_SEASON_CYCLE_DAYS)
                        if step_end <= now:
                            cursor.execute(
                                """
                                UPDATE seasons
                                SET is_active = FALSE,
                                    is_completed = TRUE,
                                    updated_at = NOW()
                                WHERE id = %s
                                """,
                                (season_id_for_step,),
                            )
                        elif latest_cursor_start <= now < step_end:
                            current_season_id = season_id_for_step
                            current_season_number = season_number_for_step
                            current_season_start = latest_cursor_start
                        latest_cursor_start = step_end
                        bootstrap_number += 1

                    if current_season_id is not None:
                        cursor.execute(
                            """
                            UPDATE seasons
                            SET is_active = TRUE,
                                is_completed = FALSE,
                                updated_at = NOW()
                            WHERE id = %s
                            """,
                            (current_season_id,),
                        )

                    conn.commit()
                    if current_season_id is not None and current_season_number is not None and current_season_start is not None:
                        self.manager.log_season_update(
                            event_name="standard_bootstrap_catchup_completed",
                            season_id=current_season_id,
                            details=(
                                f"active_season_number={current_season_number} "
                                f"active_start_date={current_season_start.isoformat()} "
                                f"created={created_count} reused={reused_count} snapshots_filled={snapped_count}"
                            ),
                        )
                    print(
                        f"\n✅ Standard bootstrap catch-up completed: created={created_count}, "
                        f"reused={reused_count}, snapshots_filled={snapped_count}"
                    )
                    if current_season_id is not None and current_season_number is not None:
                        print(
                            f"   🎮 Active Standard Season #{current_season_number} "
                            f"(id={current_season_id})"
                        )
                    print("=" * 70)
                    return

                season_id = int(latest["id"])
                season_number = int(latest["season_number"])
                start_date = self._utc_day_start(latest["start_date"])
                total_supply = int(latest["total_supply"])
                remaining_supply = int(latest["remaining_supply"])
                is_active = bool(latest["is_active"])
                is_completed = bool(latest.get("is_completed", False))
                claimed_supply = max(total_supply - remaining_supply, 0)

                hard_stop_at = start_date + timedelta(days=STANDARD_SEASON_ACTIVE_DAYS)
                next_cycle_at = start_date + timedelta(days=STANDARD_SEASON_CYCLE_DAYS)
                print(
                    f"\n📦 Standard state: id={season_id} season_number={season_number} "
                    f"supply={remaining_supply}/{total_supply} active={is_active} completed={is_completed}"
                )
                print(
                    f"   start={start_date.isoformat()} | hard_stop={hard_stop_at.isoformat()} | "
                    f"next_cycle={next_cycle_at.isoformat()}"
                )

                # Ghost State: sold out before day 9 -> wait until day 10 boundary.
                if now < hard_stop_at and remaining_supply == 0:
                    if is_active:
                        cursor.execute("""
                            UPDATE seasons
                            SET is_active = FALSE, updated_at = NOW()
                            WHERE id = %s
                        """, (season_id,))
                        conn.commit()
                        self.manager.log_season_update(
                            event_name="ghost_state_entered",
                            season_id=season_id,
                            details=f"sold_out_early=true cycle_restart_at={next_cycle_at.isoformat()}",
                        )
                        print(f"\n👻 Season {season_id} entered Ghost State (waiting until cycle day 10)")
                    else:
                        print(f"\n👻 Season {season_id} already in Ghost State, waiting for next cycle boundary")
                    print("=" * 70)
                    return

                # Day 9 hard stop window (Transmission start) - no new season yet.
                if hard_stop_at <= now < next_cycle_at:
                    if remaining_supply > 0:
                        cursor.execute("""
                            UPDATE seasons
                            SET
                                total_supply = %s,
                                remaining_supply = 0,
                                is_active = FALSE,
                                is_completed = TRUE,
                                updated_at = NOW()
                            WHERE id = %s
                        """, (claimed_supply, season_id))
                        conn.commit()
                        self.manager.log_season_update(
                            event_name="hard_stop_burn",
                            season_id=season_id,
                            details=f"burned={remaining_supply} new_total_supply={claimed_supply} transmission_started_at={now.isoformat()}",
                        )
                        print(f"\n🔥 Hard Stop Burn applied to season {season_id}: burned {remaining_supply}")
                    elif is_active:
                        cursor.execute("""
                            UPDATE seasons
                            SET
                                is_active = FALSE,
                                is_completed = TRUE,
                                updated_at = NOW()
                            WHERE id = %s
                        """, (season_id,))
                        conn.commit()
                        self.manager.log_season_update(
                            event_name="transmission_started",
                            season_id=season_id,
                            details=f"transmission_started_at={now.isoformat()} sold_out=true",
                        )
                        print(f"\n📡 Season {season_id} moved to Transmission")
                    else:
                        print(f"\n📡 Season {season_id} already closed in Transmission window")
                    print("=" * 70)
                    return

                # Day 10 boundary reached: start next cycle exactly at start+10 days.
                if now >= next_cycle_at:
                    if is_active or not is_completed:
                        cursor.execute("""
                            UPDATE seasons
                            SET
                                is_active = FALSE,
                                is_completed = TRUE,
                                updated_at = NOW()
                            WHERE id = %s
                        """, (season_id,))

                    cursor.execute("""
                        SELECT id
                        FROM seasons
                        WHERE type = 'standard'
                          AND is_active = TRUE
                        ORDER BY start_date DESC, id DESC
                        LIMIT 1
                    """)
                    active_standard = cursor.fetchone()
                    if active_standard:
                        conn.commit()
                        return

                    new_season_number = season_number + 1
                    cursor.execute(
                        """
                        SELECT id, season_number
                        FROM seasons
                        WHERE type = 'standard'
                          AND (start_date = %s OR season_number = %s)
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (next_cycle_at, new_season_number),
                    )
                    existing_for_cycle = cursor.fetchone()
                    if existing_for_cycle:
                        new_season_id = int(existing_for_cycle["id"])
                        new_season_number = int(existing_for_cycle["season_number"])
                        cursor.execute(
                            """
                            UPDATE seasons
                            SET is_active = TRUE,
                                is_completed = FALSE,
                                updated_at = NOW()
                            WHERE id = %s
                            """,
                            (new_season_id,),
                        )
                        print(
                            f"\n♻️ Reusing existing Standard Season "
                            f"#{new_season_number} (id={new_season_id}) for start={next_cycle_at.isoformat()}"
                        )
                    else:
                        new_season_id = self._create_standard_season(
                            cursor,
                            start_date=next_cycle_at,
                            season_number=new_season_number,
                        )
                    origins_count = self._snapshot_origin_wallets_for_season(cursor, new_season_id, next_cycle_at)
                    conn.commit()
                    self.manager.log_season_update(
                        event_name="new_standard_season_started",
                        season_id=new_season_id,
                        details=(
                            f"season_number={new_season_number} "
                            f"start_date={next_cycle_at.isoformat()} "
                            f"previous_season_id={season_id} "
                            f"origin_snapshot_count={origins_count}"
                        ),
                    )
                    print(f"\n🎮 Started Standard Season #{new_season_number} (id={new_season_id})")
                    print(f"   🏛️  Origin snapshot saved: {origins_count} wallets")
                    print("=" * 70)
                    return

                # Active cycle before hard stop with positive supply: ensure active marker.
                if not is_active and remaining_supply > 0 and now < hard_stop_at:
                    cursor.execute("""
                        UPDATE seasons
                        SET is_active = TRUE, updated_at = NOW()
                        WHERE id = %s
                    """, (season_id,))
                    conn.commit()
                    self.manager.log_season_update(
                        event_name="season_reactivated",
                        season_id=season_id,
                        details="active_window_not_finished_and_supply_available",
                    )
                    print(f"\n🔁 Season {season_id} reactivated")
                    print("=" * 70)
                    return

                print("\n✅ No season state transition required")
                print("=" * 70)
        finally:
            if advisory_lock_acquired:
                try:
                    with conn.cursor() as unlock_cursor:
                        unlock_cursor.execute("SELECT pg_advisory_unlock(%s)", (advisory_lock_key,))
                except Exception:
                    pass
            conn.close()
    
    def check_system_state(self):
        """Check and display system state"""
        print("\n" + "="*70)
        print("🔍 SYSTEM STATE CHECK")
        print("="*70)
        
        # Check testing mode
        events_limit = self.manager.get_events_limit()
        max_volume = self.manager.get_max_volume_filter()
        if events_limit or max_volume:
            print(f"\n⚠️  TESTING MODE ACTIVE:")
            if events_limit:
                print(f"  • MAX_EVENTS: {events_limit} (limited event count)")
            if max_volume:
                print(f"  • MAX_VOLUME: ${max_volume:,} (excludes large events)")
            print(f"  • Change in scripts/data_loading_manager.py")
        
        # Check if any data exists
        has_data = self.manager.has_any_data()
        needs_genesis = self.manager.needs_genesis_load()
        
        print(f"\nDatabase Status:")
        print(f"  • Has data: {'Yes' if has_data else 'No (Empty)'}")
        print(f"  • Genesis loaded: {'Yes ✅' if not needs_genesis else 'No ❌'}")
        
        if needs_genesis:
            print(f"\n⚠️  Genesis data needs to be loaded!")
            print(f"  Period: {GENESIS_START_DATE} to {GENESIS_END_DATE}")
            print(f"  Filter: 100M volume")
            print(f"\n  👉 Run: python scripts/daily_scheduler_simple.py --historical")
        else:
            print(f"\n✅ Genesis loaded - ready for daily operations")
            
            # Show today's dates
            dates = self.manager.get_loading_dates()
            print(f"\nToday's Loading Dates ({dates['reference_date']}):")
            print(f"  • Events: {dates['events_date']} ({EVENTS_LAG_DAYS} day{'s' if EVENTS_LAG_DAYS > 1 else ''} ago)")
            print(f"  • Downstream trigger: closed_time + {RESOLUTION_READY_OFFSET_DAYS} day(s)")
            
            # Check if today's data loaded
            events_loaded = self.manager.is_data_loaded_for_date(dates['events_date'], 'events')
            
            print(f"\nToday's Status:")
            print(f"  • Events ({dates['events_date']}): {'✅ Loaded' if events_loaded else '⏳ Pending'}")
            
            # Check for missing dates
            last_loaded = self.manager.get_last_loaded_date('events')
            if last_loaded:
                missing = self.manager.get_missing_dates(
                    start_from=GENESIS_END_DATE + timedelta(days=1),
                    up_to=dates['events_date']
                )
                
                if missing:
                    print(f"\n⚠️  GAP DETECTED: Missing {len(missing)} day(s) of data!")
                    print(f"  Last loaded: {last_loaded}")
                    print(f"  Gap: {missing[0]} to {missing[-1]}")
                    print(f"\n  👉 Run: python scripts/daily_scheduler_simple.py --catch-up")
                    print(f"     This will load all missing days automatically")
        
        print("="*70)
    
    def configure_for_date(self, target_date: date, is_genesis: bool = False):
        """
        Configure fetch_events_config for specific date
        
        Args:
            target_date: Date to load
            is_genesis: Whether this is Genesis load
        """
        try:
            # Calculate dates
            if is_genesis:
                start_date = GENESIS_START_DATE
                end_date = GENESIS_END_DATE
            else:
                start_date = target_date
                end_date = target_date
            
            # Set environment variables (will be passed to subprocesses)
            os.environ['POLYSTARS_START_DATE'] = start_date.strftime('%Y-%m-%d')
            os.environ['POLYSTARS_END_DATE'] = end_date.strftime('%Y-%m-%d')
            os.environ['POLYSTARS_MIN_VOLUME'] = str(self.manager.get_volume_filter(is_genesis=is_genesis))
            os.environ['POLYSTARS_IS_GENESIS'] = 'true' if is_genesis else 'false'

            # In closed_time pipeline, events ingestion must not require closed=true.
            if not is_genesis:
                # 'none' means "omit closed filter" (fetch both open and closed).
                os.environ['POLYSTARS_EVENTS_CLOSED_ONLY'] = 'none'
                os.environ['POLYSTARS_RESOLUTION_STATUS'] = ''
            else:
                os.environ.pop('POLYSTARS_EVENTS_CLOSED_ONLY', None)
                os.environ.pop('POLYSTARS_RESOLUTION_STATUS', None)
            
            # Set MAX_EVENTS if specified (for testing)
            events_limit = self.manager.get_events_limit()
            if events_limit:
                os.environ['POLYSTARS_MAX_EVENTS'] = str(events_limit)
            elif 'POLYSTARS_MAX_EVENTS' in os.environ:
                # Clear if was set before
                del os.environ['POLYSTARS_MAX_EVENTS']
            
            # Set MAX_VOLUME if specified (for testing)
            max_volume = self.manager.get_max_volume_filter()
            if max_volume:
                os.environ['POLYSTARS_MAX_VOLUME'] = str(max_volume)
            elif 'POLYSTARS_MAX_VOLUME' in os.environ:
                # Clear if was set before
                del os.environ['POLYSTARS_MAX_VOLUME']
            
            volume_label = "100M (Genesis)" if is_genesis else "5M (Daily)"
            print(f"📅 Config set (via env vars):")
            print(f"   Date range: {start_date} to {end_date}")
            print(f"   MIN_VOLUME: {os.environ['POLYSTARS_MIN_VOLUME']} ({volume_label})")
            
            # Show testing mode warnings
            if max_volume or events_limit:
                print(f"\n   ⚠️  TESTING MODE ACTIVE:")
                if max_volume:
                    print(f"      • MAX_VOLUME: ${max_volume:,} (excludes events over this)")
                if events_limit:
                    print(f"      • MAX_EVENTS: {events_limit} events (limits total count)")
                print(f"      • Set both to None in data_loading_manager.py for production")
            
        except Exception as e:
            print(f"⚠️  Could not configure: {e}")
    
    def run_script(self, script_key: str, target_date: Optional[date] = None) -> Dict:
        """Run a data fetching script and track record counts"""
        script_config = self.scripts[script_key]
        
        print(f"\n{'='*70}")
        print(f"🚀 RUNNING: {script_config['name']}")
        print(f"{'='*70}")
        
        if self.dry_run:
            print("🔍 DRY RUN - Skipping")
            return {'success': True, 'duration': 0, 'records': 0, 'markets': 0}
        
        # Map script keys to table names
        table_map = {
            'events': 'events',
            'redemptions': 'redemptions',
            'positions': 'user_closed_positions',
            'leaderboard': 'trader_leaderboard'
        }
        
        # Get count BEFORE running script
        table_name = table_map.get(script_key)
        count_before = self.manager.get_table_count(table_name) if table_name else 0
        markets_before = self.manager.get_table_count('markets') if script_key == 'events' else 0
        events_for_date_before = 0
        markets_for_date_before = 0
        redemptions_for_date_before = 0
        positions_for_date_before = 0
        if script_key == 'events' and target_date:
            events_for_date_before = self.manager.get_events_count_for_date(target_date)
            markets_for_date_before = self.manager.get_markets_count_for_event_date(target_date)
        elif script_key == 'redemptions' and target_date:
            redemptions_for_date_before = self.manager.get_redemptions_count_for_event_date(target_date)
        elif script_key == 'positions' and target_date:
            positions_for_date_before = self.manager.get_positions_count_for_event_date(target_date)
        
        start_time = time.time()
        
        try:
            # Use the same Python interpreter that's running this script
            cmd = [sys.executable, script_config['script']] + script_config['args']
            result = subprocess.run(cmd, cwd=project_root, check=True)
            
            duration = time.time() - start_time
            
            # Get count AFTER running script
            count_after = self.manager.get_table_count(table_name) if table_name else 0
            markets_after = self.manager.get_table_count('markets') if script_key == 'events' else 0
            
            # Calculate actual loaded records
            records = count_after - count_before
            markets = markets_after - markets_before

            # For daily/catch-up events, use absolute date-scoped counts.
            # This stays correct even if data_loads is reset but tables already contain rows.
            if script_key == 'events' and target_date:
                events_for_date_after = self.manager.get_events_count_for_date(target_date)
                markets_for_date_after = self.manager.get_markets_count_for_event_date(target_date)
                events_delta = events_for_date_after - events_for_date_before
                markets_delta = markets_for_date_after - markets_for_date_before
                records = events_for_date_after
                markets = markets_for_date_after
            elif script_key == 'redemptions' and target_date:
                redemptions_for_date_after = self.manager.get_redemptions_count_for_event_date(target_date)
                redemptions_delta = redemptions_for_date_after - redemptions_for_date_before
                records = redemptions_for_date_after
            elif script_key == 'positions' and target_date:
                positions_for_date_after = self.manager.get_positions_count_for_event_date(target_date)
                positions_delta = positions_for_date_after - positions_for_date_before
                records = positions_for_date_after
            
            if script_key == 'events':
                if target_date:
                    print(
                        f"\n✅ Completed ({duration:.1f}s) - "
                        f"{records:,} events, {markets:,} markets for {target_date} "
                        f"(Δ{events_delta:+,} events, Δ{markets_delta:+,} markets this run)"
                    )
                else:
                    print(f"\n✅ Completed ({duration:.1f}s) - {records:,} events, {markets:,} markets")
            elif script_key == 'redemptions' and target_date:
                print(
                    f"\n✅ Completed ({duration:.1f}s) - "
                    f"{records:,} redemptions for {target_date} "
                    f"(Δ{redemptions_delta:+,} this run)"
                )
            elif script_key == 'positions' and target_date:
                print(
                    f"\n✅ Completed ({duration:.1f}s) - "
                    f"{records:,} positions for {target_date} "
                    f"(Δ{positions_delta:+,} this run)"
                )
            else:
                print(f"\n✅ Completed ({duration:.1f}s) - {records:,} records")
            
            return {'success': True, 'duration': duration, 'records': records, 'markets': markets}
            
        except subprocess.CalledProcessError as e:
            duration = time.time() - start_time
            print(f"\n❌ Failed (code {e.returncode})")
            return {'success': False, 'duration': duration, 'error': str(e), 'records': 0, 'markets': 0}

    def _participants_window_for_season(
        self,
        season_type: str,
        season_start_date: datetime,
    ) -> tuple[datetime, datetime, bool]:
        """
        Return (window_start, window_end, use_resolution_anchor) for a season's
        participants partition. Mirrors _snapshot_origin_wallets_for_season.
        """
        if season_type == "standard":
            normalized_start = self._utc_day_start(season_start_date)
            window_end = normalized_start - timedelta(days=STANDARD_ORIGIN_SNAPSHOT_OFFSET_DAYS)
            window_start = window_end - timedelta(days=ORIGIN_LOOKBACK_DAYS_STANDARD)
            return window_start, window_end, True
        # Genesis: full configured genesis date range.
        window_start = datetime.combine(
            GENESIS_START_DATE, datetime.min.time(), tzinfo=timezone.utc
        )
        window_end = datetime.combine(
            GENESIS_END_DATE + timedelta(days=1),
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
        return window_start, window_end, False

    def refresh_participants_snapshot(self) -> Dict:
        """
        Refresh per-season partitions of the participants table. The legacy
        materialized-view refresh has been replaced by one TRUNCATE+INSERT
        per active season, driven by refresh_participants_for_season() in SQL.
        """
        print(f"\n{'='*70}")
        print("🚀 RUNNING: Participants Snapshot Refresh")
        print(f"{'='*70}")

        if self.dry_run:
            print("🔍 DRY RUN - Skipping")
            return {'success': True, 'skipped': True, 'records': 0}

        start_time = time.time()
        conn = self.manager.get_connection()
        cursor = None
        per_season_results: List[Dict[str, Any]] = []
        total_rows = 0
        try:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # Drop orphan partitions whose seasons row was deleted (e.g. after
            # a manual reset). Leaving them around is not just clutter — the
            # 1-event-↔-1-season guard inside refresh_participants_for_season
            # treats their rows as "event already belongs to another season"
            # and silently starves the live season's pool.
            cursor.execute(
                """
                SELECT c.relname AS partition_name,
                       SUBSTRING(c.relname FROM 'participants_season_(\\d+)')::BIGINT AS season_id
                FROM pg_inherits i
                JOIN pg_class    c ON c.oid = i.inhrelid
                WHERE i.inhparent = 'public.participants'::regclass
                  AND c.relname LIKE 'participants_season_%'
                """
            )
            existing_partitions = [dict(r) for r in cursor.fetchall()]
            for part in existing_partitions:
                pid = int(part["season_id"])
                cursor.execute("SELECT 1 FROM seasons WHERE id = %s", (pid,))
                if cursor.fetchone() is None:
                    cursor.execute("SELECT participants_drop_partition(%s)", (pid,))
                    print(f"   🗑️  dropped orphan partition for season_id={pid}")
            conn.commit()

            # All seasons that should have a current partition: every season
            # with a row. Closed standard seasons stay readable until the
            # operator explicitly drops their partition.
            cursor.execute(
                """
                SELECT id, type, start_date
                FROM seasons
                ORDER BY id ASC
                """
            )
            seasons = [dict(r) for r in cursor.fetchall()]

            for season in seasons:
                season_id = int(season["id"])
                season_type = str(season["type"])
                window_start, window_end, use_resolution_anchor = (
                    self._participants_window_for_season(season_type, season["start_date"])
                )
                cursor.execute(
                    "SELECT participants_ensure_partition(%s)",
                    (season_id,),
                )
                cursor.execute(
                    "SELECT refresh_participants_for_season(%s, %s, %s, %s) AS inserted",
                    (season_id, window_start, window_end, use_resolution_anchor),
                )
                inserted_row = cursor.fetchone() or {}
                inserted = int(inserted_row.get("inserted") or 0)
                total_rows += inserted
                per_season_results.append({
                    "season_id": season_id,
                    "season_type": season_type,
                    "rows": inserted,
                })
                print(
                    f"   🧬 season {season_id:>3} ({season_type}): "
                    f"{inserted:,} participant rows"
                )
                conn.commit()

            duration = time.time() - start_time
            print(
                f"\n✅ Completed ({duration:.1f}s) - {total_rows:,} rows across "
                f"{len(per_season_results)} season partition(s)"
            )
            return {
                'success': True,
                'duration': duration,
                'records': total_rows,
                'per_season': per_season_results,
                'refresh_mode': 'per_season_partition',
            }
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            duration = time.time() - start_time
            print(f"\n❌ Failed ({duration:.1f}s): {e}")
            return {'success': False, 'duration': duration, 'error': str(e), 'records': 0}
        finally:
            if cursor is not None:
                cursor.close()
            conn.close()

    def _make_mint_receipt_verifier(self) -> Any:
        """Build the object recovery uses to ask the chain whether a recorded
        ``tx_hash`` actually produced a mint. Tests override this to inject a
        fake without needing live RPC / private-key env. The returned object
        only needs ``.fetch_mint_receipt_status(tx_hash)`` returning the
        ``(status, token_id, asset_address)`` triple documented on
        :meth:`EvmClient.fetch_mint_receipt_status`.
        """
        from scripts.evm_service import EvmClient as _EvmClient
        return _EvmClient()

    def _recover_stale_processing(self) -> Dict[str, int]:
        """Heal claims left in PROCESSING by a crashed prior worker run.

        Three cases:

        * ``tx_hash IS NULL`` — EVM mint never went out, the row never
          touched the chain. Safe to flip back to ``QUEUED`` so the next
          pickup re-attempts it from scratch.

        * ``tx_hash IS NOT NULL`` and the COMPLETED flip lands cleanly —
          EVM mint already happened (the durable tx_hash write committed),
          only the final ``COMPLETED`` flip didn't land. Re-minting this
          would be a double-spend; finalize it to ``COMPLETED`` with the
          existing artifacts.

        * ``tx_hash IS NOT NULL`` but the COMPLETED flip violates
          ``ux_claims_season_collection_mint`` — a sibling claim already
          finalized with the same ``collection_mint_number``. With the
          current allocator (``MAX over PROCESSING ∪ COMPLETED``) this
          should be unreachable for fresh allocations, but it can still
          fire on legacy rows allocated under the old ``MAX over
          COMPLETED`` policy or on rows whose cmn was set by a now-
          removed BEFORE-INSERT trigger. Recovery resolves it by
          allocating the next free ``MAX(cmn) + 1`` over PROCESSING ∪
          COMPLETED for the season under ``pg_advisory_xact_lock`` and
          flipping with the new number. The on-chain NFT keeps its
          original number — the audit trail of the divergence lives in
          ``error_message`` and is surfaced by the admin UI as a
          divergence badge.

        All cases gate on ``updated_at < NOW() - INTERVAL 'N minutes'``
        so an in-flight claim from a sibling worker is never touched.
        """
        threshold_minutes = max(1, int(MINT_QUEUE_STALE_PROCESSING_MINUTES))

        # ── Branch 1: requeue stale PROCESSING rows that never went on-chain.
        # Bulk UPDATE is safe — no unique-index conflict possible because we
        # null out collection_mint_number in the same statement.
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE claims
                    SET    status                 = 'QUEUED',
                           collection_mint_number = NULL,
                           error_message          = '[auto-reset: stale PROCESSING with no tx_hash at '
                                                    || NOW()::text || ']',
                           updated_at             = NOW()
                    WHERE  status = 'PROCESSING'
                      AND  tx_hash IS NULL
                      AND  updated_at < NOW() - INTERVAL '{threshold_minutes} minutes'
                    """
                )
                requeued = cursor.rowcount or 0
            conn.commit()
        finally:
            conn.close()

        # ── Branch 2: heal stale PROCESSING rows that have a recorded tx_hash.
        # The hash is written by the cron worker's ``on_pre_broadcast`` hook
        # *before* ``send_raw_transaction``, so its mere presence does NOT
        # prove the tx ever reached the chain — it only proves we tried. We
        # MUST consult the RPC before deciding what to do:
        #
        #   * receipt status==1 → the mint succeeded (existing auto-complete
        #     flow, with renumber-on-cmn-conflict for legacy rows).
        #   * receipt status==0 → the mint reverted on-chain. No NFT exists.
        #     Mark FAILED and free the cmn so the next allocator can reuse it.
        #   * receipt absent but tx still in the mempool → leave PROCESSING
        #     and bump updated_at so we don't re-check on every cron tick.
        #   * receipt absent and tx not in the mempool → likely dropped or
        #     never reached the RPC. Wait at least
        #     ``MINT_QUEUE_DROPPED_TX_REQUEUE_HOURS`` (default 6h) before
        #     requeueing, since a low-tier safe-gas tx can legitimately sit
        #     in the mempool that long; only after the dropped-tx threshold
        #     is it safe to assume the broadcast genuinely went nowhere and
        #     a fresh attempt will not double-mint.
        auto_completed = 0
        renumbered = 0
        finalized_failed = 0
        # Hard ceiling so a pathological loop (e.g. constraint we don't
        # know how to recover from) can never spin forever.
        max_iterations = 500

        # Lazy: only stand up the EVM client if we actually have a row to
        # check. Recovery runs on every cron tick and we don't want to pay
        # the RPC handshake when there's no work to do.
        verifier: Optional[Any] = None
        dropped_tx_requeue_hours = max(
            1, _env_int("MINT_QUEUE_DROPPED_TX_REQUEUE_HOURS", 6),
        )

        for _ in range(max_iterations):
            # Pick the next stale-on-chain claim. Re-running this query
            # each iteration also picks up rows freshly aged past the
            # threshold during a long recovery pass.
            conn = self.manager.get_connection()
            picked: Optional[Dict[str, Any]] = None
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute(
                        f"""
                        SELECT id, season_id, collection_mint_number,
                               tx_hash, asset_address, tx_attempts, metadata_uri,
                               EXTRACT(EPOCH FROM (NOW() - updated_at))::bigint AS stale_seconds
                        FROM   claims
                        WHERE  status = 'PROCESSING'
                          AND  tx_hash IS NOT NULL
                          AND  updated_at < NOW() - INTERVAL '{threshold_minutes} minutes'
                        ORDER  BY id
                        LIMIT  1
                        """
                    )
                    picked = cursor.fetchone()
            finally:
                conn.close()

            if not picked:
                break

            claim_id = int(picked["id"])
            season_id = int(picked["season_id"])
            original_cmn = picked.get("collection_mint_number")
            tx_hash = str(picked.get("tx_hash") or "").strip()
            existing_asset_address = str(picked.get("asset_address") or "").strip() or None
            existing_metadata_uri = str(picked.get("metadata_uri") or "").strip() or None
            stale_seconds = int(picked.get("stale_seconds") or 0)
            # ``verified_token_id`` is populated by the success-branch receipt
            # lookup (or the historical-winner fallback) and consumed by the
            # post-flip hooks below to backfill ``claims.token_id`` — the
            # recovery flip itself doesn't touch that column.
            verified_token_id: Optional[int] = None

            if not tx_hash:
                # Should be unreachable given the WHERE clause; defensive skip.
                continue

            # Verify on-chain status before deciding the row's fate.
            if verifier is None:
                try:
                    verifier = self._make_mint_receipt_verifier()
                except Exception as init_exc:
                    print(
                        f"   ⚠️  Recovery: cannot init EvmClient for tx verification "
                        f"({type(init_exc).__name__}: {init_exc}); leaving stale rows for the next pass"
                    )
                    break

            try:
                onchain_status, fetched_token_id, verified_asset_address = (
                    verifier.fetch_mint_receipt_status(tx_hash)
                )
            except Exception as verify_exc:
                print(
                    f"   ⚠️  Recovery: claim {claim_id} receipt lookup failed "
                    f"({type(verify_exc).__name__}: {verify_exc}); leaving in PROCESSING"
                )
                # Bump updated_at so we back off until the next stale window.
                self._touch_claim_updated_at(claim_id)
                continue

            if isinstance(fetched_token_id, int):
                verified_token_id = fetched_token_id

            if onchain_status == "pending":
                print(
                    f"   ⏳ Recovery: claim {claim_id} tx {tx_hash[:10]}… still pending in mempool "
                    f"(stale {stale_seconds // 60}m); leaving in PROCESSING"
                )
                self._touch_claim_updated_at(claim_id)
                continue

            if onchain_status == "not_found":
                # Before deciding the latest tx is gone for good, walk the
                # full tx_attempts audit in reverse to handle the RBF race:
                # we sent a replacement, but the ORIGINAL tx mined first
                # (it could have been picked up from a private mempool /
                # MEV-Share / Flashbots). The latest hash returns not_found
                # because nonce N is now consumed by the original; any older
                # attempt for the same nonce, however, will return success.
                #
                # Without this fallback we'd requeue an already-on-chain
                # claim and double-mint when the regular pickup loop
                # re-broadcasts. With it, we finalize via the historical
                # winning hash and free up the row cleanly.
                attempts_audit = picked.get("tx_attempts") or []
                if isinstance(attempts_audit, list) and len(attempts_audit) > 1:
                    historical_winner: Optional[Tuple[str, str | None, Optional[int]]] = None
                    for prior in reversed(attempts_audit[:-1]):
                        prior_hash = str(prior.get("hash") or "").strip()
                        if not prior_hash or prior_hash == tx_hash:
                            continue
                        try:
                            prior_status, prior_token_id, prior_asset = (
                                verifier.fetch_mint_receipt_status(prior_hash)
                            )
                        except Exception:
                            continue
                        if prior_status == "success":
                            historical_winner = (
                                prior_hash,
                                prior_asset,
                                prior_token_id if isinstance(prior_token_id, int) else None,
                            )
                            break
                    if historical_winner is not None:
                        winning_hash, winning_asset, winning_token_id = historical_winner
                        if winning_token_id is not None:
                            verified_token_id = winning_token_id
                        print(
                            f"   🔁 Recovery: claim {claim_id} latest tx "
                            f"{tx_hash[:10]}… not_found, but historical attempt "
                            f"{winning_hash[:10]}… succeeded — finalizing on the older hash"
                        )
                        # Promote the historical winner back to the live
                        # tx_hash slot so /api/cards and explorer links
                        # resolve to the actually-mined tx.
                        conn_promote = self.manager.get_connection()
                        try:
                            with conn_promote.cursor() as cur_promote:
                                cur_promote.execute(
                                    """
                                    UPDATE claims
                                    SET    tx_hash       = %s,
                                           asset_address = COALESCE(asset_address, %s),
                                           updated_at    = NOW()
                                    WHERE  id = %s
                                      AND  status = 'PROCESSING'
                                    """,
                                    (winning_hash, winning_asset, claim_id),
                                )
                            conn_promote.commit()
                        finally:
                            conn_promote.close()
                        # Re-enter the success branch by overwriting locals.
                        tx_hash = winning_hash
                        existing_asset_address = (
                            existing_asset_address or winning_asset
                        )
                        asset_to_persist = existing_asset_address
                        # Fall through to the normal success-completion code
                        # path below (after the if/elif ladder for status).
                        onchain_status = "success"
                        # Continue down the function — handled by the
                        # success branch that follows this block.
                    else:
                        # No historical winner — original eviction logic.
                        if stale_seconds < dropped_tx_requeue_hours * 3600:
                            print(
                                f"   ⏳ Recovery: claim {claim_id} tx {tx_hash[:10]}… "
                                f"not on chain and not in mempool (stale "
                                f"{stale_seconds // 60}m, audit walked {len(attempts_audit)} "
                                f"attempts, none mined); will requeue after "
                                f"{dropped_tx_requeue_hours}h"
                            )
                            self._touch_claim_updated_at(claim_id)
                            continue
                        self._requeue_dropped_claim(claim_id, tx_hash, dropped_tx_requeue_hours)
                        requeued += 1
                        continue
                else:
                    # Single-attempt history (initial only): original behavior.
                    if stale_seconds < dropped_tx_requeue_hours * 3600:
                        print(
                            f"   ⏳ Recovery: claim {claim_id} tx {tx_hash[:10]}… not on chain "
                            f"and not in mempool (stale {stale_seconds // 60}m, "
                            f"will requeue after {dropped_tx_requeue_hours}h); leaving in PROCESSING"
                        )
                        self._touch_claim_updated_at(claim_id)
                        continue
                    self._requeue_dropped_claim(claim_id, tx_hash, dropped_tx_requeue_hours)
                    requeued += 1
                    continue

            if onchain_status == "reverted":
                self._fail_reverted_claim(claim_id, tx_hash)
                finalized_failed += 1
                continue

            # onchain_status == "success" — proceed to the existing finalize /
            # renumber flow, also backfilling asset_address if the pre-broadcast
            # hook only persisted tx_hash.
            asset_to_persist = existing_asset_address or verified_asset_address

            conn = self.manager.get_connection()
            try:
                with conn.cursor() as cursor:
                    # Try the simple flip first. SAVEPOINT lets us recover
                    # from the unique-violation without aborting the whole
                    # transaction.
                    cursor.execute("SAVEPOINT recover_flip")
                    try:
                        cursor.execute(
                            """
                            UPDATE claims
                            SET    status        = 'COMPLETED',
                                   asset_address = COALESCE(asset_address, %s),
                                   timestamp     = COALESCE(timestamp, NOW()),
                                   error_message = '[auto-completed: on-chain receipt confirmed at '
                                                   || NOW()::text || ']',
                                   updated_at    = NOW()
                            WHERE  id = %s
                              AND  status = 'PROCESSING'
                              AND  tx_hash IS NOT NULL
                            """,
                            (asset_to_persist, claim_id),
                        )
                        cursor.execute("RELEASE SAVEPOINT recover_flip")
                        flip_rowcount = cursor.rowcount or 0
                        if flip_rowcount == 0:
                            # Lost the row to a concurrent change (e.g. another
                            # process flipped it). Skip silently.
                            conn.commit()
                            continue
                        auto_completed += 1
                        conn.commit()
                        print(
                            f"   ✅ Recovery: claim {claim_id} auto-completed "
                            f"(season {season_id}, cmn={original_cmn}, tx={tx_hash[:10]}…)"
                        )
                        self._run_post_recovery_hooks(
                            claim_id=claim_id,
                            metadata_uri=existing_metadata_uri,
                            token_id=verified_token_id,
                        )
                        continue
                    except psycopg2.errors.UniqueViolation as conflict:
                        constraint = (
                            getattr(conflict.diag, "constraint_name", "") or ""
                        )
                        if constraint != "ux_claims_season_collection_mint":
                            # Different unique violation — not ours to fix.
                            cursor.execute("ROLLBACK TO SAVEPOINT recover_flip")
                            conn.commit()
                            print(
                                f"   ⚠️  Recovery: claim {claim_id} flip hit "
                                f"unexpected unique violation on '{constraint}' — leaving in PROCESSING"
                            )
                            break
                        cursor.execute("ROLLBACK TO SAVEPOINT recover_flip")

                    # Renumber path: another claim already owns
                    # ``original_cmn`` in COMPLETED. Allocate the next free
                    # number under the per-season advisory xact lock so we
                    # don't race against the regular allocator.
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(9283742, %s)",
                        (season_id,),
                    )
                    cursor.execute(
                        """
                        SELECT COALESCE(MAX(collection_mint_number), 0) + 1
                        FROM   claims
                        WHERE  season_id = %s
                          AND  status IN ('PROCESSING', 'COMPLETED')
                          AND  collection_mint_number IS NOT NULL
                        """,
                        (season_id,),
                    )
                    new_cmn = int(cursor.fetchone()[0])
                    cursor.execute(
                        """
                        UPDATE claims
                        SET    collection_mint_number = %s,
                               status                 = 'COMPLETED',
                               asset_address          = COALESCE(asset_address, %s),
                               timestamp              = COALESCE(timestamp, NOW()),
                               error_message          = '[auto-renumbered by recovery at ' || NOW()::text
                                                        || ': cmn ' || %s || ' -> ' || %s
                                                        || ' (on-chain metadata still references the original number)]',
                               updated_at             = NOW()
                        WHERE  id = %s
                          AND  status = 'PROCESSING'
                          AND  tx_hash IS NOT NULL
                        """,
                        (new_cmn, asset_to_persist, original_cmn, new_cmn, claim_id),
                    )
                    rowcount = cursor.rowcount or 0
                conn.commit()
                if rowcount > 0:
                    auto_completed += 1
                    renumbered += 1
                    print(
                        f"   🔁 Recovery: claim {claim_id} renumbered "
                        f"cmn {original_cmn} → {new_cmn} (season {season_id}, "
                        f"on-chain metadata unchanged)"
                    )
                    self._run_post_recovery_hooks(
                        claim_id=claim_id,
                        metadata_uri=existing_metadata_uri,
                        token_id=verified_token_id,
                    )
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                print(
                    f"   ⚠️  Recovery: claim {claim_id} renumber path failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                # Bail out of recovery rather than spinning on a broken row.
                break
            finally:
                conn.close()

        return {
            'requeued': requeued,
            'auto_completed': auto_completed,
            'renumbered': renumbered,
            'finalized_failed': finalized_failed,
        }

    def _run_post_recovery_hooks(
        self,
        *,
        claim_id: int,
        metadata_uri: Optional[str],
        token_id: Optional[int],
    ) -> None:
        """Replay the post-mint side-effects that the normal QUEUE pickup
        loop runs after a successful flip to COMPLETED, but which the
        recovery path doesn't normally execute:

        * Backfill ``claims.token_id`` from the on-chain receipt (recovery's
          simple-flip / renumber UPDATEs don't touch that column).
        * Reconstruct the ``polystars_card`` payload from the IPFS metadata
          we already pinned (``card_display_data`` block — see
          ``EvmClient._build_card_display_data_payload``) and call
          ``denormalize_card_onto_claim`` so ``/api/cards/{slug}`` resolves
          the recovered STAR.
        * Fire the Telegram "NEW CLAIM" announcement.

        All steps are best-effort: a failure here MUST NOT undo the
        recovery flip that already committed (the row is COMPLETED on
        chain and in the DB, side-effects are catch-up). On any error we
        log and return — the operator can replay manually if needed.
        """
        if token_id is not None:
            try:
                conn = self.manager.get_connection()
                try:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE claims
                            SET    token_id   = %s,
                                   updated_at = NOW()
                            WHERE  id = %s
                              AND  token_id IS NULL
                            """,
                            (int(token_id), claim_id),
                        )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as exc:
                print(
                    f"   ⚠️  Recovery hooks: claim {claim_id} token_id backfill failed: "
                    f"{type(exc).__name__}: {exc}"
                )

        if not metadata_uri:
            return

        try:
            from scripts.polystars_card_payload import (
                build_recovery_payload_from_ipfs,
                denormalize_card_onto_claim,
            )
        except Exception as exc:
            print(
                f"   ⚠️  Recovery hooks: claim {claim_id} payload helpers import failed: "
                f"{type(exc).__name__}: {exc}"
            )
            return

        payload = build_recovery_payload_from_ipfs(metadata_uri)
        if not payload:
            # Legacy mint with no card_display_data block, gateway down, or
            # malformed JSON — skip silently. The recovery flip is already
            # committed; missing card fields are non-blocking.
            return

        try:
            denormalize_card_onto_claim(
                self.manager, claim_id=claim_id, polystars_card=payload,
            )
        except Exception as exc:
            print(
                f"   ⚠️  Recovery hooks: claim {claim_id} denormalize failed: "
                f"{type(exc).__name__}: {exc}"
            )

        try:
            from scripts.telegram_notifier import notify_claim_minted
            notify_claim_minted(
                front_image_url=str(payload.get("front_image_url") or ""),
                season_type=payload.get("season_type"),
                collection_mint_number=payload.get("collection_mint_number"),
                season_capacity=payload.get("season_size"),
                card_url=str(payload.get("qr_payload") or ""),
                archetype=payload.get("archetype"),
            )
        except Exception as exc:
            print(
                f"   ⚠️  Recovery hooks: claim {claim_id} telegram notify failed: "
                f"{type(exc).__name__}: {exc}"
            )

    def _touch_claim_updated_at(self, claim_id: int) -> None:
        """Bump ``updated_at`` so the stale-PROCESSING timer resets without
        changing any other column. Used by the receipt-verification path to
        avoid re-querying the RPC on every cron tick for txs that are
        legitimately still pending."""
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE claims SET updated_at = NOW() WHERE id = %s",
                    (claim_id,),
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            conn.close()

    def _requeue_dropped_claim(
        self, claim_id: int, tx_hash: str, dropped_tx_requeue_hours: int,
    ) -> None:
        """Recover a row whose recorded tx_hash is neither on-chain nor in
        the mempool after the dropped-tx window. NULL out the hash and cmn
        so the regular allocator + pickup loop can re-attempt the mint
        cleanly. Pinata pins from the abandoned attempt are intentionally
        left dangling (small leak; safer than racing a possible late-mining
        tx that still references them)."""
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE claims
                    SET    status                 = 'QUEUED',
                           tx_hash                = NULL,
                           collection_mint_number = NULL,
                           error_message          = '[auto-requeue: tx ' || %s
                                                    || ' not on chain after '
                                                    || %s::text || 'h at '
                                                    || NOW()::text || ']',
                           updated_at             = NOW()
                    WHERE  id = %s
                      AND  status = 'PROCESSING'
                      AND  tx_hash = %s
                    """,
                    (tx_hash, dropped_tx_requeue_hours, claim_id, tx_hash),
                )
            conn.commit()
            print(
                f"   ♻️  Recovery: claim {claim_id} requeued — tx {tx_hash[:10]}… "
                f"not on chain after {dropped_tx_requeue_hours}h"
            )
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            print(
                f"   ⚠️  Recovery: claim {claim_id} requeue failed: "
                f"{type(exc).__name__}: {exc}"
            )
        finally:
            conn.close()

    def _fail_reverted_claim(self, claim_id: int, tx_hash: str) -> None:
        """Finalize a row whose on-chain mint reverted (receipt status==0).
        No NFT exists, so we mark FAILED and clear ``collection_mint_number``
        to free the slot for the next allocator. ``tx_hash`` is preserved on
        the row as audit trail of the failed attempt."""
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE claims
                    SET    status                 = 'FAILED',
                           collection_mint_number = NULL,
                           error_message          = '[on-chain revert: tx ' || %s
                                                    || ' status=0 at '
                                                    || NOW()::text || ']',
                           updated_at             = NOW()
                    WHERE  id = %s
                      AND  status = 'PROCESSING'
                      AND  tx_hash = %s
                    """,
                    (tx_hash, claim_id, tx_hash),
                )
            conn.commit()
            print(
                f"   ❌ Recovery: claim {claim_id} marked FAILED — on-chain revert "
                f"(tx {tx_hash[:10]}…)"
            )
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            print(
                f"   ⚠️  Recovery: claim {claim_id} fail-revert update failed: "
                f"{type(exc).__name__}: {exc}"
            )
        finally:
            conn.close()

    def _get_season_name_for_claim(self, cursor, season_id: int) -> str:
        cursor.execute(
            "SELECT type, season_number FROM seasons WHERE id = %s",
            (season_id,),
        )
        row = cursor.fetchone()
        if not row:
            return f"season-{season_id}"
        if isinstance(row, dict):
            return f"{row['type']}#{row['season_number']}"
        return f"{row[0]}#{row[1]}"

    def _replace_stuck_transactions(
        self,
        *,
        rapid_max_fee_wei: int,
        eth_usd: float,
    ) -> Dict[str, int]:
        """Bump PROCESSING claims whose latest tx has been pending in the
        mempool for longer than ``MINT_QUEUE_REPLACE_AFTER_MINUTES``.

        Runs after recovery and before the QUEUED pickup loop so a stuck
        nonce gets unblocked before the worker piles new mints behind it
        (which would all inherit the stuck state).

        Skip rules:
          * ``len(tx_attempts) >= MINT_QUEUE_MAX_RBF_ATTEMPTS`` → mark
            ``error_message='[stuck: max RBF attempts reached]'`` and stop
            (no Telegram alert per design — admin UI surfaces the badge).
          * Bumped fee × gas_limit > ceiling → same stuck path
            (``ReplacementCapped``).
          * ``ReplacementUnderpriced`` → log + same stuck path; the next
            run will see the (still-stuck) row but won't try again because
            the attempt cap takes precedence.
          * ``ReplacementOriginalAlreadyMined`` → no-op; recovery on the
            next tick will verify the previous hash and finalize.

        Picks oldest stuck nonce first because Ethereum includes by nonce —
        bumping the earliest stuck tx unblocks all later ones automatically.
        """
        from scripts.evm_service import (
            EvmClient,
            ReplacementCapped,
            ReplacementUnderpriced,
            ReplacementOriginalAlreadyMined,
            ReplacementInsufficientFunds,
        )

        threshold_minutes = max(1, int(MINT_QUEUE_REPLACE_AFTER_MINUTES))
        max_attempts = max(1, int(MINT_QUEUE_MAX_RBF_ATTEMPTS))
        # Per-tx fee ceiling in wei. The price gate is in USD per mint, so
        # we lift it to a wei ceiling here (5× by default).
        threshold_usd = _mint_queue_max_usd_per_mint() * MINT_QUEUE_RBF_FEE_CEILING_MULTIPLIER
        max_fee_wei_ceiling = (
            int((threshold_usd / max(eth_usd, 1e-9)) * 1e18)
            if eth_usd > 0 else None
        )

        replaced = 0
        capped = 0
        underpriced = 0
        original_mined = 0

        # Pull candidates: PROCESSING with non-empty tx_attempts older than
        # threshold. We re-query each iteration because replacing the oldest
        # stuck tx may surface another candidate that was hidden behind it
        # in the ordering.
        client: Optional[Any] = None
        for _safety_iter in range(50):  # sanity cap on per-tick replacements
            conn = self.manager.get_connection()
            picked: Optional[Dict[str, Any]] = None
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                    cursor.execute(
                        f"""
                        SELECT id, season_id, tx_hash, tx_attempts,
                               EXTRACT(EPOCH FROM (NOW() - updated_at))::bigint AS stale_seconds
                        FROM   claims
                        WHERE  status = 'PROCESSING'
                          AND  tx_hash IS NOT NULL
                          AND  jsonb_array_length(tx_attempts) > 0
                          AND  jsonb_array_length(tx_attempts) < %s
                          AND  updated_at < NOW() - INTERVAL '{threshold_minutes} minutes'
                          AND  (error_message IS NULL OR error_message NOT LIKE '%%[stuck:%%')
                        ORDER  BY updated_at ASC
                        LIMIT  1
                        """,
                        (max_attempts,),
                    )
                    picked = cursor.fetchone()
            finally:
                conn.close()

            if not picked:
                break

            claim_id = int(picked["id"])
            attempts = picked.get("tx_attempts") or []
            if not isinstance(attempts, list) or not attempts:
                # Defensive — WHERE filter already excludes empty arrays.
                continue
            last_attempt = attempts[-1]
            # Backfilled attempts have no fee data — we can't safely RBF
            # them because we don't know what fee to bump above. Mark stuck.
            if last_attempt.get("backfilled") or "max_fee_wei" not in last_attempt:
                self._mark_claim_stuck(
                    claim_id,
                    "[stuck: legacy attempt without fee metadata; manual remedy required]",
                )
                capped += 1
                continue
            if not last_attempt.get("recipient") or not last_attempt.get("metadata_uri"):
                # Same: can't rebuild calldata without these.
                self._mark_claim_stuck(
                    claim_id,
                    "[stuck: legacy attempt missing calldata args; manual remedy required]",
                )
                capped += 1
                continue

            # Lazy: only stand up the client if we actually have work.
            if client is None:
                try:
                    client = EvmClient()
                except Exception as init_exc:
                    print(
                        f"   ⚠️  RBF: cannot init EvmClient ({type(init_exc).__name__}: "
                        f"{init_exc}); skipping replacement pass"
                    )
                    break

            stale_minutes = int(picked.get("stale_seconds") or 0) // 60
            attempt_idx = len(attempts) + 1
            print(
                f"   ♻️  RBF: claim {claim_id} stuck {stale_minutes}m "
                f"(attempt #{attempt_idx}/{max_attempts}); bumping fees"
            )

            def _persist_replacement(attempt: Dict[str, Any], _cid: int = claim_id) -> None:
                # Same UPDATE shape as the initial pre_broadcast hook —
                # latest tx_hash + appended attempt + bumped updated_at.
                attempt_json = json.dumps(attempt)
                conn_pb = self.manager.get_connection()
                try:
                    with conn_pb.cursor() as cur_pb:
                        cur_pb.execute(
                            """
                            UPDATE claims
                            SET    tx_hash     = %s,
                                   tx_attempts = tx_attempts || %s::jsonb,
                                   updated_at  = NOW()
                            WHERE  id = %s
                            """,
                            (str(attempt["hash"]), attempt_json, _cid),
                        )
                    conn_pb.commit()
                finally:
                    conn_pb.close()

            try:
                client.replace_stuck_tx(
                    previous_attempt=last_attempt,
                    current_rapid_max_fee_wei=int(rapid_max_fee_wei),
                    max_fee_wei_ceiling=max_fee_wei_ceiling,
                    on_pre_broadcast=_persist_replacement,
                )
                replaced += 1
                print(f"   ✅ RBF: claim {claim_id} replacement broadcast")
            except ReplacementCapped as exc:
                self._mark_claim_stuck(
                    claim_id, f"[stuck: max RBF fee cap reached: {exc}]",
                )
                capped += 1
                print(f"   🛑 RBF: claim {claim_id} fee cap hit — marked stuck")
            except ReplacementUnderpriced as exc:
                # Could be transient — but we still mark stuck so the next
                # tick doesn't immediately retry. Operator can clear
                # error_message manually to re-enable.
                self._mark_claim_stuck(
                    claim_id, f"[stuck: replacement underpriced: {exc}]",
                )
                underpriced += 1
                print(f"   🛑 RBF: claim {claim_id} underpriced — marked stuck")
            except ReplacementOriginalAlreadyMined:
                # Original mined while we were preparing replacement.
                # Don't mark stuck — recovery on next tick will verify.
                original_mined += 1
                print(
                    f"   ℹ️  RBF: claim {claim_id} original already mined "
                    f"(nonce too low); recovery will finalize"
                )
            except ReplacementInsufficientFunds as exc:
                self._mark_claim_stuck(
                    claim_id, f"[stuck: insufficient funds for RBF: {exc}]",
                )
                capped += 1
                print(
                    f"   🛑 RBF: claim {claim_id} insufficient funds — marked stuck. "
                    "Refill the hot wallet to resume mints."
                )
                # Insufficient funds affects ALL pending replacements equally,
                # no point continuing this pass.
                break
            except Exception as exc:
                # Unknown error — log, mark stuck so we don't loop on it,
                # continue trying other claims.
                self._mark_claim_stuck(
                    claim_id, f"[stuck: RBF error: {type(exc).__name__}: {exc}]",
                )
                capped += 1
                print(
                    f"   ⚠️  RBF: claim {claim_id} unexpected error "
                    f"{type(exc).__name__}: {exc}"
                )

        # Also handle claims that have already hit the attempt cap but
        # haven't been marked yet (e.g. from a prior crashed worker).
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE claims
                    SET    error_message = '[stuck: max RBF attempts reached at '
                                           || NOW()::text || ']',
                           updated_at = NOW()
                    WHERE  status = 'PROCESSING'
                      AND  jsonb_array_length(tx_attempts) >= %s
                      AND  (error_message IS NULL OR error_message NOT LIKE '%%[stuck:%%')
                    """,
                    (max_attempts,),
                )
                cap_marked = cursor.rowcount or 0
            conn.commit()
        finally:
            conn.close()

        return {
            "replaced": replaced,
            "capped": capped + cap_marked,
            "underpriced": underpriced,
            "original_already_mined": original_mined,
        }

    def _mark_claim_stuck(self, claim_id: int, reason: str) -> None:
        """Set ``error_message`` on a stuck claim so admin UI can flag it,
        bump ``updated_at`` so the row drops out of the threshold window
        until the message is cleared. Status stays PROCESSING — these claims
        are NOT failed (the on-chain tx may still mine eventually); they
        just need human attention."""
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE claims
                    SET    error_message = %s,
                           updated_at    = NOW()
                    WHERE  id = %s
                    """,
                    (reason, claim_id),
                )
            conn.commit()
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            print(
                f"   ⚠️  RBF: claim {claim_id} mark-stuck failed: "
                f"{type(exc).__name__}: {exc}"
            )
        finally:
            conn.close()

    def process_mint_queue(self, max_claims: int = 100) -> Dict:
        """Process QUEUED claims into on-chain mints. Picks up to ``max_claims``
        claims in FIFO order, atomically transitions each QUEUED → PROCESSING,
        builds the card payload (front/back PNGs + Pinata upload), submits the
        EVM mint, persists on-chain artifacts as a durability barrier, then
        flips to COMPLETED. Failures land in FAILED with the error message.

        Concurrency safety:
          * ``pg_advisory_lock`` blocks overlapping invocations (e.g. an hourly
            cron tick firing while the prior batch is still running).
          * Per-claim ``FOR UPDATE SKIP LOCKED`` makes pickups race-free even if
            the advisory lock is bypassed.

        Crash recovery:
          * ``_recover_stale_processing`` heals rows left in PROCESSING by a
            crashed prior run before the new pickup loop starts. Rows with no
            ``tx_hash`` requeue; rows with a ``tx_hash`` auto-finalize to
            COMPLETED so we never re-mint an already-on-chain claim.
        """
        print(f"\n{'='*70}")
        print("🪙 RUNNING: Mint Queue Worker")
        print(f"{'='*70}")
        if self.dry_run:
            print("🔍 DRY RUN — skipping queue worker")
            return {'success': True, 'skipped': True, 'processed': 0}
        if not _env_bool("MINT_QUEUE_ENABLED", True):
            print("⏸️  MINT_QUEUE_ENABLED=0 — skipping queue worker")
            return {'success': True, 'skipped': True, 'processed': 0}

        # Acquire global advisory lock on a dedicated connection. Held until
        # the function returns, so concurrent workers find pg_try_advisory_lock
        # returning false and exit without touching the queue.
        lock_conn = self.manager.get_connection()
        try:
            with lock_conn.cursor() as lcur:
                lcur.execute(
                    "SELECT pg_try_advisory_lock(%s)",
                    (MINT_QUEUE_ADVISORY_LOCK_KEY,),
                )
                got_lock = bool(lcur.fetchone()[0])
        except Exception:
            lock_conn.close()
            raise
        if not got_lock:
            lock_conn.close()
            print(
                "⏸️  Mint queue worker skipped — another instance holds the "
                f"advisory lock (key={MINT_QUEUE_ADVISORY_LOCK_KEY})"
            )
            return {
                'success': True, 'skipped': True, 'processed': 0,
                'reason': 'concurrent_run',
            }

        try:
            # Heal stale PROCESSING rows from a crashed prior run BEFORE
            # picking up new work, so a newly-queued claim doesn't get stuck
            # behind a corpse blocking the queue.
            try:
                recovery = self._recover_stale_processing()
                if (
                    recovery['requeued']
                    or recovery['auto_completed']
                    or recovery.get('renumbered')
                    or recovery.get('finalized_failed')
                ):
                    print(
                        f"🔧 Recovery: requeued={recovery['requeued']} "
                        f"auto_completed={recovery['auto_completed']} "
                        f"renumbered={recovery.get('renumbered', 0)} "
                        f"finalized_failed={recovery.get('finalized_failed', 0)} "
                        f"(threshold={MINT_QUEUE_STALE_PROCESSING_MINUTES}min)"
                    )
            except Exception as recovery_exc:
                print(f"⚠️  Recovery pass raised (continuing): {recovery_exc}")

            # Lazy import: heavy deps (web3 + Playwright via cardgen) only loaded here.
            from web3 import Web3
            from scripts.evm_service import EvmClient
            from scripts.polystars_card_payload import (
                build_polystars_card_from_claim,
                denormalize_card_onto_claim,
                unpin_pinata_urls,
            )

            BLOCKCHAIN_ETHEREUM = "ethereum"
            start_time = time.time()
            processed = completed = failed = 0
            per_claim_results: List[Dict[str, Any]] = []

            from scripts.gas_tracker import (
                fetch_eth_mint_gas_tracker,
                GasTrackerSnapshot,
            )

            price_gate_threshold_usd = _mint_queue_max_usd_per_mint()
            price_gate_enabled = _env_bool("MINT_QUEUE_PRICE_GATE_ENABLED", True)
            price_gate_skipped = False
            price_gate_reason: Optional[str] = None
            print(
                f"💰 Price gate: enabled={price_gate_enabled} "
                f"max_usd_per_mint=${price_gate_threshold_usd:.4f}"
            )

            # RBF pass: bump any PROCESSING claims whose tx has been pending
            # in the mempool too long. Runs BEFORE the pickup loop so a stuck
            # nonce gets unblocked before the worker piles new mints behind
            # it (which would otherwise inherit the stuck state). Needs one
            # gas snapshot to know how much to bump above the previous fee.
            try:
                rbf_snap: GasTrackerSnapshot = fetch_eth_mint_gas_tracker()
                rbf_rapid_max_fee_wei = int(rbf_snap.rapid_gwei * 1e9)
                rbf_result = self._replace_stuck_transactions(
                    rapid_max_fee_wei=rbf_rapid_max_fee_wei,
                    eth_usd=rbf_snap.eth_usd,
                )
                if any(rbf_result.values()):
                    print(
                        f"♻️  RBF pass: replaced={rbf_result['replaced']} "
                        f"capped={rbf_result['capped']} "
                        f"underpriced={rbf_result['underpriced']} "
                        f"original_already_mined={rbf_result['original_already_mined']} "
                        f"(threshold={MINT_QUEUE_REPLACE_AFTER_MINUTES}min, "
                        f"max_attempts={MINT_QUEUE_MAX_RBF_ATTEMPTS})"
                    )
            except Exception as rbf_exc:
                # RBF is best-effort — a gas tracker outage or RPC flake
                # must not block the rest of the worker (recovery + new
                # claims still proceed; stuck rows will be retried next tick).
                print(f"⚠️  RBF pass raised (continuing): {rbf_exc}")

            # Tier the next mint pays. Stays None when the gate is disabled,
            # which preserves the original "let the node pick" behaviour.
            # The gate certifies affordability under the worst case (rapid),
            # but the actual broadcast goes out at the safe tier — the network
            # is cheap right now, no reason to overpay for inclusion.
            mint_gas_price_gwei: float | None = None
            for _ in range(max_claims):
                # Price gate — re-checked on every iteration so a successful mint
                # is followed by another fresh check before the next pickup.
                if price_gate_enabled:
                    try:
                        snap: GasTrackerSnapshot = fetch_eth_mint_gas_tracker()
                    except Exception as gate_exc:
                        price_gate_skipped = True
                        price_gate_reason = f"gas tracker unavailable: {gate_exc}"
                        print(
                            f"⏸️  Price gate: skipping batch — {price_gate_reason}"
                        )
                        break
                    rapid_usd = snap.rapid_usd
                    if rapid_usd > price_gate_threshold_usd:
                        price_gate_skipped = True
                        price_gate_reason = (
                            f"rapid mint cost ${rapid_usd:.4f} "
                            f"> threshold ${price_gate_threshold_usd:.4f} "
                            f"(rapid={snap.rapid_gwei:.3f} gwei, "
                            f"ETH=${snap.eth_usd:.2f}, "
                            f"gas_units={snap.gas_estimate})"
                        )
                        print(f"⏸️  Price gate: {price_gate_reason} — waiting for next cron tick")
                        break
                    mint_gas_price_gwei = snap.safe_gwei
                    print(
                        f"   ✅ Price gate: rapid mint cost ${rapid_usd:.4f} "
                        f"≤ ${price_gate_threshold_usd:.4f} "
                        f"(rapid={snap.rapid_gwei:.3f} gwei, ETH=${snap.eth_usd:.2f}); "
                        f"will broadcast at safe={snap.safe_gwei:.3f} gwei "
                        f"(~${snap.safe_usd:.4f})"
                    )

                # Atomic claim pickup: FOR UPDATE SKIP LOCKED + transition to PROCESSING.
                conn = self.manager.get_connection()
                row: Optional[Dict[str, Any]] = None
                try:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                        cursor.execute(
                            """
                            UPDATE claims
                            SET    status = 'PROCESSING', updated_at = NOW()
                            WHERE  id = (
                                SELECT id FROM claims
                                WHERE  status = 'QUEUED'
                                ORDER  BY created_at ASC, id ASC
                                FOR UPDATE SKIP LOCKED
                                LIMIT 1
                            )
                            RETURNING id, season_id, user_wallet, recipient_address,
                                      proxy_wallet, claim_type, collection_mint_number
                            """
                        )
                        row = cursor.fetchone()
                    conn.commit()
                finally:
                    conn.close()

                if not row:
                    break  # Queue is empty.
                processed += 1
                claim_id = int(row["id"])
                season_id = int(row["season_id"])
                pre_existing_mint_number = (
                    int(row["collection_mint_number"])
                    if row.get("collection_mint_number") is not None else None
                )
                print(f"   🪙 claim {claim_id} (season {season_id}, {row.get('claim_type')}): start")

                # Allocate collection_mint_number now (post-pickup, pre-card-
                # render). Reads MAX(...) over PROCESSING ∪ COMPLETED claims
                # (with non-null cmn) for this season, advances by 1,
                # persists onto the PROCESSING row.
                # ``pg_advisory_xact_lock(season_id)`` serializes allocators
                # at the season level even if the global advisory_lock above
                # were somehow bypassed. ``WHERE collection_mint_number IS NULL``
                # makes this idempotent for stuck-PROCESSING retries.
                #
                # Why MAX over PROCESSING ∪ COMPLETED (not COMPLETED only):
                # the prior policy had a blind spot — a claim sitting in
                # PROCESSING (rendered, sometimes already on-chain, but not
                # yet flipped to COMPLETED) was invisible to the next
                # allocator, so two claims could each get the same number
                # baked into their card PNG. Recovery later fixed the DB
                # row by renumbering, but the IPFS/on-chain PNG is immutable
                # — the visual divergence ("1/333" on two different cards)
                # could not be undone. By including PROCESSING (and FAILED
                # rows are excluded only because pre-mint failure NULLs
                # their cmn), the next number always reflects every slot
                # that has ever been baked onto a card image. The cost is
                # gaps in the sequence when a PROCESSING row is requeued
                # back to QUEUED and its cmn freed — accepted as cheaper
                # than a permanent on-chain visual conflict.
                conn = self.manager.get_connection()
                try:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                        cursor.execute(
                            "SELECT pg_advisory_xact_lock(9283742, %s)",
                            (season_id,),
                        )
                        cursor.execute(
                            """
                            UPDATE claims
                            SET    collection_mint_number = (
                                       SELECT COALESCE(MAX(collection_mint_number), 0) + 1
                                       FROM   claims
                                       WHERE  season_id = %s
                                         AND  status    IN ('PROCESSING', 'COMPLETED')
                                         AND  collection_mint_number IS NOT NULL
                                   ),
                                   updated_at = NOW()
                            WHERE  id = %s
                              AND  collection_mint_number IS NULL
                            RETURNING collection_mint_number
                            """,
                            (season_id, claim_id),
                        )
                        alloc_row = cursor.fetchone()
                    conn.commit()
                finally:
                    conn.close()

                if alloc_row and alloc_row.get("collection_mint_number") is not None:
                    collection_mint_number: Optional[int] = int(
                        alloc_row["collection_mint_number"]
                    )
                else:
                    # No new allocation happened — either the row already had
                    # a number from a prior PROCESSING attempt that crashed
                    # post-allocation, or the UPDATE matched nothing (very
                    # unlikely; would mean the row was deleted between
                    # pickup and allocation). Fall back to whatever was on
                    # the row at pickup time.
                    collection_mint_number = pre_existing_mint_number
                if collection_mint_number is not None:
                    print(f"   🔢 claim {claim_id}: collection_mint_number = {collection_mint_number}")

                polystars_card: Optional[Dict[str, Any]] = None
                # Once True, the EVM mint has been broadcast (or about to be —
                # we record the deterministic tx hash *before* send via the
                # pre_broadcast hook so even a crash mid-RPC leaves recovery
                # the information it needs). A subsequent exception MUST NOT
                # mark the row as FAILED or unpin Pinata assets, since the tx
                # may already be on-chain and immutably reference those pins.
                # Recovery owns the row from this point on; it verifies the
                # hash via eth_getTransactionReceipt and decides COMPLETED /
                # FAILED / requeue.
                on_chain_completed = False

                def _record_pre_broadcast_tx_hash(
                    attempt: Dict[str, Any], _claim_id: int = claim_id
                ) -> None:
                    """Persist the deterministic tx_hash AND the full attempt
                    record BEFORE send_raw_transaction. Closes the double-mint
                    window: after this commit lands, recovery sees ``tx_hash
                    IS NOT NULL`` and looks up the on-chain status instead of
                    requeueing.

                    Also persists ``metadata_uri`` here (rather than after the
                    receipt-wait, as before) so RBF can rebuild the same
                    calldata for a replacement bump without re-pinning to
                    Pinata. The metadata CID is committed-to the moment we
                    broadcast — there is no scenario where the receipt-wait
                    would invalidate it.

                    Appends the attempt dict to ``tx_attempts`` so the audit
                    trail starts from the very first send. RBF later appends
                    additional 'replacement' entries; recovery walks this
                    array in reverse if the live ``tx_hash`` returns
                    not_found (handles the race where an older attempt mines
                    even though we sent a replacement).
                    """
                    nonlocal on_chain_completed
                    tx_hash = str(attempt["hash"])
                    metadata_uri = str(attempt.get("metadata_uri") or "") or None
                    attempt_json = json.dumps(attempt)
                    conn_pb = self.manager.get_connection()
                    try:
                        with conn_pb.cursor() as cur_pb:
                            cur_pb.execute(
                                """
                                UPDATE claims
                                SET    tx_hash      = %s,
                                       mint_chain   = %s,
                                       metadata_uri = COALESCE(metadata_uri, %s),
                                       tx_attempts  = tx_attempts || %s::jsonb,
                                       updated_at   = NOW()
                                WHERE  id = %s
                                """,
                                (
                                    tx_hash,
                                    BLOCKCHAIN_ETHEREUM,
                                    metadata_uri,
                                    attempt_json,
                                    _claim_id,
                                ),
                            )
                        conn_pb.commit()
                    finally:
                        conn_pb.close()
                    on_chain_completed = True

                try:
                    # Recipient priority: ``claims.recipient_address`` (admin/UI
                    # may set it to a different EOA than the claimer), with
                    # fallback to ``user_wallet`` (the claimer EOA itself) for
                    # legacy rows that don't carry a recipient yet. Validation
                    # is intentionally inside the per-claim try so a row with
                    # bad data (e.g. seeded test value ``0xlooter``) lands in
                    # FAILED instead of crashing the whole batch.
                    raw_recipient = (
                        str(row.get("recipient_address") or "").strip()
                        or str(row.get("user_wallet") or "").strip()
                    )
                    try:
                        recipient_address = Web3.to_checksum_address(raw_recipient)
                    except Exception as exc:
                        raise ValueError(
                            f"Invalid recipient address on claim row: {raw_recipient!r}"
                        ) from exc

                    conn = self.manager.get_connection()
                    try:
                        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                            season_name = self._get_season_name_for_claim(cursor, season_id)
                    finally:
                        conn.close()

                    polystars_card = build_polystars_card_from_claim(
                        self.manager, claim_id=claim_id
                    )
                    winner_context = {
                        "assignment_type": (
                            "winner_self"
                            if str(row.get("claim_type") or "").lower() == "origin"
                            else "random_fallback"
                        ),
                        "winner_wallet_address": str(row.get("proxy_wallet") or ""),
                        "claimer_wallet_address": str(row.get("user_wallet") or ""),
                        "season_id": season_id,
                        "blockchain": BLOCKCHAIN_ETHEREUM,
                    }
                    client = EvmClient()
                    mint_result = client.mint_user_nft(
                        user_wallet_address=recipient_address,
                        season_name=season_name,
                        claim_id=claim_id,
                        collection_mint_number=collection_mint_number,
                        winner_context=winner_context,
                        polystars_card=polystars_card,
                        gas_price_gwei=mint_gas_price_gwei,
                        on_pre_broadcast=_record_pre_broadcast_tx_hash,
                    )
                    # ``on_chain_completed`` was already flipped True inside
                    # the pre_broadcast hook; reasserting here is a no-op but
                    # keeps the post-condition explicit at the call site.
                    on_chain_completed = True

                    # Durability barrier: persist on-chain artifacts immediately
                    # while status is still PROCESSING. Once this commit lands,
                    # the EVM tx is permanently recorded — a crash before the
                    # COMPLETED flip is recoverable by the next run's stale-
                    # PROCESSING pass (which auto-finalizes rows that already
                    # have a tx_hash, never re-minting them).
                    conn = self.manager.get_connection()
                    try:
                        with conn.cursor() as cursor:
                            cursor.execute(
                                """
                                UPDATE claims
                                SET    tx_hash       = %s,
                                       asset_address = %s,
                                       metadata_uri  = %s,
                                       mint_chain    = %s,
                                       timestamp     = NOW(),
                                       updated_at    = NOW()
                                WHERE  id = %s
                                """,
                                (
                                    mint_result.tx_hash,
                                    mint_result.asset_address,
                                    mint_result.metadata_uri,
                                    BLOCKCHAIN_ETHEREUM,
                                    claim_id,
                                ),
                            )
                        conn.commit()
                    finally:
                        conn.close()
                    # Denormalize the rendered card payload onto the claim so
                    # /api/cards/{slug} can serve the freshly minted STAR.
                    # A failure here must not undo the on-chain mint — log and continue.
                    try:
                        denormalize_card_onto_claim(
                            self.manager, claim_id=claim_id, polystars_card=polystars_card,
                        )
                    except Exception as denorm_exc:
                        print(f"   ⚠️  claim {claim_id}: denormalize failed: {denorm_exc}")
                    # Final flip to COMPLETED — separate commit from the
                    # tx_hash write so failure modes are unambiguous on
                    # recovery (PROCESSING + tx_hash = stuck post-mint).
                    conn = self.manager.get_connection()
                    try:
                        with conn.cursor() as cursor:
                            cursor.execute(
                                """
                                UPDATE claims
                                SET    status     = 'COMPLETED',
                                       updated_at = NOW()
                                WHERE  id = %s
                                """,
                                (claim_id,),
                            )
                        conn.commit()
                    finally:
                        conn.close()
                    completed += 1
                    per_claim_results.append({
                        "claim_id": claim_id, "status": "COMPLETED",
                        "tx_hash": mint_result.tx_hash,
                    })
                    print(f"   ✅ claim {claim_id}: minted {mint_result.tx_hash}")
                    try:
                        from scripts.telegram_notifier import notify_claim_minted
                        notify_claim_minted(
                            front_image_url=str(polystars_card.get("front_image_url") or "") if polystars_card else "",
                            season_type=polystars_card.get("season_type") if polystars_card else None,
                            collection_mint_number=collection_mint_number,
                            season_capacity=polystars_card.get("season_size") if polystars_card else None,
                            card_url=str(polystars_card.get("qr_payload") or "") if polystars_card else "",
                            archetype=polystars_card.get("archetype") if polystars_card else None,
                        )
                    except Exception as notify_exc:
                        print(f"   ⚠️  claim {claim_id}: telegram notify failed: {notify_exc}")
                except Exception as exc:
                    err_text = str(exc)[:500]
                    if on_chain_completed:
                        # EVM tx already broadcast — do NOT mark FAILED.
                        # Leave the row in PROCESSING so the recovery pass
                        # on the next run auto-finalizes it (idempotent
                        # finalization based on tx_hash presence).
                        print(
                            f"   ⚠️  claim {claim_id}: post-mint step failed but EVM tx "
                            f"already broadcast — leaving for recovery pass: {err_text}"
                        )
                        per_claim_results.append({
                            "claim_id": claim_id,
                            "status": "RECOVERY_PENDING",
                            "tx_hash": getattr(mint_result, "tx_hash", None),
                            "error": err_text,
                        })
                        continue
                    failed += 1
                    # Pre-mint failure: clear ``collection_mint_number`` so it
                    # doesn't burn a slot in the per-season mint sequence.
                    # The next claim picked up will compute MAX(...)+1 over
                    # COMPLETED only and reuse this number cleanly. We do this
                    # ONLY here (not in the on_chain_completed branch above):
                    # if the EVM tx already broadcast, the recovery pass will
                    # auto-finalize that row to COMPLETED and the number must
                    # stay (it matches what was put on the rendered card and
                    # in the metadata that the contract committed to).
                    conn = self.manager.get_connection()
                    try:
                        with conn.cursor() as cursor:
                            cursor.execute(
                                """
                                UPDATE claims
                                SET    status                 = 'FAILED',
                                       error_message          = %s,
                                       collection_mint_number = NULL,
                                       updated_at             = NOW()
                                WHERE  id = %s
                                """,
                                (err_text, claim_id),
                            )
                        conn.commit()
                    finally:
                        conn.close()
                    # Best-effort cleanup of any Pinata uploads from a half-built
                    # card. Only safe before the EVM mint — afterwards the pins
                    # are referenced by on-chain metadata and must NOT be removed.
                    if polystars_card:
                        try:
                            unpin_pinata_urls([
                                str(polystars_card.get("front_image_url") or ""),
                                str(polystars_card.get("back_image_url") or ""),
                            ])
                        except Exception:
                            pass
                    per_claim_results.append({
                        "claim_id": claim_id, "status": "FAILED", "error": err_text,
                    })
                    print(f"   ❌ claim {claim_id}: {err_text}")

            duration = time.time() - start_time
            print(
                f"\n✅ Mint queue worker done in {duration:.1f}s — "
                f"processed={processed} completed={completed} failed={failed}"
                + (f" (price gate: {price_gate_reason})" if price_gate_skipped else "")
            )
            return {
                'success': True,
                'duration': duration,
                'processed': processed,
                'completed': completed,
                'failed': failed,
                'per_claim': per_claim_results,
                'price_gate': {
                    'enabled': price_gate_enabled,
                    'threshold_usd': price_gate_threshold_usd,
                    'short_circuited': price_gate_skipped,
                    'reason': price_gate_reason,
                },
            }
        finally:
            try:
                with lock_conn.cursor() as lcur:
                    lcur.execute(
                        "SELECT pg_advisory_unlock(%s)",
                        (MINT_QUEUE_ADVISORY_LOCK_KEY,),
                    )
            except Exception:
                pass
            try:
                lock_conn.close()
            except Exception:
                pass

    def run_daily_pipeline(self, force: bool = False) -> Dict:
        """Run daily data pipeline"""
        
        print("\n" + "="*70)
        print("📊 DAILY DATA PIPELINE")
        print("="*70)
        print(f"Mode: {'DRY RUN' if self.dry_run else 'PRODUCTION'}")
        print(f"Database: {'Local PostgreSQL' if self.use_local_db else 'Supabase'}")
        print("Closed-time pipeline: ENABLED")

        # Update standard season lifecycle before daily ETL tasks.
        self.run_standard_season_update()
        
        # Check if another long-running operation is in progress
        lock = ProcessLock()
        if lock.is_locked():
            print("\n⚠️  Another operation (--catch-up or --historical) is running")
            print("   Daily pipeline will be skipped to avoid conflicts")
            print("   This is normal - cron will retry on next schedule")
            return {'success': False, 'error': 'Another operation in progress', 'skipped': True}
        
        # Check Genesis first
        if self.manager.needs_genesis_load() and not force:
            print("\n❌ Cannot run - Genesis data not loaded!")
            print("   Run with --historical flag first")
            return {'success': False, 'error': 'Genesis not loaded'}
        
        # Get loading dates
        dates = self.manager.get_loading_dates()
        events_date = dates['events_date']
        redemptions_date = dates['redemptions_date']
        
        # 🔍 AUTO-CHECK: Verify no missing dates before daily load
        print(f"\n🔍 Checking for missing data...")
        missing_dates = self.manager.get_missing_dates(
            start_from=GENESIS_END_DATE + timedelta(days=1),
            up_to=events_date - timedelta(days=1)  # Up to day before today's load
        )
        
        if missing_dates:
            print(f"\n⚠️  Found {len(missing_dates)} missing date(s) before today's load")
            print(f"   Missing: {', '.join(str(d) for d in missing_dates[:5])}")
            if len(missing_dates) > 5:
                print(f"   ... and {len(missing_dates) - 5} more")
            print(f"\n🔄 AUTO-CATCHUP: Running catch-up first to fill gaps...")
            print("="*70)
            
            # Cleanup current logging
            cleanup_logging()
            
            # Setup logging for catch-up
            setup_logging("catchup")
            
            # Run catch-up
            catchup_result = self.run_catch_up()
            
            # Cleanup catch-up logging
            cleanup_logging()
            
            # Resume logging for daily run
            setup_logging("daily")
            
            if not catchup_result['success']:
                print(f"\n❌ Catch-up failed - cannot proceed with daily load")
                return {'success': False, 'error': 'Catch-up failed', 'details': catchup_result}
            
            print(f"\n✅ Catch-up completed successfully")
            print(f"   Loaded {catchup_result.get('dates_loaded', 0)} date(s)")
            print(f"\n📅 Resuming daily pipeline...")
            print("="*70)
        else:
            print(f"✅ No missing dates - proceeding with today's load")
        
        print(f"\nLoading Dates:")
        print(f"  • Events: {events_date} ({EVENTS_LAG_DAYS} day{'s' if EVENTS_LAG_DAYS > 1 else ''} ago)")
        if self.use_closed_time_pipeline:
            print(
                f"  • Redemptions trigger: closed_time + "
                f"(DATA_LAG_DAYS - EVENTS_LAG_DAYS) = {RESOLUTION_READY_OFFSET_DAYS} days"
            )
        else:
            print(f"  • Redemptions: {redemptions_date} ({DATA_LAG_DAYS} days ago)")
        print("="*70)
        
        results = {}
        
        # STEP 1: Events (yesterday's data)
        print(f"\n📅 Events: Loading for {events_date}")
        if not force and self.manager.is_data_loaded_for_date(events_date, 'events'):
            print(f"⏭️  Already loaded")
            results['events'] = {'success': True, 'skipped': True}
        else:
            self.configure_for_date(events_date, is_genesis=False)
            results['events'] = self.run_script('events', target_date=events_date)
            if results['events']['success'] and not self.dry_run:
                self.manager.mark_data_loaded('events', events_date, 
                                            record_count=results['events']['records'],
                                            markets_count=results['events']['markets'],
                                            load_type='daily')
            elif not results['events']['success'] and not self.dry_run:
                error_msg = results['events'].get('error', 'Script execution failed')
                self.manager.mark_data_error('events', events_date, error_msg)

        # Sync queue from freshly ingested events and poll pending statuses.
        if not self.dry_run and results['events'].get('success'):
            synced = self.manager.sync_resolution_queue_for_event_date(
                load_date=events_date,
                min_volume=self.manager.get_volume_filter(is_genesis=False),
            )
            print(f"\n🧩 Resolution queue sync: {synced:,} row(s) upserted for {events_date}")

        # STEP 2-4: closed_time + (DATA_LAG_DAYS - EVENTS_LAG_DAYS) pipeline (feature-flagged)
        if self.use_closed_time_pipeline:
            if not self.dry_run:
                poll_stats = self.poll_pending_event_resolutions()
                results['resolution_polling'] = {'success': True, **poll_stats}
            else:
                print("\n🔍 DRY RUN: skipping resolution polling")
                results['resolution_polling'] = {'success': True, 'skipped': True}

            ready_event_ids = [] if self.dry_run else self.manager.get_ready_resolution_event_ids(
                as_of=datetime.utcnow(),
                limit=self.ready_batch_limit,
            )
            if ready_event_ids:
                print(
                    f"\n📅 Redemptions/Positions/Leaderboard: "
                    f"{len(ready_event_ids):,} ready event(s) by "
                    f"closed_time + {RESOLUTION_READY_OFFSET_DAYS}d"
                )
                downstream_run_id = None if self.dry_run else self.manager.start_downstream_run(
                    trigger_type='daily',
                    events_load_date=events_date,
                    ready_events_requested=len(ready_event_ids),
                )
                os.environ['POLYSTARS_EVENT_IDS'] = ",".join(ready_event_ids)
                try:
                    # Keep date config for compatibility with existing scripts, but event_ids are authoritative.
                    self.configure_for_date(events_date, is_genesis=False)
                    step_success = True
                    downstream_errors: List[str] = []
                    for script_key in ['redemptions', 'positions', 'leaderboard']:
                        # In closed-time mode this run can include multiple event dates,
                        # so use per-run table deltas (target_date=None) instead of date-scoped counters.
                        results[script_key] = self.run_script(script_key, target_date=None)
                        if not results[script_key].get('success'):
                            step_success = False
                            downstream_errors.append(
                                f"{script_key}: {results[script_key].get('error', 'script execution failed')}"
                            )

                    if step_success and not self.dry_run:
                        card_stats = self.generate_event_cards_for_event_ids(ready_event_ids)
                        results['event_cards'] = {'success': True, **card_stats}
                        print(
                            f"🧠 Event cards: requested={card_stats['requested']:,}, "
                            f"processed={card_stats['processed']:,}, success={card_stats['success']:,}, "
                            f"failed={card_stats['failed']:,}, "
                            f"tag_colors={card_stats.get('tag_colors_generated', 0):,}"
                        )
                        processed = self.manager.mark_resolution_events_processed(
                            ready_event_ids,
                            processed_run_id=downstream_run_id,
                        )
                        print(f"✅ Marked {processed:,} event(s) as processed in resolution queue")
                        participants_result = self.refresh_participants_snapshot()
                        results['participants'] = participants_result
                        participants_status = 'success' if participants_result.get('success') else 'error'
                        participants_rows = int(participants_result.get('records', 0) or 0)
                        participants_duration_ms = (
                            int(float(participants_result.get('duration', 0)) * 1000)
                            if participants_result.get('duration') is not None
                            else None
                        )
                        participants_error = (
                            None
                            if participants_result.get('success')
                            else participants_result.get('error', 'participants refresh failed')
                        )
                        self.manager.finish_downstream_run(
                            run_id=downstream_run_id,
                            status='success',
                            ready_events_processed=processed,
                            ready_events_failed=max(0, len(ready_event_ids) - processed),
                            redemptions_delta=int(results['redemptions'].get('records', 0)),
                            positions_delta=int(results['positions'].get('records', 0)),
                            leaderboard_delta=int(results['leaderboard'].get('records', 0)),
                            event_cards_requested=int(card_stats.get('requested', 0)),
                            event_cards_processed=int(card_stats.get('processed', 0)),
                            event_cards_success=int(card_stats.get('success', 0)),
                            event_cards_failed=int(card_stats.get('failed', 0)),
                            tag_colors_generated=int(card_stats.get('tag_colors_generated', 0)),
                            participants_status=participants_status,
                            participants_rows=participants_rows,
                            participants_duration_ms=participants_duration_ms,
                            participants_error=participants_error,
                            error_text=None,
                        )
                        self.manager.link_data_load_to_downstream_run(
                            events_date,
                            downstream_run_id,
                            processed,
                        )
                        if not participants_result.get('success'):
                            print("⚠️  Participants refresh failed after downstream success")
                    elif not self.dry_run:
                        error_text = "; ".join(downstream_errors) if downstream_errors else "downstream steps failed"
                        self.manager.mark_resolution_events_downstream_attempt(
                            ready_event_ids,
                            error_text=error_text,
                        )
                        if downstream_run_id is not None:
                            self.manager.finish_downstream_run(
                                run_id=downstream_run_id,
                                status='partial',
                                ready_events_processed=0,
                                ready_events_failed=len(ready_event_ids),
                                redemptions_delta=int(results.get('redemptions', {}).get('records', 0)),
                                positions_delta=int(results.get('positions', {}).get('records', 0)),
                                leaderboard_delta=int(results.get('leaderboard', {}).get('records', 0)),
                                event_cards_requested=0,
                                event_cards_processed=0,
                                event_cards_success=0,
                                event_cards_failed=0,
                                tag_colors_generated=0,
                                participants_status='skipped',
                                participants_rows=0,
                                participants_duration_ms=None,
                                participants_error=None,
                                error_text=error_text,
                            )
                        print("⚠️  Some downstream scripts failed; ready events remain unprocessed for retry")
                finally:
                    os.environ.pop('POLYSTARS_EVENT_IDS', None)
            else:
                print(
                    f"\n⏳ No events ready for downstream processing "
                    f"(rule: closed_time + {RESOLUTION_READY_OFFSET_DAYS} days)"
                )
                for script_key in ['redemptions', 'positions', 'leaderboard']:
                    results[script_key] = {'success': True, 'skipped': True, 'reason': 'no_ready_events'}
                results['event_cards'] = {'success': True, 'skipped': True, 'reason': 'no_ready_events'}
                results['participants'] = {'success': True, 'skipped': True, 'reason': 'no_ready_events'}
        else:
            # Legacy date-based pipeline
            print(f"\n📅 Redemptions/Positions/Leaderboard: Loading for {redemptions_date}")

            # ⚠️ ВАЖНО: Проверка на Genesis период (защита от дублей)
            if redemptions_date <= GENESIS_END_DATE:
                print(f"\n⏭️  Skipping redemptions/positions/leaderboard for {redemptions_date}")
                print(f"   Reason: Date is within Genesis period (already loaded)")
                print(f"   Genesis end: {GENESIS_END_DATE}")
                for script_key in ['redemptions', 'positions', 'leaderboard']:
                    results[script_key] = {'success': True, 'skipped': True, 'reason': 'genesis_period'}
            else:
                for script_key in ['redemptions', 'positions', 'leaderboard']:
                    if not force and self.manager.is_data_loaded_for_date(redemptions_date, script_key):
                        print(f"⏭️  {self.scripts[script_key]['name']}: Already loaded")
                        results[script_key] = {'success': True, 'skipped': True}
                    else:
                        # Configure for redemptions date
                        if script_key == 'redemptions':
                            self.configure_for_date(redemptions_date, is_genesis=False)

                        results[script_key] = self.run_script(script_key, target_date=redemptions_date)
                        if results[script_key]['success'] and not self.dry_run:
                            self.manager.mark_data_loaded(script_key, redemptions_date,
                                                        record_count=results[script_key]['records'],
                                                        load_type='daily')
                        elif not results[script_key]['success'] and not self.dry_run:
                            error_msg = results[script_key].get('error', 'Script execution failed')
                            self.manager.mark_data_error(script_key, redemptions_date, error_msg)
        
        # STEP 5: Auto-fix incomplete days (events loaded but redemptions missing)
        # Disabled for closed-time pipeline (event-based readiness is tracked in event_resolution_queue).
        if self.use_closed_time_pipeline:
            incomplete = []
            fixed_count = 0
            skipped_count = 0
        else:
            # This handles the first DATA_LAG_DAYS days after Genesis where daily pipeline skipped redemptions
            print(f"\n🔍 Checking for incomplete days...")
            incomplete = self.manager.get_incomplete_dates(
                start_from=GENESIS_END_DATE + timedelta(days=1),
                up_to=date.today() - timedelta(days=1)
            )

            fixed_count = 0
            skipped_count = 0
        
        if incomplete:
            print(f"\n⚠️  Found {len(incomplete)} day(s) with incomplete data!")
            print(f"   (Events loaded but redemptions/positions/leaderboard missing)")
            
            for incomplete_date, missing_types in incomplete:
                # Check if data is ready (DATA_LAG_DAYS lag for finalization)
                today = date.today()
                days_since = (today - incomplete_date).days
                
                # Only auto-fix if events ended >= DATA_LAG_DAYS ago
                if days_since < DATA_LAG_DAYS:
                    print(f"\n⏳ Skipping {incomplete_date} (ended {days_since} day(s) ago)")
                    print(f"   Missing: {', '.join(missing_types)}")
                    print(f"   Will be available on: {incomplete_date + timedelta(days=DATA_LAG_DAYS)}")
                    skipped_count += 1
                    continue
                
                print(f"\n📅 Auto-fixing: {incomplete_date} (ended {days_since} days ago)")
                print(f"   Missing: {', '.join(missing_types)}")
                
                # Configure for this date (no lag - historical data is finalized)
                self.configure_for_date(incomplete_date, is_genesis=False)
                
                for script_key in missing_types:
                    result = self.run_script(script_key, target_date=incomplete_date)
                    if result['success'] and not self.dry_run:
                        # For events, also pass markets_count
                        if script_key == 'events':
                            self.manager.mark_data_loaded(script_key, incomplete_date,
                                                        record_count=result['records'],
                                                        markets_count=result['markets'],
                                                        load_type='daily')
                            print(f"   ✅ {self.scripts[script_key]['name']} loaded ({result['records']:,} events, {result['markets']:,} markets)")
                        else:
                            self.manager.mark_data_loaded(script_key, incomplete_date,
                                                        record_count=result['records'],
                                                        load_type='daily')
                            print(f"   ✅ {self.scripts[script_key]['name']} loaded ({result['records']:,} records)")
                    else:
                        if not self.dry_run:
                            error_msg = result.get('error', 'Script execution failed')
                            self.manager.mark_data_error(script_key, incomplete_date, error_msg)
                        print(f"   ⚠️  {self.scripts[script_key]['name']} failed")
                
                fixed_count += 1
            
            if fixed_count == 0 and skipped_count > 0:
                print(f"\n   ⏳ {skipped_count} day(s) skipped (waiting for {DATA_LAG_DAYS}-day lag)")
        else:
            print("   ✅ No incomplete days found")
        
        # NOTE: The mint queue worker is intentionally NOT invoked here.
        # It runs on its own hourly cron (--process-mint-queue) so that
        # claims queued during the day get minted within ~1h instead of
        # waiting for the next 02:00 UTC daily pipeline run.

        # Summary
        print("\n" + "="*70)
        print("📊 PIPELINE SUMMARY")
        print("="*70)
        success_count = sum(1 for r in results.values() if r.get('success'))
        skipped_count_main = sum(1 for r in results.values() if r.get('skipped'))
        print(f"✅ Successful: {success_count}/{len(results)}")
        if skipped_count_main > 0:
            print(f"⏭️  Skipped: {skipped_count_main} (already loaded or Genesis period)")
        if incomplete:
                if fixed_count > 0:
                    print(f"🔧 Auto-fixed: {fixed_count} incomplete day(s)")
                if skipped_count > 0:
                    print(f"⏳ Waiting for lag: {skipped_count} day(s) (need {DATA_LAG_DAYS}-day finalization)")
        print("="*70)

        return {'success': all(r.get('success') for r in results.values()), 'results': results}
    
    def run_genesis_load(self) -> Dict:
        """Load Genesis (historical) data"""
        
        print("\n" + "="*70)
        print("🕰️  GENESIS DATA LOAD")
        print("="*70)
        print(f"Period: {GENESIS_START_DATE} to {GENESIS_END_DATE}")
        print(f"Filter: 100M volume")
        
        # Show testing mode warnings
        events_limit = self.manager.get_events_limit()
        max_volume = self.manager.get_max_volume_filter()
        if events_limit or max_volume:
            print(f"\n⚠️  TESTING MODE:")
            if events_limit:
                print(f"   • MAX_EVENTS: {events_limit} events (will load only first {events_limit})")
            if max_volume:
                print(f"   • MAX_VOLUME: ${max_volume:,} (excludes events over this)")
            print(f"   • Set to None in data_loading_manager.py for full load")
        
        if not self.manager.needs_genesis_load():
            print("\n✅ Genesis already loaded")
            return {'success': True, 'skipped': True}
        
        if self.dry_run:
            print("\n🔍 DRY RUN - Skipping")
            return {'success': True}
        
        # Acquire lock to prevent concurrent runs
        lock = ProcessLock()
        if not lock.acquire('historical'):
            return {'success': False, 'error': 'Could not acquire lock'}
        
        try:
            print("="*70)
            
            # Configure for Genesis
            self.configure_for_date(GENESIS_START_DATE, is_genesis=True)
            
            # Run all scripts for Genesis period
            results = {}
            
            for script_key in ['events', 'redemptions', 'positions', 'leaderboard']:
                results[script_key] = self.run_script(script_key)
                if results[script_key]['success']:
                    # For events, also pass markets_count
                    if script_key == 'events':
                        self.manager.mark_data_loaded(script_key, GENESIS_START_DATE,
                                                    record_count=results[script_key]['records'],
                                                    markets_count=results[script_key]['markets'],
                                                    load_type='genesis')
                    else:
                        self.manager.mark_data_loaded(script_key, GENESIS_START_DATE,
                                                    record_count=results[script_key]['records'],
                                                    load_type='genesis')
                else:
                    # Mark error for failed loads
                    error_msg = results[script_key].get('error', 'Script execution failed')
                    self.manager.mark_data_error(script_key, GENESIS_START_DATE, error_msg)

            # Generate cards for historical events only after full downstream success.
            all_steps_success = all(results[key].get('success') for key in ['events', 'redemptions', 'positions', 'leaderboard'])
            if all_steps_success and not self.dry_run:
                print("\n🧠 Generating event cards for historical load...")
                total_requested = 0
                total_processed = 0
                total_success = 0
                total_failed = 0
                batch_limit = max(1, self.event_cards_max_per_run)
                conn = self.manager.get_connection()
                try:
                    while True:
                        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                            target_event_ids = self._get_genesis_event_ids_missing_cards(cursor, limit=batch_limit)
                            conn.commit()
                        if not target_event_ids:
                            break

                        card_stats = self.generate_event_cards_for_event_ids(target_event_ids)
                        total_requested += int(card_stats.get("requested", 0))
                        total_processed += int(card_stats.get("processed", 0))
                        total_success += int(card_stats.get("success", 0))
                        total_failed += int(card_stats.get("failed", 0))

                        # Safety break in case generation returns zero progress.
                        if int(card_stats.get("processed", 0)) == 0:
                            break
                finally:
                    conn.close()

                results["event_cards"] = {
                    "success": True,
                    "requested": total_requested,
                    "processed": total_processed,
                    "success_count": total_success,
                    "failed": total_failed,
                }
                print(
                    f"🧠 Historical event cards: requested={total_requested:,}, "
                    f"processed={total_processed:,}, success={total_success:,}, failed={total_failed:,}"
                )
                participants_result = self.refresh_participants_snapshot()
                results["participants"] = participants_result
                if not participants_result.get('success'):
                    print("⚠️  Participants refresh failed after historical downstream success")
            
            # Summary
            print("\n" + "="*70)
            print("📊 GENESIS LOAD SUMMARY")
            print("="*70)
            success_count = sum(1 for r in results.values() if r.get('success'))
            print(f"✅ Successful: {success_count}/{len(results)}")
            print("="*70)
            
            return {'success': all(r.get('success') for r in results.values()), 'results': results}
        
        finally:
            # Always release lock
            lock.release()
    
    def _run_catch_up_iteration(self, iteration: int = 1) -> Dict:
        """
        Single iteration of catch-up (internal method)
        
        Args:
            iteration: Current iteration number (for logging)
            
        Returns:
            Dict with catch-up results
        """
        if iteration == 1:
            print("\n" + "="*70)
            print("🔄 CATCH-UP MODE: Loading missing data")
            print("="*70)
        else:
            print("\n" + "="*70)
            print(f"🔄 CATCH-UP ITERATION #{iteration}: Checking for new gaps")
            print("="*70)
        
        # Check Genesis first
        if self.manager.needs_genesis_load():
            print("\n❌ Genesis not loaded - run --historical first!")
            return {'success': False, 'error': 'Genesis not loaded', 'iteration': iteration}
        
        # Get target date for events (respecting lag)
        events_target_date = date.today() - timedelta(days=EVENTS_LAG_DAYS)
        
        print(f"\n📅 Lag configuration:")
        print(f"   Events lag: {EVENTS_LAG_DAYS} days (loading up to {events_target_date})")
        print(f"   Data lag: {DATA_LAG_DAYS} days (redemptions/positions/leaderboard)")
        
        # Find missing dates
        missing_dates = self.manager.get_missing_dates(
            start_from=GENESIS_END_DATE + timedelta(days=1),
            up_to=events_target_date
        )
        
        if not missing_dates:
            if iteration == 1:
                print("\n✅ No missing data - system is up to date!")
            else:
                print(f"\n✅ No new gaps found - all caught up!")
            return {'success': True, 'missing': 0, 'iteration': iteration}
        
        print(f"\nFound {len(missing_dates)} missing day(s):")
        print(f"  From: {missing_dates[0]}")
        print(f"  To: {missing_dates[-1]}")
        print(f"  Total: {len(missing_dates)} days")
        
        if self.dry_run:
            print("\n🔍 DRY RUN - Would load these dates")
            for d in missing_dates[:10]:
                print(f"  • {d}")
            if len(missing_dates) > 10:
                print(f"  • ... and {len(missing_dates) - 10} more")
            return {'success': True, 'missing': len(missing_dates), 'iteration': iteration}
        
        # Estimate time
        estimated_minutes = len(missing_dates) * 5  # ~5 min per day average
        print(f"\n⏱️  Estimated time: ~{estimated_minutes} minutes ({estimated_minutes/60:.1f} hours)")
        print(f"   (about 5 minutes per day)")
        
        print("\n" + "="*70)
        print("Starting catch-up process...")
        print("="*70)
        
        # Load each missing date
        results = {}
        start_time = time.time()
        participants_refresh_needed = False
        
        for i, missing_date in enumerate(missing_dates, 1):
            print(f"\n{'='*70}")
            print(f"📅 Day {i}/{len(missing_dates)}: Loading {missing_date}")
            print(f"{'='*70}")
            
            # STEP 1: Events for this date
            print(f"\n1️⃣ Events for {missing_date}")
            self.configure_for_date(missing_date, is_genesis=False)
            result_events = self.run_script('events', target_date=missing_date)
            
            if result_events['success']:
                self.manager.mark_data_loaded('events', missing_date,
                                            record_count=result_events['records'],
                                            markets_count=result_events['markets'],
                                            load_type='daily')
                print(f"✅ Events loaded for {missing_date} ({result_events['records']:,} events, {result_events['markets']:,} markets)")

                # Keep resolution queue complete during catch-up as well.
                if self.use_closed_time_pipeline:
                    synced = self.manager.sync_resolution_queue_for_event_date(
                        load_date=missing_date,
                        min_volume=self.manager.get_volume_filter(is_genesis=False),
                    )
                    print(f"🧩 Resolution queue sync: {synced:,} row(s) upserted for {missing_date}")
            else:
                error_msg = result_events.get('error', 'Script execution failed')
                self.manager.mark_data_error('events', missing_date, error_msg)
                print(f"❌ Events failed for {missing_date}")
                results[str(missing_date)] = {'success': False, 'step': 'events'}
                continue  # Skip other steps if events failed
            
            # STEP 2-4: Redemptions, Positions, Leaderboard
            if self.use_closed_time_pipeline:
                # Catch-up in closed-time mode must follow queue readiness exactly,
                # same principle as --run, but scoped to this missing events date.
                ready_for_day = self.manager.get_ready_resolution_event_ids_for_event_date(
                    load_date=missing_date,
                    as_of=datetime.utcnow(),
                )
                if not ready_for_day:
                    print(
                        f"\n⏳ No ready events for downstream on {missing_date} "
                        f"(rule: resolution_ready_at <= now)"
                    )
                else:
                    print(f"\n2️⃣ Redemptions/Positions/Leaderboard for {missing_date}")
                    print(f"   🎯 Ready events for this date: {len(ready_for_day):,}")
                    self.configure_for_date(missing_date, is_genesis=False)
                    downstream_success = True
                    downstream_counts = {'redemptions': 0, 'positions': 0, 'leaderboard': 0}
                    for script_key in ['redemptions', 'positions', 'leaderboard']:
                        result = self.run_script(script_key, target_date=None)
                        if result['success']:
                            downstream_counts[script_key] = int(result.get('records', 0))
                        else:
                            print(f"  ⚠️  {self.scripts[script_key]['name']} failed (continuing...)")
                            downstream_success = False
                    if downstream_success:
                        participants_refresh_needed = True
                        run_id = self.manager.start_downstream_run(
                            trigger_type='catch_up',
                            events_load_date=missing_date,
                            ready_events_requested=len(ready_for_day),
                        )
                        card_stats = self.generate_event_cards_for_event_ids(ready_for_day)
                        print(
                            f"🧠 Event cards ({missing_date}): requested={card_stats['requested']:,}, "
                            f"processed={card_stats['processed']:,}, success={card_stats['success']:,}, "
                            f"failed={card_stats['failed']:,}, "
                            f"tag_colors={card_stats.get('tag_colors_generated', 0):,}"
                        )
                        processed = self.manager.mark_resolution_events_processed(
                            ready_for_day,
                            processed_run_id=run_id,
                        )
                        self.manager.finish_downstream_run(
                            run_id=run_id,
                            status='success',
                            ready_events_processed=processed,
                            ready_events_failed=max(0, len(ready_for_day) - processed),
                            redemptions_delta=downstream_counts['redemptions'],
                            positions_delta=downstream_counts['positions'],
                            leaderboard_delta=downstream_counts['leaderboard'],
                            event_cards_requested=int(card_stats.get('requested', 0)),
                            event_cards_processed=int(card_stats.get('processed', 0)),
                            event_cards_success=int(card_stats.get('success', 0)),
                            event_cards_failed=int(card_stats.get('failed', 0)),
                            tag_colors_generated=int(card_stats.get('tag_colors_generated', 0)),
                            participants_status='skipped',
                            participants_rows=0,
                            participants_duration_ms=None,
                            participants_error=None,
                            error_text=None,
                        )
                        self.manager.link_data_load_to_downstream_run(
                            missing_date,
                            run_id,
                            processed,
                        )
                        print(f"✅ Marked {processed:,} ready event(s) as processed for {missing_date}")
                    else:
                        self.manager.mark_resolution_events_downstream_attempt(
                            ready_for_day,
                            error_text="catch_up downstream steps failed",
                        )
                        print("⚠️  Some downstream scripts failed; ready events remain unprocessed for retry")
            else:
                # Legacy date-based mode keeps lag guard.
                today = date.today()
                days_since_event = (today - missing_date).days
                if missing_date <= GENESIS_END_DATE:
                    print(f"\n⏭️  Skipping redemptions/positions/leaderboard for {missing_date}")
                    print(f"   Reason: Date is within Genesis period (already loaded)")
                elif days_since_event < DATA_LAG_DAYS:
                    print(f"\n⏳ Skipping redemptions/positions/leaderboard for {missing_date}")
                    print(f"   Reason: Event ended only {days_since_event} day(s) ago (need {DATA_LAG_DAYS} days)")
                    print(f"   Will be available on: {missing_date + timedelta(days=DATA_LAG_DAYS)}")
                else:
                    print(f"\n2️⃣ Redemptions/Positions/Leaderboard for {missing_date}")
                    print(f"   ℹ️  Event ended {days_since_event} days ago - data is finalized")
                    self.configure_for_date(missing_date, is_genesis=False)
                    for script_key in ['redemptions', 'positions', 'leaderboard']:
                        self.run_script(script_key, target_date=None)
            
            results[str(missing_date)] = {'success': True}
            
            # Progress
            elapsed = time.time() - start_time
            avg_time_per_day = elapsed / i
            remaining_days = len(missing_dates) - i
            estimated_remaining = remaining_days * avg_time_per_day
            
            print(f"\n📊 Progress: {i}/{len(missing_dates)} days")
            print(f"⏱️  Elapsed: {elapsed/60:.1f} min | Remaining: ~{estimated_remaining/60:.1f} min")

        if participants_refresh_needed and not self.dry_run:
            participants_result = self.refresh_participants_snapshot()
            results["participants_refresh"] = participants_result
            if not participants_result.get('success'):
                print("⚠️  Participants refresh failed after catch-up downstream success")
        
        # Summary
        total_time = time.time() - start_time
        print("\n" + "="*70)
        print(f"📊 CATCH-UP ITERATION #{iteration} SUMMARY")
        print("="*70)
        print(f"✅ Loaded: {len(missing_dates)} days")
        print(f"⏱️  Total time: {total_time/60:.1f} minutes ({total_time/3600:.1f} hours)")
        print(f"⚡ Average: {total_time/len(missing_dates)/60:.1f} minutes per day")
        print("="*70)
        
        return {'success': True, 'days_loaded': len(missing_dates), 'duration': total_time, 'iteration': iteration}
    
    def run_catch_up(self) -> Dict:
        """
        Automatically load all missing data with auto-retry
        
        This method runs catch-up iterations in a loop. After each successful
        iteration, it checks if new dates became available (e.g., if catch-up
        took >24h, a new day might have passed). If so, it automatically runs
        another iteration.
        
        Protection: Maximum 10 iterations to prevent infinite loops.
        
        Returns:
            Dict with catch-up results including total iterations
        """
        MAX_ITERATIONS = 10
        total_days_loaded = 0
        total_duration = 0
        iteration = 0
        
        # Acquire lock once for all iterations
        lock = ProcessLock()
        if not lock.acquire('catch-up'):
            return {'success': False, 'error': 'Could not acquire lock'}
        
        try:
            overall_start_time = time.time()
            
            for iteration in range(1, MAX_ITERATIONS + 1):
                # Run one catch-up iteration
                result = self._run_catch_up_iteration(iteration)
                
                if not result['success']:
                    # Error occurred
                    return result
                
                # Accumulate stats
                days_loaded = result.get('days_loaded', 0)
                total_days_loaded += days_loaded
                if 'duration' in result:
                    total_duration += result['duration']
                
                # No missing dates in this iteration
                if days_loaded == 0:
                    if iteration == 1:
                        # First check - no missing data at all
                        return result
                    else:
                        # Subsequent check - no new gaps, we're done
                        break
                
                # Check if there are MORE missing dates now
                # (could happen if this iteration took >24h and new day passed)
                print("\n" + "="*70)
                print(f"🔍 Checking for new gaps after iteration #{iteration}...")
                print("="*70)
                
                events_target_date = date.today() - timedelta(days=EVENTS_LAG_DAYS)
                new_missing_dates = self.manager.get_missing_dates(
                    start_from=GENESIS_END_DATE + timedelta(days=1),
                    up_to=events_target_date
                )
                
                if not new_missing_dates:
                    print("\n✅ No new gaps detected - catch-up complete!")
                    break
                
                # New gaps detected
                print(f"\n⚠️  New gap(s) detected: {len(new_missing_dates)} day(s)")
                print(f"   Range: {new_missing_dates[0]} to {new_missing_dates[-1]}")
                
                if iteration < MAX_ITERATIONS:
                    print(f"\n🔄 Automatically starting iteration #{iteration + 1}...")
                    print(f"   (This can happen if catch-up took >24h and new day passed)")
                    time.sleep(2)  # Brief pause before next iteration
                else:
                    print(f"\n⚠️  Maximum iterations ({MAX_ITERATIONS}) reached!")
                    print(f"   Stopping to prevent infinite loop")
                    print(f"   Remaining gaps: {len(new_missing_dates)} day(s)")
                    print(f"\n   👉 Run --catch-up again to continue")
                    break
        
            # Final summary
            overall_duration = time.time() - overall_start_time
            
            print("\n" + "="*70)
            print("🎉 CATCH-UP COMPLETE - FINAL SUMMARY")
            print("="*70)
            print(f"✅ Total days loaded: {total_days_loaded}")
            print(f"🔄 Iterations: {iteration}")
            print(f"⏱️  Total time: {overall_duration/60:.1f} minutes ({overall_duration/3600:.1f} hours)")
            if total_days_loaded > 0:
                print(f"⚡ Average: {overall_duration/total_days_loaded/60:.1f} minutes per day")
            print("="*70)
            
            return {
                'success': True,
                'total_days_loaded': total_days_loaded,
                'iterations': iteration,
                'total_duration': overall_duration
            }
        
        finally:
            # Always release lock
            lock.release()


def main():
    parser = argparse.ArgumentParser(
        description='Simplified Daily Data Scheduler',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check system state
  python scripts/daily_scheduler_simple.py --check
  
  # Run daily pipeline
  python scripts/daily_scheduler_simple.py --run
  
  # Load Genesis (historical data)
  python scripts/daily_scheduler_simple.py --historical
  
  # Load Genesis + auto catch-up (recommended for initial setup)
  python scripts/daily_scheduler_simple.py --historical --auto-catchup
  
  # Catch-up missing data
  python scripts/daily_scheduler_simple.py --catch-up

  # Run only season lifecycle update
  python scripts/daily_scheduler_simple.py --season-update
  
  # Force reload
  python scripts/daily_scheduler_simple.py --run --force
  
  # Dry run
  python scripts/daily_scheduler_simple.py --run --dry-run
  
  # Docker (with auto catch-up)
  docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --historical --auto-catchup
        """
    )
    
    parser.add_argument('--run', action='store_true', help='Run daily pipeline')
    parser.add_argument('--check', action='store_true', help='Check system state')
    parser.add_argument('--historical', action='store_true', help='Load Genesis data')
    parser.add_argument('--catch-up', action='store_true', help='Load all missing data automatically')
    parser.add_argument('--season-update', action='store_true', help='Run only seasons lifecycle logic')
    parser.add_argument('--auto-catchup', action='store_true', help='After --historical, automatically run --catch-up')
    parser.add_argument('--process-mint-queue', action='store_true', help='Run only the mint queue worker (process QUEUED claims into on-chain mints)')
    parser.add_argument('--mint-queue-batch-size', type=int, default=100, help='Max claims processed per --process-mint-queue invocation (default 100)')
    parser.add_argument('--force', action='store_true', help='Force reload')
    parser.add_argument('--dry-run', action='store_true', help='Dry run (test)')
    args = parser.parse_args()

    scheduler = SimplifiedScheduler(use_local_db=True, dry_run=args.dry_run)
    
    # Setup logging based on operation
    operation_name = None
    if args.run:
        operation_name = "daily"
    elif args.historical:
        operation_name = "historical"
    elif args.catch_up:
        operation_name = "catchup"
    elif args.season_update:
        operation_name = "season_update"
    
    if operation_name:
        setup_logging(operation_name)
    
    try:
        if args.check:
            scheduler.check_system_state()
        
        elif args.run:
            result = scheduler.run_daily_pipeline(force=args.force)
            sys.exit(0 if result['success'] else 1)
        
        elif args.historical:
            result = scheduler.run_genesis_load()
            
            # Auto-run catch-up after successful historical load
            if result['success'] and args.auto_catchup:
                print("\n" + "="*70)
                print("🔄 AUTO-CATCHUP: Historical load completed successfully")
                print("   Now running catch-up to load missing data...")
                print("="*70 + "\n")
                
                # Cleanup historical logging
                cleanup_logging()
                
                # Setup new logging for catchup
                setup_logging("catchup")
                
                # Run catch-up
                catchup_result = scheduler.run_catch_up()
                sys.exit(0 if catchup_result['success'] else 1)
            else:
                sys.exit(0 if result['success'] else 1)
        
        elif args.catch_up:
            result = scheduler.run_catch_up()
            sys.exit(0 if result['success'] else 1)

        elif args.season_update:
            scheduler.run_standard_season_update()
            # Always refresh per-season participants partitions so newly-created
            # seasons (and any drifted-stale ones) get a populated allocation
            # pool. Without this, ``mintable-count`` and the queue allocator
            # would see an empty partition for the just-rotated season.
            scheduler.refresh_participants_snapshot()
            print("\n✅ Season lifecycle update completed")
            sys.exit(0)

        elif args.process_mint_queue:
            result = scheduler.process_mint_queue(max_claims=args.mint_queue_batch_size)
            sys.exit(0 if result.get('success') else 1)

        else:
            print("Use --help for usage")
            print("\n🚀 Quick start (new server):")
            print("  1. Check: python scripts/daily_scheduler_simple.py --check")
            print("  2. Genesis + Catch-up: python scripts/daily_scheduler_simple.py --historical --auto-catchup")
            print("  3. Daily: python scripts/daily_scheduler_simple.py --run")
            print("  4. Seasons only: python scripts/daily_scheduler_simple.py --season-update")
            print("\n💡 Or manually:")
            print("  2a. Genesis only: python scripts/daily_scheduler_simple.py --historical")
            print("  2b. Catch-up: python scripts/daily_scheduler_simple.py --catch-up")
    finally:
        if operation_name:
            cleanup_logging()


if __name__ == '__main__':
    main()
