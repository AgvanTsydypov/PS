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

# Check system status
echo "📅 Checking system status..."
python /app/scripts/data_loading_manager.py --status || echo "  Could not get status"

# Start cron
echo ""
echo "✅ Scheduler service initialized successfully!"
echo "🕐 Cron schedule: Daily at 2:00 AM UTC"
echo "📝 Logs: /app/logs/scheduler.log"
echo ""
echo "To run manually:"
echo "  docker exec polystars_scheduler python /app/scripts/daily_scheduler_simple.py --run"
echo ""
echo "======================================"

# Execute CMD
exec "$@"
