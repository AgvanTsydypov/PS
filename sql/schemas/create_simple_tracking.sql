-- ============================================================================
-- SIMPLIFIED DATA LOADING TRACKING (CLOSED_TIME PIPELINE)
-- ============================================================================

\echo '📊 Creating simplified tracking (closed_time)...'

CREATE TABLE IF NOT EXISTS data_loads (
    id SERIAL PRIMARY KEY,
    load_date DATE NOT NULL UNIQUE,
    events_loaded BOOLEAN DEFAULT FALSE,
    events_loaded_at TIMESTAMPTZ,
    events_count INTEGER DEFAULT 0,
    markets_count INTEGER DEFAULT 0,
    load_type VARCHAR(20) DEFAULT 'daily' CHECK (load_type IN ('genesis', 'daily')),
    events_error TEXT,
    last_downstream_run_id BIGINT,
    downstream_last_run_at TIMESTAMPTZ,
    downstream_ready_events_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE data_loads ADD COLUMN IF NOT EXISTS markets_count INTEGER DEFAULT 0;
ALTER TABLE data_loads ADD COLUMN IF NOT EXISTS events_error TEXT;
ALTER TABLE data_loads ADD COLUMN IF NOT EXISTS last_downstream_run_id BIGINT;
ALTER TABLE data_loads ADD COLUMN IF NOT EXISTS downstream_last_run_at TIMESTAMPTZ;
ALTER TABLE data_loads ADD COLUMN IF NOT EXISTS downstream_ready_events_count INTEGER NOT NULL DEFAULT 0;

DROP VIEW IF EXISTS recent_loads;
DROP VIEW IF EXISTS genesis_status;
DROP VIEW IF EXISTS daily_loads_summary;
DROP VIEW IF EXISTS recent_downstream_runs;

ALTER TABLE data_loads DROP COLUMN IF EXISTS redemptions_loaded;
ALTER TABLE data_loads DROP COLUMN IF EXISTS positions_loaded;
ALTER TABLE data_loads DROP COLUMN IF EXISTS leaderboard_loaded;
ALTER TABLE data_loads DROP COLUMN IF EXISTS redemptions_loaded_at;
ALTER TABLE data_loads DROP COLUMN IF EXISTS positions_loaded_at;
ALTER TABLE data_loads DROP COLUMN IF EXISTS leaderboard_loaded_at;
ALTER TABLE data_loads DROP COLUMN IF EXISTS redemptions_count;
ALTER TABLE data_loads DROP COLUMN IF EXISTS positions_count;
ALTER TABLE data_loads DROP COLUMN IF EXISTS leaderboard_count;
ALTER TABLE data_loads DROP COLUMN IF EXISTS redemptions_error;
ALTER TABLE data_loads DROP COLUMN IF EXISTS positions_error;
ALTER TABLE data_loads DROP COLUMN IF EXISTS leaderboard_error;

CREATE INDEX IF NOT EXISTS idx_data_loads_date ON data_loads(load_date DESC);
CREATE INDEX IF NOT EXISTS idx_data_loads_type ON data_loads(load_type);
CREATE INDEX IF NOT EXISTS idx_data_loads_events_loaded ON data_loads(events_loaded);

\echo '✅ data_loads table ready'

CREATE TABLE IF NOT EXISTS downstream_runs (
    id BIGSERIAL PRIMARY KEY,
    run_uuid UUID NOT NULL UNIQUE,
    trigger_type TEXT NOT NULL
        CHECK (trigger_type IN ('daily', 'genesis', 'catch_up', 'manual')),
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'success', 'partial', 'error')),
    events_load_date DATE,
    ready_events_requested INTEGER NOT NULL DEFAULT 0,
    ready_events_processed INTEGER NOT NULL DEFAULT 0,
    ready_events_failed INTEGER NOT NULL DEFAULT 0,
    redemptions_delta INTEGER NOT NULL DEFAULT 0,
    positions_delta INTEGER NOT NULL DEFAULT 0,
    leaderboard_delta INTEGER NOT NULL DEFAULT 0,
    event_cards_requested INTEGER NOT NULL DEFAULT 0,
    event_cards_processed INTEGER NOT NULL DEFAULT 0,
    event_cards_success INTEGER NOT NULL DEFAULT 0,
    event_cards_failed INTEGER NOT NULL DEFAULT 0,
    tag_colors_generated INTEGER NOT NULL DEFAULT 0,
    error_text TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_downstream_runs_started_at
    ON downstream_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_downstream_runs_status
    ON downstream_runs(status);
