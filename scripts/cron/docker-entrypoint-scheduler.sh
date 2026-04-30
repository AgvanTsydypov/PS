#!/bin/bash
# ============================================================================
# Docker Entrypoint for Scheduler Service
# ============================================================================
# Initializes the scheduler container and starts cron
# 
# LOGGING:
# - Logs are duplicated to BOTH files AND stdout using 'tee'
# - View in real-time: docker logs -f polystars_scheduler
# - File logs: /app/logs/scheduler.log and /app/logs/cleanup.log
# - Python scripts output (with PYTHONUNBUFFERED=1) is visible in docker logs
# ============================================================================

set -e

echo "🚀 Starting PolyStars Scheduler Service"
echo "======================================"

# Wait for PostgreSQL to be ready (using environment variables)
echo "⏳ Waiting for PostgreSQL..."
echo "   Connecting to: ${DB_HOST:-${LOCAL_DB_HOST}}:${DB_PORT:-${LOCAL_DB_PORT}}"
MAX_TRIES=30
COUNTER=0
until PGPASSWORD="${DB_PASSWORD:-${LOCAL_DB_PASSWORD}}" pg_isready \
    -h "${DB_HOST:-${LOCAL_DB_HOST}}" \
    -p "${DB_PORT:-${LOCAL_DB_PORT}}" \
    -U "${DB_USER:-${LOCAL_DB_USER}}" > /dev/null 2>&1; do
    COUNTER=$((COUNTER+1))
    if [ $COUNTER -ge $MAX_TRIES ]; then
        echo "❌ PostgreSQL not ready after $MAX_TRIES attempts"
        echo "   Skipping database check and starting scheduler anyway..."
        break
    fi
    echo "  PostgreSQL not ready, waiting... (attempt $COUNTER/$MAX_TRIES)"
    sleep 2
done

if [ $COUNTER -lt $MAX_TRIES ]; then
    echo "✅ PostgreSQL is ready!"
else
    echo "⚠️  Starting without database connection verification"
fi

# Create tracking tables if they don't exist
echo "📊 Ensuring tracking tables exist..."
if [ -f "/app/sql/schemas/create_simple_tracking.sql" ]; then
    PGPASSWORD="${DB_PASSWORD:-${LOCAL_DB_PASSWORD}}" psql \
        -h "${DB_HOST:-${LOCAL_DB_HOST}}" \
        -p "${DB_PORT:-${LOCAL_DB_PORT}}" \
        -U "${DB_USER:-${LOCAL_DB_USER}}" \
        -d "${DB_NAME:-${LOCAL_DB_NAME}}" \
        -f /app/sql/schemas/create_simple_tracking.sql \
        2>/dev/null || echo "  Tables already exist or error occurred"
fi

# Export environment variables for cron
# Cron doesn't inherit environment variables, so we need to add them explicitly.
# Keep all UPPER_SNAKE_CASE vars to avoid missing new flags from .env/.env.prod.
echo "🔧 Setting up cron environment..."
printenv | grep -E '^[A-Z_][A-Z0-9_]*=' | sed 's/^\(.*\)$/export \1/g' > /app/cron_env.sh
chmod +x /app/cron_env.sh

# Setup cron jobs with current environment
echo "🕐 Setting up cron jobs..."

PROJECT_DIR="/app"
PYTHON_BIN="/usr/local/bin/python"

# Job 1: Daily data pipeline (2:00 AM UTC)
DAILY_SCHEDULE="0 2 * * *"
SCHEDULER_SCRIPT="$PROJECT_DIR/scripts/daily_scheduler_simple.py"
SCHEDULER_LOG="$PROJECT_DIR/logs/scheduler.log"

# Job 2: Preflight low-volume queue cleanup (1:30 AM UTC)
PREFLIGHT_SCHEDULE="30 1 * * *"
PREFLIGHT_SCRIPT="$PROJECT_DIR/scripts/db/print_low_volume_events.py"
PREFLIGHT_LOG="$PROJECT_DIR/logs/preflight_low_volume.log"

# Job 3: Seasons lifecycle update (00:00 AM UTC)
SEASON_UPDATE_SCHEDULE="0 0 * * *"
SEASON_UPDATE_LOG="$PROJECT_DIR/logs/season_update.log"

# Job 4: Weekly log cleanup (3:00 AM UTC every Sunday)
CLEANUP_SCHEDULE="0 3 * * 0"
CLEANUP_SCRIPT="$PROJECT_DIR/scripts/utils/cleanup_old_logs.py"
CLEANUP_LOG="$PROJECT_DIR/logs/cleanup.log"

