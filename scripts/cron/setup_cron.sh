#!/bin/bash
# ============================================================================
# Setup Cron for Daily Data Scheduler
# ============================================================================
# This script sets up a cron job to run the daily scheduler every day at 2 AM
# ============================================================================

echo "🕐 Setting up daily data scheduler cron job..."

# Path to project
PROJECT_DIR="/app"
PYTHON_BIN="/usr/local/bin/python"
SCHEDULER_SCRIPT="$PROJECT_DIR/scripts/daily_scheduler_simple.py"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/scheduler.log"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Cron schedule: Run every day at 2:00 AM UTC
# Format: minute hour day month day_of_week
CRON_SCHEDULE="0 2 * * *"

# Create cron job entry
CRON_JOB="$CRON_SCHEDULE cd $PROJECT_DIR && $PYTHON_BIN $SCHEDULER_SCRIPT --run >> $LOG_FILE 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "$SCHEDULER_SCRIPT"; then
    echo "⚠️  Cron job already exists. Removing old one..."
    crontab -l | grep -v "$SCHEDULER_SCRIPT" | crontab -
fi

# Add new cron job
(crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -

echo "✅ Cron job added successfully!"
echo ""
echo "Schedule: Every day at 2:00 AM UTC"
echo "Log file: $LOG_FILE"
echo ""
echo "Current crontab:"
crontab -l
echo ""
echo "To view logs:"
echo "  tail -f $LOG_FILE"
echo ""
echo "To test manually:"
echo "  python $SCHEDULER_SCRIPT --run"