CREATE INDEX IF NOT EXISTS idx_downstream_runs_events_load_date
    ON downstream_runs(events_load_date);
ALTER TABLE downstream_runs ADD COLUMN IF NOT EXISTS tag_colors_generated INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_data_loads_last_downstream_run'
          AND table_name = 'data_loads'
    ) THEN
        ALTER TABLE data_loads
            ADD CONSTRAINT fk_data_loads_last_downstream_run
            FOREIGN KEY (last_downstream_run_id) REFERENCES downstream_runs(id)
            ON DELETE SET NULL;
    END IF;
END $$;

\echo '✅ downstream_runs table ready'

CREATE TABLE IF NOT EXISTS event_resolution_queue (
    event_id TEXT PRIMARY KEY,
    end_date TIMESTAMPTZ,
    closed BOOLEAN NOT NULL DEFAULT FALSE,
    closed_time TIMESTAMPTZ,
    resolution_ready_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'ready_for_redemptions', 'processed', 'error')),
    last_checked_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    downstream_attempts INTEGER NOT NULL DEFAULT 0,
    last_downstream_attempt_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    error_text TEXT,
    downstream_error_text TEXT,
    processed_run_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE event_resolution_queue ADD COLUMN IF NOT EXISTS downstream_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE event_resolution_queue ADD COLUMN IF NOT EXISTS last_downstream_attempt_at TIMESTAMPTZ;
ALTER TABLE event_resolution_queue ADD COLUMN IF NOT EXISTS downstream_error_text TEXT;
ALTER TABLE event_resolution_queue ADD COLUMN IF NOT EXISTS processed_run_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE constraint_name = 'fk_event_resolution_queue_processed_run'
          AND table_name = 'event_resolution_queue'
    ) THEN
        ALTER TABLE event_resolution_queue
            ADD CONSTRAINT fk_event_resolution_queue_processed_run
            FOREIGN KEY (processed_run_id) REFERENCES downstream_runs(id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_event_resolution_queue_status_ready
    ON event_resolution_queue(status, resolution_ready_at);
CREATE INDEX IF NOT EXISTS idx_event_resolution_queue_end_date
    ON event_resolution_queue(end_date);
CREATE INDEX IF NOT EXISTS idx_event_resolution_queue_processed_run_id
    ON event_resolution_queue(processed_run_id);

\echo '✅ event_resolution_queue table ready'

CREATE OR REPLACE VIEW recent_loads AS
SELECT
    load_date,
    load_type,
    events_loaded,
    events_count,
    markets_count,
    downstream_ready_events_count,
    last_downstream_run_id,
    downstream_last_run_at,
    CASE WHEN events_loaded THEN 'events_loaded' ELSE 'none' END as status,
    events_count + markets_count as total_records
FROM data_loads
ORDER BY load_date DESC
LIMIT 30;

CREATE OR REPLACE VIEW genesis_status AS
SELECT
    COUNT(*) as genesis_loads,
    SUM(events_count) as total_events,
    SUM(markets_count) as total_markets,
    MIN(load_date) as first_load,
    MAX(load_date) as last_load
FROM data_loads
WHERE load_type = 'genesis';

CREATE OR REPLACE VIEW daily_loads_summary AS
SELECT
    COUNT(*) as total_days,
    SUM(CASE WHEN events_loaded THEN 1 ELSE 0 END) as days_with_events,
    SUM(events_count) as total_events,
    SUM(markets_count) as total_markets,
    SUM(downstream_ready_events_count) as total_ready_events_processed,
    MIN(load_date) as first_day,
    MAX(load_date) as last_day
FROM data_loads
WHERE load_type = 'daily';

CREATE OR REPLACE VIEW recent_downstream_runs AS
SELECT
    id,
    run_uuid,
    trigger_type,
    status,
    events_load_date,
    ready_events_requested,
    ready_events_processed,
    ready_events_failed,
    redemptions_delta,
    positions_delta,
    leaderboard_delta,
    event_cards_requested,
    event_cards_processed,
    event_cards_success,
    event_cards_failed,
    tag_colors_generated,
    started_at,
    finished_at
FROM downstream_runs
ORDER BY started_at DESC
LIMIT 100;

\echo '✅ Views created'
\echo ''
\echo '✅ Simplified tracking system ready!'
\echo ''
\echo '📊 Tables: data_loads, downstream_runs, event_resolution_queue'
\echo '📈 Views: recent_loads, genesis_status, daily_loads_summary, recent_downstream_runs'
\echo ''
