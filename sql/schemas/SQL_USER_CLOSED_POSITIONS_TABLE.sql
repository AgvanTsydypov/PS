-- ============================================================
-- USER CLOSED POSITIONS TABLE - SQL Schema for Supabase
-- ============================================================
-- Run this in Supabase SQL Editor to create the table

-- Create main table
CREATE TABLE IF NOT EXISTS public.user_closed_positions (
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
    timestamp_human TIMESTAMP WITH TIME ZONE,
    
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
    end_date_parsed TIMESTAMP WITH TIME ZONE,
    
    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Ensure unique positions per user/condition
    CONSTRAINT unique_user_position UNIQUE(proxy_wallet, condition_id, asset, timestamp_unix)
);

-- Create indexes for performance
CREATE INDEX idx_user_closed_positions_wallet ON public.user_closed_positions(proxy_wallet);
CREATE INDEX idx_user_closed_positions_event_id ON public.user_closed_positions(event_id);
CREATE INDEX idx_user_closed_positions_market_id ON public.user_closed_positions(market_id);
CREATE INDEX idx_user_closed_positions_condition ON public.user_closed_positions(condition_id);
CREATE INDEX idx_user_closed_positions_timestamp ON public.user_closed_positions(timestamp_unix DESC);
CREATE INDEX idx_user_closed_positions_realized_pnl ON public.user_closed_positions(realized_pnl DESC);
CREATE INDEX idx_user_closed_positions_event_slug ON public.user_closed_positions(event_slug);
CREATE INDEX idx_user_closed_positions_outcome ON public.user_closed_positions(outcome_index);

-- Enable Row Level Security
ALTER TABLE public.user_closed_positions ENABLE ROW LEVEL SECURITY;

-- Create policy for public read access
CREATE POLICY "Allow public read access" 
ON public.user_closed_positions FOR SELECT TO public USING (true);

-- Optional: Create policy for wallet-specific write access
-- CREATE POLICY "Allow users to insert their own positions"
-- ON public.user_closed_positions FOR INSERT 
-- WITH CHECK (proxy_wallet = current_user);

-- Add comments for documentation
COMMENT ON TABLE public.user_closed_positions IS 'Closed trading positions for Polymarket users';
COMMENT ON COLUMN public.user_closed_positions.proxy_wallet IS 'User proxy wallet address';
COMMENT ON COLUMN public.user_closed_positions.event_id IS 'Event ID associated with the position';
COMMENT ON COLUMN public.user_closed_positions.market_id IS 'Market ID associated with the position';
COMMENT ON COLUMN public.user_closed_positions.condition_id IS 'Condition ID of the market';
COMMENT ON COLUMN public.user_closed_positions.avg_price IS 'Average purchase price';
COMMENT ON COLUMN public.user_closed_positions.total_bought IS 'Total amount bought';
COMMENT ON COLUMN public.user_closed_positions.realized_pnl IS 'Realized profit and loss';
COMMENT ON COLUMN public.user_closed_positions.cur_price IS 'Current price at time of closure';
COMMENT ON COLUMN public.user_closed_positions.outcome_index IS 'Index of the outcome (0 or 1)';

-- ============================================================
-- OPTIONAL: Foreign Key Constraints
-- ============================================================
-- Uncomment if you want to enforce referential integrity:

-- Link to events table if available
-- ALTER TABLE public.user_closed_positions 
-- ADD CONSTRAINT fk_condition_to_market 
-- FOREIGN KEY (condition_id) 
-- REFERENCES public.markets(condition_id) 
-- ON DELETE SET NULL;

-- ============================================================
-- USEFUL VIEWS FOR ANALYTICS
-- ============================================================

-- View: User PnL Summary
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
FROM public.user_closed_positions
GROUP BY proxy_wallet;

-- View: Top Profitable Users
CREATE OR REPLACE VIEW top_profitable_users AS
SELECT 
    proxy_wallet,
    SUM(realized_pnl) as total_pnl,
    COUNT(*) as position_count,
    AVG(realized_pnl) as avg_pnl
FROM public.user_closed_positions
GROUP BY proxy_wallet
ORDER BY total_pnl DESC
LIMIT 100;

-- View: Recent Closed Positions
CREATE OR REPLACE VIEW recent_closed_positions AS
SELECT 
    proxy_wallet,
    title,
    outcome,
    realized_pnl,
    timestamp_unix,
    timestamp_human
FROM public.user_closed_positions
ORDER BY timestamp_unix DESC
LIMIT 100;

-- ============================================================
-- GRANT PERMISSIONS
-- ============================================================
-- Grant permissions to authenticated users (adjust based on your needs)
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO authenticated;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO anon;
