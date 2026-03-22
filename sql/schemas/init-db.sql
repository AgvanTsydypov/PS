-- ============================================================================
-- POLYMARKET DATABASE INITIALIZATION SCRIPT
-- ============================================================================
-- This script creates all tables for PolyStars project
-- Auto-executed by Docker PostgreSQL on first container startup
-- ============================================================================

DO $$ BEGIN RAISE NOTICE '🚀 Starting PolyStars database initialization...'; END $$;

-- ============================================================================
-- 1. EVENTS TABLE - Main events data
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '📊 Creating events table...'; END $$;

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    ticker TEXT,
    slug TEXT,
    title TEXT NOT NULL,
    description TEXT,
    
    -- Date fields
    start_date TIMESTAMPTZ,
    creation_date TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    closed_time TIMESTAMPTZ,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    
    -- URLs
    image TEXT,
    icon TEXT,
    
    -- Boolean flags
    active BOOLEAN DEFAULT false,
    closed BOOLEAN DEFAULT false,
    archived BOOLEAN DEFAULT false,
    new BOOLEAN DEFAULT false,
    featured BOOLEAN DEFAULT false,
    restricted BOOLEAN DEFAULT false,
    neg_risk BOOLEAN DEFAULT false,
    enable_order_book BOOLEAN DEFAULT false,
    
    -- Volume metrics
    volume NUMERIC(20, 6) DEFAULT 0,
    volume24hr NUMERIC(20, 6) DEFAULT 0,
    volume1wk NUMERIC(20, 6) DEFAULT 0,
    volume1mo NUMERIC(20, 6) DEFAULT 0,
    volume1yr NUMERIC(20, 6) DEFAULT 0,
    
    -- Liquidity metrics
    liquidity NUMERIC(20, 6) DEFAULT 0,
    open_interest NUMERIC(20, 6) DEFAULT 0,
    liquidity_amm NUMERIC(20, 6) DEFAULT 0,
    liquidity_clob NUMERIC(20, 6) DEFAULT 0,
    
    -- Other fields
    competitive INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0
);

-- Events indexes
CREATE INDEX IF NOT EXISTS idx_events_closed ON events(closed);
CREATE INDEX IF NOT EXISTS idx_events_closed_time ON events(closed_time);
CREATE INDEX IF NOT EXISTS idx_events_end_date ON events(end_date);
CREATE INDEX IF NOT EXISTS idx_events_volume ON events(volume DESC);
CREATE INDEX IF NOT EXISTS idx_events_active ON events(active);
CREATE INDEX IF NOT EXISTS idx_events_ticker ON events(ticker);

DO $$ BEGIN RAISE NOTICE '✅ Events table created'; END $$;

-- ============================================================================
-- 2. EVENT METADATA TABLES - Series and tags normalization
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🏷️ Creating series/tags tables...'; END $$;

CREATE TABLE IF NOT EXISTS series (
    id TEXT PRIMARY KEY,
    ticker TEXT,
    slug TEXT,
    title TEXT,
    subtitle TEXT,
    series_type TEXT,
    recurrence TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    label TEXT,
    hex_color TEXT,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT tags_hex_color_format
        CHECK (hex_color IS NULL OR hex_color ~* '^#[0-9a-f]{6}$')
);

CREATE TABLE IF NOT EXISTS event_tags (
    event_id TEXT NOT NULL,
    tag_id TEXT NOT NULL,
    PRIMARY KEY (event_id, tag_id),
    CONSTRAINT fk_event_tags_event
        FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
    CONSTRAINT fk_event_tags_tag
        FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

ALTER TABLE tags
    ADD COLUMN IF NOT EXISTS hex_color TEXT;

ALTER TABLE tags
    ADD COLUMN IF NOT EXISTS is_primary BOOLEAN NOT NULL DEFAULT FALSE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'tags_hex_color_format'
          AND conrelid = 'tags'::regclass
    ) THEN
        ALTER TABLE tags
            ADD CONSTRAINT tags_hex_color_format
            CHECK (hex_color IS NULL OR hex_color ~* '^#[0-9a-f]{6}$');
    END IF;
