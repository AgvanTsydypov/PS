-- ==========================================
-- TABLE: trader_leaderboard
-- ==========================================
-- Stores trader leaderboard rankings from Polymarket API
-- Source: https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings
--
-- Usage:
--   psql -U your_user -d your_database -f sql/schema/create_trader_leaderboard_table.sql

-- Drop table if exists (optional, comment out for production)
-- DROP TABLE IF EXISTS public.trader_leaderboard CASCADE;

CREATE TABLE IF NOT EXISTS public.trader_leaderboard (
    -- Primary identifiers
    id BIGSERIAL PRIMARY KEY,
    
    -- Leaderboard data
    rank INTEGER,  -- The rank position of the trader
    proxy_wallet VARCHAR(42) NOT NULL,  -- User Profile Address (0x-prefixed, 40 hex chars)
    user_name VARCHAR(255),  -- The trader's username
    vol NUMERIC(24, 6) NOT NULL DEFAULT 0,  -- Trading volume for this trader
    pnl NUMERIC(24, 6) NOT NULL DEFAULT 0,  -- Profit and loss for this trader
    
    -- Profile information
    profile_image TEXT,  -- URL to the trader's profile image
    x_username VARCHAR(255),  -- The trader's X (Twitter) username
    verified_badge BOOLEAN DEFAULT FALSE,  -- Whether the trader has a verified badge
    
    -- Query parameters (what was requested)
    category VARCHAR(20) NOT NULL,  -- OVERALL, POLITICS, SPORTS, CRYPTO, CULTURE, MENTIONS, WEATHER, ECONOMICS, TECH, FINANCE
    time_period VARCHAR(10) NOT NULL,  -- DAY, WEEK, MONTH, ALL
    order_by VARCHAR(10) NOT NULL,  -- PNL, VOL
    
    -- Metadata
    fetched_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    fetched_date DATE NOT NULL DEFAULT CURRENT_DATE,  -- Date portion of fetched_at (for uniqueness)
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT trader_leaderboard_proxy_wallet_check CHECK (proxy_wallet ~* '^0x[a-f0-9]{40}$'),
    CONSTRAINT trader_leaderboard_category_check CHECK (category IN ('OVERALL', 'POLITICS', 'SPORTS', 'CRYPTO', 'CULTURE', 'MENTIONS', 'WEATHER', 'ECONOMICS', 'TECH', 'FINANCE')),
    CONSTRAINT trader_leaderboard_time_period_check CHECK (time_period IN ('DAY', 'WEEK', 'MONTH', 'ALL')),
    CONSTRAINT trader_leaderboard_order_by_check CHECK (order_by IN ('PNL', 'VOL'))
);

-- ==========================================
-- INDEXES
-- ==========================================

-- Unique index: one entry per (wallet, category, period, order_by) per day
CREATE UNIQUE INDEX IF NOT EXISTS idx_trader_leaderboard_unique_entry
    ON public.trader_leaderboard(proxy_wallet, category, time_period, order_by, fetched_date);

-- Index for querying by wallet
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_proxy_wallet 
    ON public.trader_leaderboard(proxy_wallet);

-- Index for querying by rank
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_rank 
    ON public.trader_leaderboard(rank) WHERE rank IS NOT NULL;

-- Index for querying by category and time period
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_category_period 
    ON public.trader_leaderboard(category, time_period, order_by);

-- Index for querying by username
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_user_name 
    ON public.trader_leaderboard(user_name) WHERE user_name IS NOT NULL;

-- Index for fetched_at (for time-series queries)
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_fetched_at 
    ON public.trader_leaderboard(fetched_at DESC);

-- Composite index for common queries (category + period + rank)
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_category_period_rank 
    ON public.trader_leaderboard(category, time_period, rank) WHERE rank IS NOT NULL;

-- Index for volume and PnL queries
CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_vol 
    ON public.trader_leaderboard(vol DESC);

CREATE INDEX IF NOT EXISTS idx_trader_leaderboard_pnl 
    ON public.trader_leaderboard(pnl DESC);

-- ==========================================
-- COMMENTS
-- ==========================================

COMMENT ON TABLE public.trader_leaderboard IS 'Trader leaderboard rankings from Polymarket API';

COMMENT ON COLUMN public.trader_leaderboard.rank IS 'The rank position of the trader';
COMMENT ON COLUMN public.trader_leaderboard.proxy_wallet IS 'User Profile Address (0x-prefixed, 40 hex chars)';
COMMENT ON COLUMN public.trader_leaderboard.user_name IS 'The trader''s username on Polymarket';
COMMENT ON COLUMN public.trader_leaderboard.vol IS 'Trading volume for this trader';
COMMENT ON COLUMN public.trader_leaderboard.pnl IS 'Profit and loss for this trader';
COMMENT ON COLUMN public.trader_leaderboard.profile_image IS 'URL to the trader''s profile image';
COMMENT ON COLUMN public.trader_leaderboard.x_username IS 'The trader''s X (Twitter) username';
COMMENT ON COLUMN public.trader_leaderboard.verified_badge IS 'Whether the trader has a verified badge';
COMMENT ON COLUMN public.trader_leaderboard.category IS 'Market category (OVERALL, POLITICS, SPORTS, CRYPTO, CULTURE, MENTIONS, WEATHER, ECONOMICS, TECH, FINANCE)';
COMMENT ON COLUMN public.trader_leaderboard.time_period IS 'Time period for leaderboard (DAY, WEEK, MONTH, ALL)';
COMMENT ON COLUMN public.trader_leaderboard.order_by IS 'Ordering criteria (PNL, VOL)';
COMMENT ON COLUMN public.trader_leaderboard.fetched_at IS 'When this data was fetched from the API';
COMMENT ON COLUMN public.trader_leaderboard.fetched_date IS 'Date portion of fetched_at (used for uniqueness constraint)';

-- ==========================================
-- EXAMPLE QUERIES
-- ==========================================

-- Top 10 traders by PnL today (OVERALL category)
-- SELECT rank, user_name, proxy_wallet, pnl, vol
-- FROM public.trader_leaderboard
-- WHERE category = 'OVERALL' 
--   AND time_period = 'DAY' 
--   AND order_by = 'PNL'
--   AND fetched_at::date = CURRENT_DATE
-- ORDER BY rank
-- LIMIT 10;

-- Track a specific trader's performance over time
-- SELECT category, time_period, rank, pnl, vol, fetched_at
-- FROM public.trader_leaderboard
-- WHERE proxy_wallet = '0x...'
-- ORDER BY fetched_at DESC;

-- Top 20 traders in Politics by volume this week
-- SELECT rank, user_name, vol, pnl
-- FROM public.trader_leaderboard
-- WHERE category = 'POLITICS' 
--   AND time_period = 'WEEK' 
--   AND order_by = 'VOL'
--   AND fetched_at::date = CURRENT_DATE
-- ORDER BY rank
-- LIMIT 20;

-- Compare traders across all categories
-- SELECT category, rank, pnl, vol
-- FROM public.trader_leaderboard
-- WHERE proxy_wallet = '0x...'
--   AND time_period = 'WEEK'
--   AND fetched_at::date = CURRENT_DATE
-- ORDER BY category;

GRANT SELECT, INSERT, UPDATE ON public.trader_leaderboard TO PUBLIC;
GRANT USAGE, SELECT ON SEQUENCE trader_leaderboard_id_seq TO PUBLIC;

-- Run VACUUM manually after table creation (cannot run inside transaction):
-- VACUUM ANALYZE public.trader_leaderboard;
