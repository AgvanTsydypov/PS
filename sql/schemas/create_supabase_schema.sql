-- ============================================================================
-- SUPABASE DATABASE SCHEMA FOR POLYMARKET EVENTS
-- ============================================================================
-- Run this SQL in your Supabase SQL Editor to create the tables
-- ============================================================================

-- 1. Events Table (Main events data)
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

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_events_closed ON events(closed);
CREATE INDEX IF NOT EXISTS idx_events_closed_time ON events(closed_time);
CREATE INDEX IF NOT EXISTS idx_events_end_date ON events(end_date);
CREATE INDEX IF NOT EXISTS idx_events_volume ON events(volume DESC);
CREATE INDEX IF NOT EXISTS idx_events_active ON events(active);
CREATE INDEX IF NOT EXISTS idx_events_ticker ON events(ticker);


-- 2. Markets Table (Individual markets within events)
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
    outcomes TEXT,  -- JSON array as string
    outcome_prices TEXT,  -- JSON array as string
    
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
    uma_resolution_statuses TEXT,  -- JSON array as string
    uma_bond TEXT,
    uma_reward TEXT,
    
    -- Market details
    market_maker_address TEXT,
    submitted_by TEXT,
    group_item_title TEXT,
    group_item_threshold TEXT,
    clob_token_ids TEXT,  -- JSON array as string
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

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_markets_event_id ON markets(event_id);
CREATE INDEX IF NOT EXISTS idx_markets_closed ON markets(closed);
CREATE INDEX IF NOT EXISTS idx_markets_volume ON markets(volume_num DESC);
CREATE INDEX IF NOT EXISTS idx_markets_status ON markets(uma_resolution_status);
CREATE INDEX IF NOT EXISTS idx_markets_end_date ON markets(end_date);


-- 3. Fetch Metadata Table (Track data fetches)
CREATE TABLE IF NOT EXISTS fetch_metadata (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    total_events INTEGER,
    fetch_method TEXT,
    filters JSONB,  -- Store filters as JSON
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create index for timestamp queries
CREATE INDEX IF NOT EXISTS idx_fetch_metadata_timestamp ON fetch_metadata(timestamp DESC);


-- ============================================================================
-- ENABLE ROW LEVEL SECURITY (RLS)
-- ============================================================================
-- Uncomment these if you want to enable RLS for security
-- You'll need to add policies based on your authentication setup

-- ALTER TABLE events ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE markets ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE fetch_metadata ENABLE ROW LEVEL SECURITY;

-- Example policy (allows all authenticated users to read)
-- CREATE POLICY "Allow public read access" ON events FOR SELECT USING (true);
-- CREATE POLICY "Allow public read access" ON markets FOR SELECT USING (true);


-- ============================================================================
-- USEFUL VIEWS FOR ANALYTICS
-- ============================================================================

-- View: Events with market count and total volume
CREATE OR REPLACE VIEW events_summary AS
SELECT 
    e.*,
    COUNT(m.id) as market_count,
    SUM(m.volume_num) as total_market_volume,
    AVG(m.volume_num) as avg_market_volume
FROM events e
LEFT JOIN markets m ON e.id = m.event_id
GROUP BY e.id;

-- View: Top volume events
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

-- View: Recently closed events
CREATE OR REPLACE VIEW recently_closed_events AS
SELECT 
    e.id,
    e.title,
    e.volume,
    e.end_date,
    COUNT(m.id) as market_count
FROM events e
LEFT JOIN markets m ON e.id = m.event_id
WHERE e.closed = true
GROUP BY e.id, e.title, e.volume, e.end_date
ORDER BY e.end_date DESC
LIMIT 100;


-- ============================================================================
-- GRANT PERMISSIONS
-- ============================================================================
-- Grant permissions to authenticated users (adjust based on your needs)
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO authenticated;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon;

