#!/bin/bash
# ============================================================================
# Docker Entrypoint for Scheduler Service
# ============================================================================
# Initializes the scheduler container and starts cron
# ============================================================================

set -e

echo "🚀 Starting PolyStars Scheduler Service"
echo "======================================"

# Wait for PostgreSQL to be ready
echo "⏳ Waiting for PostgreSQL..."
until pg_isready -h postgres -U postgres; do
    echo "  PostgreSQL not ready, waiting..."
    sleep 2
done
echo "✅ PostgreSQL is ready!"

# Create tracking tables if they don't exist
echo "📊 Ensuring tracking tables exist..."
if [ -f "/app/sql/schemas/create_simple_tracking.sql" ]; then
    PGPASSWORD=$LOCAL_DB_PASSWORD psql \
        -h $LOCAL_DB_HOST \
        -U $LOCAL_DB_USER \
        -d $LOCAL_DB_NAME \
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
