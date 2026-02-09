-- ============================================================================
-- SEASON TRACKING TABLES
-- ============================================================================
-- Add tables to track data loading seasons and daily progress
-- Run this to add season tracking to existing database
-- ============================================================================

\echo '📅 Adding season tracking tables...'

-- ============================================================================
-- 1. SEASONS TABLE - Track each season
-- ============================================================================
CREATE TABLE IF NOT EXISTS seasons (
    id SERIAL PRIMARY KEY,
    season_name VARCHAR(50) UNIQUE NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    season_type VARCHAR(20) NOT NULL CHECK (season_type IN ('genesis', 'regular')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_seasons_name ON seasons(season_name);
CREATE INDEX IF NOT EXISTS idx_seasons_dates ON seasons(start_date, end_date);

\echo '✅ Seasons table created'

-- ============================================================================
-- 2. SEASON DATA LOADS TABLE - Track daily data loads per season
-- ============================================================================
CREATE TABLE IF NOT EXISTS season_data_loads (
    id SERIAL PRIMARY KEY,
    season_name VARCHAR(50) NOT NULL,
    load_date DATE NOT NULL,
    day_in_season INTEGER NOT NULL,
    
    -- Track which scripts ran successfully
    events_loaded BOOLEAN DEFAULT FALSE,
    redemptions_loaded BOOLEAN DEFAULT FALSE,
    positions_loaded BOOLEAN DEFAULT FALSE,
    leaderboard_loaded BOOLEAN DEFAULT FALSE,
    
    -- Timestamps when each script completed
    events_loaded_at TIMESTAMPTZ,
    redemptions_loaded_at TIMESTAMPTZ,
    positions_loaded_at TIMESTAMPTZ,
    leaderboard_loaded_at TIMESTAMPTZ,
    
    -- Record counts from each script
    events_count INTEGER DEFAULT 0,
    redemptions_count INTEGER DEFAULT 0,
    positions_count INTEGER DEFAULT 0,
    leaderboard_count INTEGER DEFAULT 0,
    
    -- Error tracking
    events_error TEXT,
    redemptions_error TEXT,
    positions_error TEXT,
    leaderboard_error TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    CONSTRAINT unique_season_date UNIQUE(season_name, load_date)
);

CREATE INDEX IF NOT EXISTS idx_season_data_loads_season ON season_data_loads(season_name);
CREATE INDEX IF NOT EXISTS idx_season_data_loads_date ON season_data_loads(load_date DESC);
CREATE INDEX IF NOT EXISTS idx_season_data_loads_status ON season_data_loads(season_name, events_loaded, redemptions_loaded, positions_loaded, leaderboard_loaded);

\echo '✅ Season data loads table created'

-- ============================================================================
-- 3. USEFUL VIEWS FOR SEASON ANALYTICS
-- ============================================================================

-- View: Current season status
CREATE OR REPLACE VIEW current_season_status AS
SELECT 
    s.season_name,
    s.start_date,
    s.end_date,
    COUNT(sdl.id) as days_loaded,
    SUM(CASE WHEN sdl.events_loaded THEN 1 ELSE 0 END) as days_with_events,
    SUM(CASE WHEN sdl.redemptions_loaded THEN 1 ELSE 0 END) as days_with_redemptions,
    SUM(CASE WHEN sdl.positions_loaded THEN 1 ELSE 0 END) as days_with_positions,
    SUM(CASE WHEN sdl.leaderboard_loaded THEN 1 ELSE 0 END) as days_with_leaderboard,
    SUM(sdl.events_count) as total_events,
    SUM(sdl.redemptions_count) as total_redemptions,
    SUM(sdl.positions_count) as total_positions,
    SUM(sdl.leaderboard_count) as total_leaderboard
FROM seasons s
LEFT JOIN season_data_loads sdl ON s.season_name = sdl.season_name
GROUP BY s.season_name, s.start_date, s.end_date
ORDER BY s.start_date DESC;

-- View: Daily load status (last 30 days)
CREATE OR REPLACE VIEW daily_load_status AS
SELECT 
    load_date,
    season_name,
    day_in_season,
    events_loaded,
    redemptions_loaded,
    positions_loaded,
    leaderboard_loaded,
    CASE 
        WHEN events_loaded AND redemptions_loaded AND positions_loaded AND leaderboard_loaded THEN 'complete'
        WHEN events_loaded OR redemptions_loaded OR positions_loaded OR leaderboard_loaded THEN 'partial'
        ELSE 'none'
    END as status,
    events_count + redemptions_count + positions_count + leaderboard_count as total_records
FROM season_data_loads
WHERE load_date >= CURRENT_DATE - INTERVAL '30 days'
ORDER BY load_date DESC;

\echo '✅ Season views created'

-- ============================================================================
-- COMPLETION
-- ============================================================================
\echo ''
\echo '✅ Season tracking tables successfully added!'
\echo ''
\echo '📊 New tables:'
\echo '   - seasons: Track season metadata'
\echo '   - season_data_loads: Track daily data loads'
\echo ''
\echo '📈 New views:'
\echo '   - current_season_status: Overview of all seasons'
\echo '   - daily_load_status: Recent daily load status'
\echo ''
