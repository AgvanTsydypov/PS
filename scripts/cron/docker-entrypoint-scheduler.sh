#!/bin/bash
# ============================================================================
# Docker Entrypoint for Scheduler Service
# ============================================================================
# Initializes the scheduler container and starts cron
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
# Cron doesn't inherit environment variables, so we need to add them explicitly
echo "🔧 Setting up cron environment..."
printenv | grep -E '^(DB_|LOCAL_DB_|POSTGRES_)' | sed 's/^\(.*\)$/export \1/g' > /app/cron_env.sh
chmod +x /app/cron_env.sh

# Setup cron job with current environment
echo "🕐 Setting up cron job..."

# Create cron job (inline to avoid line ending issues)
CRON_SCHEDULE="0 2 * * *"
PROJECT_DIR="/app"
PYTHON_BIN="/usr/local/bin/python"
SCHEDULER_SCRIPT="$PROJECT_DIR/scripts/daily_scheduler_simple.py"
LOG_FILE="$PROJECT_DIR/logs/scheduler.log"

# Remove old cron job if exists
crontab -l 2>/dev/null | grep -v "$SCHEDULER_SCRIPT" | crontab - 2>/dev/null || true

# Add new cron job
(crontab -l 2>/dev/null; echo "$CRON_SCHEDULE . /app/cron_env.sh 2>/dev/null; cd $PROJECT_DIR && $PYTHON_BIN $SCHEDULER_SCRIPT --run >> $LOG_FILE 2>&1") | crontab -

echo "✅ Cron job added successfully!"
echo "   Schedule: $CRON_SCHEDULE (Every day at 00:05 AM UTC)"
crontab -l

# Check system status
echo "📅 Checking system status..."
python /app/scripts/data_loading_manager.py --status || echo "  Could not get status"

# Start cron
echo ""
echo "✅ Scheduler service initialized successfully!"
echo "🕐 Cron schedule: Daily at 00:05 UTC"
echo "📝 Logs: /app/logs/scheduler.log"
echo ""
echo "To run manually:"
echo "  docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --run"
echo ""
echo "======================================"

# Execute CMD
exec "$@"
