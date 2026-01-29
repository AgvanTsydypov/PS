-- ============================================================
-- REDEMPTIONS TABLE - SQL Schema for Supabase
-- ============================================================
-- Run this in Supabase SQL Editor to create the table

-- Create main table
CREATE TABLE IF NOT EXISTS public.redemptions (
    id BIGSERIAL PRIMARY KEY,
    transaction_hash TEXT NOT NULL,
    condition_id TEXT,
    event_id TEXT,
    market_id TEXT,
    redeemer_address TEXT NOT NULL,
    payout_usdc NUMERIC(20, 6) NOT NULL DEFAULT 0,
    timestamp_unix BIGINT NOT NULL,
    timestamp_human TIMESTAMP WITH TIME ZONE,
    market_question TEXT,
    event_title TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT unique_redemption UNIQUE(transaction_hash, redeemer_address)
);

-- Create indexes for performance
CREATE INDEX idx_redemptions_condition_id ON public.redemptions(condition_id);
CREATE INDEX idx_redemptions_event_id ON public.redemptions(event_id);
CREATE INDEX idx_redemptions_market_id ON public.redemptions(market_id);
CREATE INDEX idx_redemptions_redeemer ON public.redemptions(redeemer_address);
CREATE INDEX idx_redemptions_timestamp ON public.redemptions(timestamp_unix DESC);
CREATE INDEX idx_redemptions_payout ON public.redemptions(payout_usdc DESC);

-- Enable Row Level Security
ALTER TABLE public.redemptions ENABLE ROW LEVEL SECURITY;

-- Create policy for public read access
CREATE POLICY "Allow public read access" 
ON public.redemptions FOR SELECT TO public USING (true);

-- Add comments for documentation
COMMENT ON TABLE public.redemptions IS 'Redemption events from Polymarket - when users claim their winnings';
COMMENT ON COLUMN public.redemptions.event_id IS 'Foreign key to events table';
COMMENT ON COLUMN public.redemptions.market_id IS 'Foreign key to markets table';

-- ============================================================
-- OPTIONAL: Foreign Key Constraints
-- ============================================================
-- Uncomment if you want to enforce referential integrity:

-- ALTER TABLE public.redemptions 
-- ADD CONSTRAINT fk_event 
-- FOREIGN KEY (event_id) 
-- REFERENCES public.events(id) 
-- ON DELETE CASCADE;

-- ALTER TABLE public.redemptions 
-- ADD CONSTRAINT fk_market 
-- FOREIGN KEY (market_id) 
-- REFERENCES public.markets(id) 
-- ON DELETE CASCADE;