END $$;

-- Ensure events table can reference series safely on existing databases.
ALTER TABLE events
    ADD COLUMN IF NOT EXISTS series_id TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_events_series'
          AND conrelid = 'events'::regclass
    ) THEN
        ALTER TABLE events
            ADD CONSTRAINT fk_events_series
            FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_events_series_id ON events(series_id);
CREATE INDEX IF NOT EXISTS idx_event_tags_tag_id ON event_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_tags_hex_color ON tags(hex_color);
CREATE INDEX IF NOT EXISTS idx_tags_is_primary ON tags(is_primary);

DO $$ BEGIN RAISE NOTICE '✅ Series/tags tables created'; END $$;

-- ============================================================================
-- 3. MARKETS TABLE - Individual markets within events
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '📈 Creating markets table...'; END $$;

CREATE TABLE IF NOT EXISTS markets (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    
    -- Basic info
    question TEXT NOT NULL,
    condition_id TEXT,
    slug TEXT,
    question_id TEXT,
    
    -- Dates
    end_date TIMESTAMPTZ,
    start_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    closed_time TIMESTAMPTZ,
    uma_end_date TIMESTAMPTZ,
    accepting_orders_timestamp TIMESTAMPTZ,
    deploying_timestamp TIMESTAMPTZ,
    
    -- URLs
    image TEXT,
    icon TEXT,
    
    -- Description and outcomes
    description TEXT,
    outcomes TEXT,
    outcome_prices TEXT,
    
    -- Volume metrics
    volume TEXT,
    volume_num NUMERIC(20, 6) DEFAULT 0,
    volume24hr NUMERIC(20, 6) DEFAULT 0,
    volume1wk NUMERIC(20, 6) DEFAULT 0,
    volume1mo NUMERIC(20, 6) DEFAULT 0,
    volume1yr NUMERIC(20, 6) DEFAULT 0,
    volume_clob NUMERIC(20, 6) DEFAULT 0,
    volume24hr_clob NUMERIC(20, 6) DEFAULT 0,
    volume1wk_clob NUMERIC(20, 6) DEFAULT 0,
    volume1mo_clob NUMERIC(20, 6) DEFAULT 0,
    volume1yr_clob NUMERIC(20, 6) DEFAULT 0,
    
    -- Liquidity
    liquidity TEXT,
    liquidity_num NUMERIC(20, 6) DEFAULT 0,
    liquidity_amm NUMERIC(20, 6) DEFAULT 0,
    liquidity_clob NUMERIC(20, 6) DEFAULT 0,
    
    -- Boolean flags
    active BOOLEAN DEFAULT false,
    closed BOOLEAN DEFAULT false,
    new BOOLEAN DEFAULT false,
    featured BOOLEAN DEFAULT false,
    archived BOOLEAN DEFAULT false,
    restricted BOOLEAN DEFAULT false,
    enable_order_book BOOLEAN DEFAULT false,
    neg_risk BOOLEAN DEFAULT false,
    ready BOOLEAN DEFAULT false,
    funded BOOLEAN DEFAULT false,
    cyom BOOLEAN DEFAULT false,
    pager_duty_notification_enabled BOOLEAN DEFAULT false,
    approved BOOLEAN DEFAULT false,
    automatically_resolved BOOLEAN DEFAULT false,
    automatically_active BOOLEAN DEFAULT false,
    clear_book_on_start BOOLEAN DEFAULT false,
    manual_activation BOOLEAN DEFAULT false,
    neg_risk_other BOOLEAN DEFAULT false,
    pending_deployment BOOLEAN DEFAULT false,
    deploying BOOLEAN DEFAULT false,
    rfq_enabled BOOLEAN DEFAULT false,
    holding_rewards_enabled BOOLEAN DEFAULT false,
    fees_enabled BOOLEAN DEFAULT false,
    requires_translation BOOLEAN DEFAULT false,
    accepting_orders BOOLEAN DEFAULT false,
    has_reviewed_dates BOOLEAN DEFAULT false,
    
    -- Resolution
    resolved_by TEXT,
    uma_resolution_status TEXT,
    uma_resolution_statuses TEXT,
    uma_bond TEXT,
    uma_reward TEXT,
    
    -- Market details
    market_maker_address TEXT,
    submitted_by TEXT,
    group_item_title TEXT,
    group_item_threshold TEXT,
    clob_token_ids TEXT,
    neg_risk_request_id TEXT,
    
    -- Date fields (ISO format)
    end_date_iso TEXT,
    start_date_iso TEXT,
    
    -- Trading parameters
    order_price_min_tick_size NUMERIC(10, 6),
    order_min_size NUMERIC(20, 6),
    rewards_min_size NUMERIC(20, 6),
    rewards_max_spread NUMERIC(10, 6),
    spread NUMERIC(10, 6),
    
    -- Price changes
    one_day_price_change NUMERIC(10, 6),
    one_week_price_change NUMERIC(10, 6),
    last_trade_price NUMERIC(10, 6),
    best_bid NUMERIC(10, 6),
    best_ask NUMERIC(10, 6),
    
    -- Other
    competitive INTEGER DEFAULT 0,
    custom_liveness INTEGER DEFAULT 0,
    
    -- Foreign key to events
    CONSTRAINT fk_event FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

-- Markets indexes
CREATE INDEX IF NOT EXISTS idx_markets_event_id ON markets(event_id);
CREATE INDEX IF NOT EXISTS idx_markets_closed ON markets(closed);
CREATE INDEX IF NOT EXISTS idx_markets_volume ON markets(volume_num DESC);
CREATE INDEX IF NOT EXISTS idx_markets_status ON markets(uma_resolution_status);
CREATE INDEX IF NOT EXISTS idx_markets_end_date ON markets(end_date);

DO $$ BEGIN RAISE NOTICE '✅ Markets table created'; END $$;

-- ============================================================================
-- 4. REDEMPTIONS TABLE - When users claim their winnings
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '💰 Creating redemptions table...'; END $$;

CREATE TABLE IF NOT EXISTS redemptions (
    id BIGSERIAL PRIMARY KEY,
    transaction_hash TEXT NOT NULL,
    condition_id TEXT,
    event_id TEXT,
    market_id TEXT,
    redeemer_address TEXT NOT NULL,
    payout_usdc NUMERIC(20, 6) NOT NULL DEFAULT 0,
    timestamp_unix BIGINT NOT NULL,
    timestamp_human TIMESTAMPTZ,
    market_question TEXT,
    event_title TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT unique_redemption UNIQUE(transaction_hash, redeemer_address)
);

-- Redemptions indexes
CREATE INDEX IF NOT EXISTS idx_redemptions_condition_id ON redemptions(condition_id);
CREATE INDEX IF NOT EXISTS idx_redemptions_event_id ON redemptions(event_id);
CREATE INDEX IF NOT EXISTS idx_redemptions_market_id ON redemptions(market_id);
CREATE INDEX IF NOT EXISTS idx_redemptions_redeemer ON redemptions(redeemer_address);
CREATE INDEX IF NOT EXISTS idx_redemptions_timestamp ON redemptions(timestamp_unix DESC);
CREATE INDEX IF NOT EXISTS idx_redemptions_payout ON redemptions(payout_usdc DESC);

DO $$ BEGIN RAISE NOTICE '✅ Redemptions table created'; END $$;

-- ============================================================================
-- 5. USER CLOSED POSITIONS TABLE - Closed trading positions
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '📊 Creating user_closed_positions table...'; END $$;

CREATE TABLE IF NOT EXISTS user_closed_positions (
    id BIGSERIAL PRIMARY KEY,
    
    -- User and market identification
    proxy_wallet TEXT NOT NULL,
    event_id TEXT,
    market_id TEXT,
    condition_id TEXT,
    asset TEXT,
    
    -- Position metrics
    avg_price NUMERIC(20, 6) DEFAULT 0,
    total_bought NUMERIC(20, 6) DEFAULT 0,
    realized_pnl NUMERIC(20, 6) DEFAULT 0,
    cur_price NUMERIC(20, 6) DEFAULT 0,
    
    -- Timestamp
    timestamp_unix BIGINT NOT NULL,
    timestamp_human TIMESTAMPTZ,
    
    -- Market/Event information
    title TEXT,
    slug TEXT,
    icon TEXT,
    event_slug TEXT,
    
    -- Outcome information
    outcome TEXT,
    outcome_index INTEGER,
    opposite_outcome TEXT,
    opposite_asset TEXT,
    
    -- End date
    end_date TEXT,
    end_date_parsed TIMESTAMPTZ,
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Ensure unique positions per user/condition
    CONSTRAINT unique_user_position UNIQUE(proxy_wallet, condition_id, asset, timestamp_unix)
);

-- User closed positions indexes
CREATE INDEX IF NOT EXISTS idx_user_closed_positions_wallet ON user_closed_positions(proxy_wallet);
CREATE INDEX IF NOT EXISTS idx_user_closed_positions_event_id ON user_closed_positions(event_id);
CREATE INDEX IF NOT EXISTS idx_user_closed_positions_market_id ON user_closed_positions(market_id);
CREATE INDEX IF NOT EXISTS idx_user_closed_positions_condition ON user_closed_positions(condition_id);
CREATE INDEX IF NOT EXISTS idx_user_closed_positions_timestamp ON user_closed_positions(timestamp_unix DESC);
CREATE INDEX IF NOT EXISTS idx_user_closed_positions_realized_pnl ON user_closed_positions(realized_pnl DESC);
CREATE INDEX IF NOT EXISTS idx_user_closed_positions_event_slug ON user_closed_positions(event_slug);
CREATE INDEX IF NOT EXISTS idx_user_closed_positions_outcome ON user_closed_positions(outcome_index);

DO $$ BEGIN RAISE NOTICE '✅ User_closed_positions table created'; END $$;

-- ============================================================================
-- 6. TRADER LEADERBOARD TABLE - Trader rankings
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🏆 Creating trader_leaderboard table...'; END $$;

CREATE TABLE IF NOT EXISTS trader_leaderboard (
    -- Primary identifiers
    id BIGSERIAL PRIMARY KEY,
    
    -- Leaderboard data
    rank INTEGER,
    proxy_wallet VARCHAR(42) NOT NULL,
    user_name VARCHAR(255),
    vol NUMERIC(24, 6) NOT NULL DEFAULT 0,
    pnl NUMERIC(24, 6) NOT NULL DEFAULT 0,
    
    -- Profile information
    profile_image TEXT,
    x_username VARCHAR(255),
    verified_badge BOOLEAN DEFAULT FALSE,
    
    -- Query parameters
    category VARCHAR(20) NOT NULL,
    time_period VARCHAR(10) NOT NULL,
    order_by VARCHAR(10) NOT NULL,
    
    -- Metadata
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT trader_leaderboard_proxy_wallet_check CHECK (proxy_wallet ~* '^0x[a-f0-9]{40}$'),
    CONSTRAINT trader_leaderboard_category_check CHECK (category IN ('OVERALL', 'POLITICS', 'SPORTS', 'CRYPTO', 'CULTURE', 'MENTIONS', 'WEATHER', 'ECONOMICS', 'TECH', 'FINANCE')),
    CONSTRAINT trader_leaderboard_time_period_check CHECK (time_period IN ('DAY', 'WEEK', 'MONTH', 'ALL')),
    CONSTRAINT trader_leaderboard_order_by_check CHECK (order_by IN ('PNL', 'VOL'))
);

-- Trader leaderboard indexes
CREATE UNIQUE INDEX IF NOT EXISTS idx_trader_leaderboard_unique_entry
    ON trader_leaderboard(proxy_wallet, category, time_period, order_by, fetched_date);
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_proxy_wallet ON trader_leaderboard(proxy_wallet);
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_rank ON trader_leaderboard(rank) WHERE rank IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_category_period ON trader_leaderboard(category, time_period, order_by);
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_user_name ON trader_leaderboard(user_name) WHERE user_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_fetched_at ON trader_leaderboard(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_category_period_rank ON trader_leaderboard(category, time_period, rank) WHERE rank IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_vol ON trader_leaderboard(vol DESC);
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_pnl ON trader_leaderboard(pnl DESC);

DO $$ BEGIN RAISE NOTICE '✅ Trader_leaderboard table created'; END $$;

-- ============================================================================
-- 7. USEFUL VIEWS FOR ANALYTICS
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '📊 Creating analytics views...'; END $$;

-- Events with market count and total volume
CREATE OR REPLACE VIEW events_summary AS
SELECT 
    e.id,
    e.ticker,
    e.slug,
    e.title,
    e.description,
    e.start_date,
    e.creation_date,
    e.end_date,
    e.closed_time,
    e.created_at,
    e.updated_at,
    e.image,
    e.icon,
    e.active,
    e.closed,
    e.archived,
    e.new,
    e.featured,
    e.restricted,
    e.neg_risk,
    e.enable_order_book,
    e.volume,
    e.volume24hr,
    e.volume1wk,
    e.volume1mo,
    e.volume1yr,
    e.liquidity,
    e.open_interest,
    e.liquidity_amm,
    e.liquidity_clob,
    e.competitive,
    e.comment_count,
    COUNT(m.id) as market_count,
    SUM(m.volume_num) as total_market_volume,
    AVG(m.volume_num) as avg_market_volume
FROM events e
LEFT JOIN markets m ON e.id = m.event_id
GROUP BY
    e.id, e.ticker, e.slug, e.title, e.description,
    e.start_date, e.creation_date, e.end_date, e.closed_time, e.created_at, e.updated_at,
    e.image, e.icon,
    e.active, e.closed, e.archived, e.new, e.featured, e.restricted, e.neg_risk, e.enable_order_book,
    e.volume, e.volume24hr, e.volume1wk, e.volume1mo, e.volume1yr,
    e.liquidity, e.open_interest, e.liquidity_amm, e.liquidity_clob,
    e.competitive, e.comment_count;

-- Top volume events
CREATE OR REPLACE VIEW top_volume_events AS
SELECT 
    id,
    title,
    volume,
    end_date,
    closed
FROM events
ORDER BY volume DESC
LIMIT 100;

-- Flattened events data for analytics (one row per event-tag relation)
DROP VIEW IF EXISTS events_flat_analytics;
CREATE VIEW events_flat_analytics AS
SELECT
    e.id,
    e.title,
    e.slug,
    t.id AS tag_id,
    t.label AS tag_label,
    s.id AS series_id,
    s.ticker AS series_ticker,
    s.slug AS series_slug,
    s.title AS series_title,
    s.series_type,
    s.subtitle AS series_subtitle
FROM events e
LEFT JOIN series s
    ON s.id = e.series_id
LEFT JOIN event_tags et
    ON et.event_id = e.id
LEFT JOIN tags t
    ON t.id = et.tag_id;

-- User PnL Summary
CREATE OR REPLACE VIEW user_pnl_summary AS
SELECT 
    proxy_wallet,
    COUNT(*) as total_positions,
    SUM(realized_pnl) as total_realized_pnl,
    AVG(realized_pnl) as avg_pnl_per_position,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_positions,
    SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losing_positions,
    MAX(realized_pnl) as best_trade,
    MIN(realized_pnl) as worst_trade
FROM user_closed_positions
GROUP BY proxy_wallet;

DO $$ BEGIN RAISE NOTICE '✅ Analytics views created'; END $$;

-- ============================================================================
-- DATA LOADING TRACKING (closed_time pipeline)
-- ============================================================================
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '📊 Creating data loading tracking table...'; END $$;

CREATE TABLE IF NOT EXISTS data_loads (
    id SERIAL PRIMARY KEY,
    load_date DATE NOT NULL UNIQUE,

    -- Ingest tracking (events/markets only)
    events_loaded BOOLEAN DEFAULT FALSE,
    events_loaded_at TIMESTAMPTZ,
    events_count INTEGER DEFAULT 0,
    markets_count INTEGER DEFAULT 0,

    -- Type: 'genesis' (historical) or 'daily' (incremental)
    load_type VARCHAR(20) DEFAULT 'daily' CHECK (load_type IN ('genesis', 'daily')),

    -- Ingest error tracking
    events_error TEXT,

    -- Link to latest downstream processing run touching this load_date
    last_downstream_run_id BIGINT,
    downstream_last_run_at TIMESTAMPTZ,
    downstream_ready_events_count INTEGER NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_data_loads_date ON data_loads(load_date DESC);
CREATE INDEX IF NOT EXISTS idx_data_loads_type ON data_loads(load_type);
CREATE INDEX IF NOT EXISTS idx_data_loads_events_loaded ON data_loads(events_loaded);

-- Add markets_count column if it doesn't exist (for existing databases)
ALTER TABLE data_loads ADD COLUMN IF NOT EXISTS markets_count INTEGER DEFAULT 0;
ALTER TABLE data_loads ADD COLUMN IF NOT EXISTS events_error TEXT;
ALTER TABLE data_loads ADD COLUMN IF NOT EXISTS last_downstream_run_id BIGINT;
ALTER TABLE data_loads ADD COLUMN IF NOT EXISTS downstream_last_run_at TIMESTAMPTZ;
ALTER TABLE data_loads ADD COLUMN IF NOT EXISTS downstream_ready_events_count INTEGER NOT NULL DEFAULT 0;

-- Old tracking views reference legacy downstream columns, drop before column cleanup.
DROP VIEW IF EXISTS recent_loads;
DROP VIEW IF EXISTS genesis_status;
DROP VIEW IF EXISTS daily_loads_summary;
DROP VIEW IF EXISTS recent_downstream_runs;

-- Drop legacy downstream columns from old date-based pipeline.
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

DO $$ BEGIN RAISE NOTICE '✅ data_loads table created'; END $$;

-- ============================================================================
-- DOWNSTREAM RUNS (closed_time batch observability)
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🧾 Creating downstream_runs table...'; END $$;

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

DO $$ BEGIN RAISE NOTICE '✅ downstream_runs table created'; END $$;

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

-- ============================================================================
-- EVENT RESOLUTION QUEUE (closed_time processing)
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🧩 Creating event_resolution_queue table...'; END $$;

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

DO $$ BEGIN RAISE NOTICE '✅ event_resolution_queue table created'; END $$;

-- ============================================================================
-- 8. EVENT CARDS TABLES - AI-generated trader card metadata
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🧠 Creating event_cards tables...'; END $$;

CREATE TABLE IF NOT EXISTS event_cards (
    event_id TEXT PRIMARY KEY,
    card_title TEXT,
    card_lore TEXT,
    primary_tag TEXT,
    secondary_tag TEXT,
    agent_name TEXT NOT NULL DEFAULT 'agent_1_quant',
    model_name TEXT NOT NULL DEFAULT 'gemini-2.5-flash',
    prompt_version TEXT NOT NULL DEFAULT 'v1',
    status TEXT NOT NULL DEFAULT 'ok'
        CHECK (status IN ('ok', 'error')),
    error_text TEXT,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_event_cards_event
        FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_cards_status ON event_cards(status);
CREATE INDEX IF NOT EXISTS idx_event_cards_prompt_version ON event_cards(prompt_version);
CREATE INDEX IF NOT EXISTS idx_event_cards_generated_at ON event_cards(generated_at DESC);

DO $$ BEGIN RAISE NOTICE '✅ event_cards tables created'; END $$;

-- ============================================================================
-- 9. USER WALLET SIGN-INS TABLE - Auth logins for user site
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🔐 Creating user_wallet_signins table...'; END $$;

CREATE TABLE IF NOT EXISTS user_wallet_signins (
    wallet_address TEXT PRIMARY KEY,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_signed_in_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sign_in_count INTEGER NOT NULL DEFAULT 1,
    proxy_wallet TEXT NOT NULL DEFAULT 'Not registered in PM',
    trader_rank TEXT NOT NULL DEFAULT 'No trades yet',
    CONSTRAINT user_wallet_signins_wallet_check CHECK (wallet_address ~* '^0x[a-f0-9]{40}$')
);

-- Ensure proxy_wallet exists for upgraded databases.
ALTER TABLE user_wallet_signins
    ADD COLUMN IF NOT EXISTS proxy_wallet TEXT NOT NULL DEFAULT 'Not registered in PM';

ALTER TABLE user_wallet_signins
    ADD COLUMN IF NOT EXISTS trader_rank TEXT NOT NULL DEFAULT 'No trades yet';

-- Ensure uniqueness guard index for claims exists when claims table is present.
DO $$
BEGIN
    IF to_regclass('public.claims') IS NOT NULL THEN
        EXECUTE '
            CREATE UNIQUE INDEX IF NOT EXISTS ux_claims_active_season_user_wallet_lower
            ON claims(season_id, LOWER(user_wallet))
            WHERE status IN (''PENDING'', ''PROCESSING'', ''COMPLETED'')
        ';
    ELSE
        RAISE NOTICE 'claims table not found in current schema; skipping claims uniqueness index';
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_user_wallet_signins_last_signed_in_at
    ON user_wallet_signins(last_signed_in_at DESC);

DO $$ BEGIN RAISE NOTICE '✅ user_wallet_signins table created'; END $$;

-- Tracking views
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
    CASE 
        WHEN events_loaded THEN 'events_loaded'
        ELSE 'none'
    END as status,
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
    started_at,
    finished_at
FROM downstream_runs
ORDER BY started_at DESC
LIMIT 100;

DO $$ BEGIN RAISE NOTICE '✅ Tracking views created'; END $$;

-- ============================================================================
-- COMPLETION
-- ============================================================================
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '✅ PolyStars database initialization complete!'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '📊 Created tables:'; END $$;
DO $$ BEGIN RAISE NOTICE '   - events'; END $$;
DO $$ BEGIN RAISE NOTICE '   - series'; END $$;
DO $$ BEGIN RAISE NOTICE '   - tags'; END $$;
DO $$ BEGIN RAISE NOTICE '   - event_tags'; END $$;
DO $$ BEGIN RAISE NOTICE '   - markets'; END $$;
DO $$ BEGIN RAISE NOTICE '   - redemptions'; END $$;
DO $$ BEGIN RAISE NOTICE '   - user_closed_positions'; END $$;
DO $$ BEGIN RAISE NOTICE '   - trader_leaderboard'; END $$;
DO $$ BEGIN RAISE NOTICE '   - data_loads (tracking)'; END $$;
DO $$ BEGIN RAISE NOTICE '   - downstream_runs'; END $$;
DO $$ BEGIN RAISE NOTICE '   - event_resolution_queue'; END $$;
DO $$ BEGIN RAISE NOTICE '   - event_cards'; END $$;
DO $$ BEGIN RAISE NOTICE '   - user_wallet_signins'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '📈 Created views:'; END $$;
DO $$ BEGIN RAISE NOTICE '   - events_summary'; END $$;
DO $$ BEGIN RAISE NOTICE '   - top_volume_events'; END $$;
DO $$ BEGIN RAISE NOTICE '   - events_flat_analytics'; END $$;
DO $$ BEGIN RAISE NOTICE '   - user_pnl_summary'; END $$;
DO $$ BEGIN RAISE NOTICE '   - recent_loads'; END $$;
DO $$ BEGIN RAISE NOTICE '   - genesis_status'; END $$;
DO $$ BEGIN RAISE NOTICE '   - daily_loads_summary'; END $$;
DO $$ BEGIN RAISE NOTICE '   - recent_downstream_runs'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '🚀 Database is ready for data ingestion!'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