# Job 5: Mint queue worker (hourly, on the hour). Decoupled from the
# daily pipeline so claims queued mid-day get minted within ~1h.
MINT_QUEUE_SCHEDULE="0 * * * *"
MINT_QUEUE_BATCH_SIZE="${MINT_QUEUE_HOURLY_BATCH_SIZE:-100}"
MINT_QUEUE_LOG="$PROJECT_DIR/logs/mint_queue.log"

# Clear existing crontab
crontab -r 2>/dev/null || true

# Add both cron jobs
# Using tee to duplicate logs: both to file AND to stdout (visible in docker logs)
(
  echo "# Daily data pipeline (2:00 AM UTC)"
  echo "$DAILY_SCHEDULE . /app/cron_env.sh 2>/dev/null; cd $PROJECT_DIR && $PYTHON_BIN $SCHEDULER_SCRIPT --run 2>&1 | tee -a $SCHEDULER_LOG"
  echo ""
  echo "# Preflight low-volume queue cleanup (1:30 AM UTC)"
  echo "$PREFLIGHT_SCHEDULE . /app/cron_env.sh 2>/dev/null; cd $PROJECT_DIR && $PYTHON_BIN $PREFLIGHT_SCRIPT --threshold 5000000 --queue-ready-within-minutes 30 --delete-matched 2>&1 | tee -a $PREFLIGHT_LOG"
  echo ""
  echo "# Season lifecycle update (00:00 AM UTC)"
  echo "$SEASON_UPDATE_SCHEDULE . /app/cron_env.sh 2>/dev/null; cd $PROJECT_DIR && $PYTHON_BIN $SCHEDULER_SCRIPT --season-update 2>&1 | tee -a $SEASON_UPDATE_LOG"
  echo ""
  echo "# Weekly log cleanup - keep 14 days (3:00 AM UTC every Sunday)"
  echo "$CLEANUP_SCHEDULE . /app/cron_env.sh 2>/dev/null; cd $PROJECT_DIR && $PYTHON_BIN $CLEANUP_SCRIPT --keep-days 14 2>&1 | tee -a $CLEANUP_LOG"
  echo ""
  echo "# Mint queue worker (every hour at :00)"
  echo "$MINT_QUEUE_SCHEDULE . /app/cron_env.sh 2>/dev/null; cd $PROJECT_DIR && $PYTHON_BIN $SCHEDULER_SCRIPT --process-mint-queue --mint-queue-batch-size $MINT_QUEUE_BATCH_SIZE 2>&1 | tee -a $MINT_QUEUE_LOG"
) | crontab -

echo "✅ Cron jobs added successfully!"
echo "   Season update: $SEASON_UPDATE_SCHEDULE (00:00 AM UTC)"
echo "   Preflight low-volume cleanup: $PREFLIGHT_SCHEDULE (1:30 AM UTC)"
echo "   Daily pipeline: $DAILY_SCHEDULE (2:00 AM UTC)"
echo "   Weekly cleanup: $CLEANUP_SCHEDULE (3:00 AM UTC every Sunday)"
echo "   Mint queue worker: $MINT_QUEUE_SCHEDULE (every hour, batch=$MINT_QUEUE_BATCH_SIZE)"
echo ""
crontab -l

# Check system status
echo "📅 Checking system status..."
python /app/scripts/data_loading_manager.py --status || echo "  Could not get status"

# Start cron
echo ""
echo "✅ Scheduler service initialized successfully!"
echo ""
echo "📅 Scheduled Tasks:"
echo "   • Season update: 00:00 AM UTC"
echo "   • Preflight low-volume cleanup: 01:30 AM UTC"
echo "   • Daily pipeline: 2:00 AM UTC"
echo "   • Log cleanup: 3:00 AM UTC (Sundays)"
echo "   • Mint queue worker: every hour at :00"
echo ""
echo "📝 Logs:"
echo "   • Season update: /app/logs/season_update.log"
echo "   • Preflight cleanup: /app/logs/preflight_low_volume.log"
echo "   • Pipeline: /app/logs/scheduler.log"
echo "   • Cleanup: /app/logs/cleanup.log"
echo "   • Mint queue: /app/logs/mint_queue.log"
echo "   • Docker logs: docker logs -f polystars_scheduler (real-time)"
echo ""
echo "Manual commands:"
echo "  # Run daily pipeline"
echo "  docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --run"
echo ""
echo "  # Run log cleanup"
echo "  docker exec polystars_scheduler python /app/scripts/utils/cleanup_old_logs.py"
echo ""
echo "======================================"

# Execute CMD
exec "$@"
