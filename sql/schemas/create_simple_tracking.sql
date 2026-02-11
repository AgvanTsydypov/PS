-- ============================================================================
-- SIMPLIFIED DATA LOADING TRACKING
-- ============================================================================
-- Single table to track daily data loads (no seasons)
-- ============================================================================

\echo '📊 Creating simplified data loading tracking...'

-- ============================================================================
-- DATA_LOADS TABLE - Track daily data loads
-- ============================================================================

CREATE TABLE IF NOT EXISTS data_loads (
    id SERIAL PRIMARY KEY,
    load_date DATE NOT NULL UNIQUE,
    
    -- Track which data types loaded
    events_loaded BOOLEAN DEFAULT FALSE,
    redemptions_loaded BOOLEAN DEFAULT FALSE,
    positions_loaded BOOLEAN DEFAULT FALSE,
    leaderboard_loaded BOOLEAN DEFAULT FALSE,
    
    -- Timestamps when each type completed
    events_loaded_at TIMESTAMPTZ,
    redemptions_loaded_at TIMESTAMPTZ,
    positions_loaded_at TIMESTAMPTZ,
    leaderboard_loaded_at TIMESTAMPTZ,
    
    -- Record counts from each type
    events_count INTEGER DEFAULT 0,
    markets_count INTEGER DEFAULT 0,
    redemptions_count INTEGER DEFAULT 0,
    positions_count INTEGER DEFAULT 0,
    leaderboard_count INTEGER DEFAULT 0,
    
    -- Type: 'genesis' (historical) or 'daily' (current)
    load_type VARCHAR(20) DEFAULT 'daily' CHECK (load_type IN ('genesis', 'daily')),
    
    -- Error tracking
    events_error TEXT,
    redemptions_error TEXT,
    positions_error TEXT,
    leaderboard_error TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_data_loads_date ON data_loads(load_date DESC);
CREATE INDEX IF NOT EXISTS idx_data_loads_type ON data_loads(load_type);
CREATE INDEX IF NOT EXISTS idx_data_loads_status ON data_loads(
    events_loaded, redemptions_loaded, positions_loaded, leaderboard_loaded
);

\echo '✅ data_loads table created'

-- ============================================================================
-- USEFUL VIEWS
-- ============================================================================

-- Recent loads status
CREATE OR REPLACE VIEW recent_loads AS
SELECT 
    load_date,
    load_type,
    events_loaded,
    redemptions_loaded,
    positions_loaded,
    leaderboard_loaded,
    CASE 
        WHEN events_loaded AND redemptions_loaded AND positions_loaded AND leaderboard_loaded 
        THEN 'complete'
        WHEN events_loaded OR redemptions_loaded OR positions_loaded OR leaderboard_loaded 
        THEN 'partial'
        ELSE 'none'
    END as status,
    events_count + redemptions_count + positions_count + leaderboard_count as total_records
FROM data_loads
ORDER BY load_date DESC
LIMIT 30;

-- Genesis status
CREATE OR REPLACE VIEW genesis_status AS
SELECT 
    COUNT(*) as genesis_loads,
    SUM(events_count) as total_events,
    SUM(redemptions_count) as total_redemptions,
    SUM(positions_count) as total_positions,
    SUM(leaderboard_count) as total_leaderboard,
    MIN(load_date) as first_load,
    MAX(load_date) as last_load
FROM data_loads
WHERE load_type = 'genesis';

-- Daily loads summary
CREATE OR REPLACE VIEW daily_loads_summary AS
SELECT 
    COUNT(*) as total_days,
    SUM(CASE WHEN events_loaded THEN 1 ELSE 0 END) as days_with_events,
    SUM(CASE WHEN redemptions_loaded THEN 1 ELSE 0 END) as days_with_redemptions,
    SUM(CASE WHEN positions_loaded THEN 1 ELSE 0 END) as days_with_positions,
    SUM(CASE WHEN leaderboard_loaded THEN 1 ELSE 0 END) as days_with_leaderboard,
    SUM(events_count) as total_events,
    SUM(redemptions_count) as total_redemptions,
    SUM(positions_count) as total_positions,
    SUM(leaderboard_count) as total_leaderboard,
    MIN(load_date) as first_day,
    MAX(load_date) as last_day
FROM data_loads
WHERE load_type = 'daily';

\echo '✅ Views created'

-- ============================================================================
-- MIGRATION FROM OLD SYSTEM (if needed)
-- ============================================================================

-- If you have old season_data_loads table, migrate data:
-- INSERT INTO data_loads (
--     load_date, load_type,
--     events_loaded, redemptions_loaded, positions_loaded, leaderboard_loaded,
--     events_count, redemptions_count, positions_count, leaderboard_count
-- )
-- SELECT 
--     load_date,
--     CASE WHEN season_name = 'genesis' THEN 'genesis' ELSE 'daily' END,
--     events_loaded, redemptions_loaded, positions_loaded, leaderboard_loaded,
--     events_count, redemptions_count, positions_count, leaderboard_count
-- FROM season_data_loads
-- ON CONFLICT (load_date) DO NOTHING;

\echo ''
\echo '✅ Simplified tracking system ready!'
\echo ''
\echo '📊 Table: data_loads'
\echo '📈 Views: recent_loads, genesis_status, daily_loads_summary'
\echo ''
