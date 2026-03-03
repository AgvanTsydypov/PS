-- ============================================================================
-- POLYSTARS SEASONS SYSTEM - SQL Migration
-- ============================================================================
-- This migration creates tables for NFT minting seasons system
-- Supports Genesis (historical) and Standard (10-day) seasons
-- ============================================================================

DO $$ BEGIN RAISE NOTICE '🎮 Starting PolyStars Seasons System migration...'; END $$;

-- ============================================================================
-- 1. CREATE SEASON TYPE ENUM
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '📋 Creating season type enum...'; END $$;

DO $$ BEGIN
    CREATE TYPE season_type AS ENUM ('genesis', 'standard');
    RAISE NOTICE '✅ Season type enum created';
EXCEPTION
    WHEN duplicate_object THEN
        RAISE NOTICE '⚠️  Season type enum already exists, skipping';
END $$;

-- ============================================================================
-- 2. CREATE PHASE TYPE ENUM
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '📋 Creating phase type enum...'; END $$;

DO $$ BEGIN
    CREATE TYPE phase_type AS ENUM ('vault', 'public');
    RAISE NOTICE '✅ Phase type enum created';
EXCEPTION
    WHEN duplicate_object THEN
        RAISE NOTICE '⚠️  Phase type enum already exists, skipping';
END $$;

-- ============================================================================
-- 3. SEASONS TABLE - Main seasons configuration
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🎯 Creating seasons table...'; END $$;

CREATE TABLE IF NOT EXISTS seasons (
    id SERIAL PRIMARY KEY,
    
    -- Season identification
    type season_type NOT NULL,
    season_number INTEGER NOT NULL,
    
    -- Date range
    start_date TIMESTAMPTZ NOT NULL,
    end_date TIMESTAMPTZ NOT NULL,
    
    -- NFT supply management
    total_supply INTEGER NOT NULL,
    remaining_supply INTEGER NOT NULL,
    
    -- Status flags
    is_active BOOLEAN DEFAULT FALSE,
    is_completed BOOLEAN DEFAULT FALSE,
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT seasons_supply_check CHECK (remaining_supply >= 0 AND remaining_supply <= total_supply),
    CONSTRAINT seasons_dates_check CHECK (end_date > start_date),
    CONSTRAINT seasons_unique_season UNIQUE(type, season_number)
);

-- Seasons indexes
CREATE INDEX IF NOT EXISTS idx_seasons_type ON seasons(type);
CREATE INDEX IF NOT EXISTS idx_seasons_active ON seasons(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_seasons_dates ON seasons(start_date, end_date);
CREATE INDEX IF NOT EXISTS idx_seasons_completed ON seasons(is_completed);
CREATE INDEX IF NOT EXISTS idx_seasons_type_number ON seasons(type, season_number);

-- Add comments
COMMENT ON TABLE seasons IS 'NFT minting seasons configuration - supports Genesis and Standard (10-day) seasons';
COMMENT ON COLUMN seasons.type IS 'Season type: genesis (unlimited time) or standard (10 days)';
COMMENT ON COLUMN seasons.season_number IS 'Sequential season number within its type';
COMMENT ON COLUMN seasons.total_supply IS 'Total NFTs available for this season';
COMMENT ON COLUMN seasons.remaining_supply IS 'NFTs still available to mint';
COMMENT ON COLUMN seasons.is_active IS 'Whether this season is currently accepting claims';

DO $$ BEGIN RAISE NOTICE '✅ Seasons table created'; END $$;

-- ============================================================================
-- 4. CLAIMS TABLE - Track all NFT claims
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🎫 Creating claims table...'; END $$;

CREATE TABLE IF NOT EXISTS claims (
    id BIGSERIAL PRIMARY KEY,
    
    -- User identification
    user_wallet VARCHAR(42) NOT NULL,
    
    -- Claim details
    season_id INTEGER NOT NULL,
    phase_type phase_type NOT NULL,
    
    -- Transaction tracking
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tx_hash VARCHAR(66),
    
    -- NFT metadata
    token_id INTEGER,
    metadata_uri TEXT,
    
    -- Status tracking
    status VARCHAR(20) DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')),
    error_message TEXT,
    
    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Foreign key to seasons
    CONSTRAINT fk_season FOREIGN KEY (season_id) REFERENCES seasons(id) ON DELETE CASCADE,
    
    -- User can only claim once per season
    CONSTRAINT unique_user_season_claim UNIQUE(user_wallet, season_id),
    
    -- Wallet address validation
    CONSTRAINT claims_wallet_check CHECK (user_wallet ~* '^0x[a-f0-9]{40}$')
);

-- Claims indexes
CREATE INDEX IF NOT EXISTS idx_claims_user_wallet ON claims(user_wallet);
CREATE INDEX IF NOT EXISTS idx_claims_season_id ON claims(season_id);
CREATE INDEX IF NOT EXISTS idx_claims_phase_type ON claims(phase_type);
CREATE INDEX IF NOT EXISTS idx_claims_timestamp ON claims(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_claims_tx_hash ON claims(tx_hash) WHERE tx_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_claims_season_phase ON claims(season_id, phase_type);

-- Add comments
COMMENT ON TABLE claims IS 'NFT claims for each season - tracks all mint requests';
COMMENT ON COLUMN claims.user_wallet IS 'Ethereum wallet address of the claimant';
COMMENT ON COLUMN claims.season_id IS 'Reference to the season being claimed';
COMMENT ON COLUMN claims.phase_type IS 'Claim phase: vault (Origins only) or public';
COMMENT ON COLUMN claims.tx_hash IS 'Blockchain transaction hash for the mint';
COMMENT ON COLUMN claims.token_id IS 'Minted NFT token ID';

DO $$ BEGIN RAISE NOTICE '✅ Claims table created'; END $$;

-- ============================================================================
-- 5. SEASON EVENTS LOG TABLE
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '📝 Creating season_events_log table...'; END $$;

CREATE TABLE IF NOT EXISTS season_events_log (
    id BIGSERIAL PRIMARY KEY,
    event_name TEXT NOT NULL,
    season_id INTEGER,
    details TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_season_events_log_season
        FOREIGN KEY (season_id) REFERENCES seasons(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_season_events_log_created_at
    ON season_events_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_season_events_log_event_name
    ON season_events_log(event_name);
CREATE INDEX IF NOT EXISTS idx_season_events_log_season_id
    ON season_events_log(season_id);

COMMENT ON TABLE season_events_log IS 'Technical lifecycle logs for seasons (hard stop, ghost state, season rotation)';
COMMENT ON COLUMN season_events_log.event_name IS 'Machine-readable event key (e.g. hard_stop_burn)';
COMMENT ON COLUMN season_events_log.details IS 'Human-readable event details';

DO $$ BEGIN RAISE NOTICE '✅ season_events_log table created'; END $$;

-- ============================================================================
-- 6. VIEW: ORIGIN WALLETS (Vault Phase Eligibility)
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🏛️  Creating v_origin_wallets view...'; END $$;

CREATE OR REPLACE VIEW v_origin_wallets AS
WITH pnl_30d AS (
    SELECT
        LOWER(proxy_wallet) AS wallet_address,
        SUM(realized_pnl) AS total_pnl_30d
    FROM user_closed_positions
    WHERE proxy_wallet IS NOT NULL
      AND COALESCE(
            end_date_parsed,
            timestamp_human,
            TO_TIMESTAMP(timestamp_unix)
          ) >= NOW() - INTERVAL '30 days'
    GROUP BY LOWER(proxy_wallet)
    HAVING SUM(realized_pnl) > 0
),
ranked AS (
    SELECT
        wallet_address,
        total_pnl_30d,
        ROW_NUMBER() OVER (ORDER BY total_pnl_30d DESC, wallet_address ASC) AS pnl_rank
    FROM pnl_30d
),
enough_data AS (
    SELECT COUNT(*) AS wallet_count
    FROM ranked
)
SELECT
    r.wallet_address,
    'top_pnl_30d'::TEXT AS source,
    r.total_pnl_30d,
    r.pnl_rank
FROM ranked r
CROSS JOIN enough_data d
WHERE d.wallet_count >= 10
  AND r.pnl_rank <= 10
ORDER BY r.pnl_rank;

-- Add comment
COMMENT ON VIEW v_origin_wallets IS 'Origins list for Vault phase: top-10 wallets by positive realized PnL over the last 30 days from user_closed_positions. Returns empty set when fewer than 10 eligible wallets exist.';

DO $$ BEGIN RAISE NOTICE '✅ v_origin_wallets view created'; END $$;

-- ============================================================================
-- 7. HELPER VIEWS FOR ANALYTICS
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '📊 Creating analytics views...'; END $$;

-- Active seasons summary
CREATE OR REPLACE VIEW v_active_seasons AS
SELECT 
    s.id,
    s.type,
    s.season_number,
    s.start_date,
    s.end_date,
    s.total_supply,
    s.remaining_supply,
    s.total_supply - s.remaining_supply as claimed_count,
    ROUND(((s.total_supply - s.remaining_supply)::NUMERIC / s.total_supply::NUMERIC) * 100, 2) as claim_percentage,
    COUNT(DISTINCT c.user_wallet) as unique_claimants,
    COUNT(c.id) FILTER (WHERE c.phase_type = 'vault') as vault_claims,
    COUNT(c.id) FILTER (WHERE c.phase_type = 'public') as public_claims,
    COUNT(c.id) FILTER (WHERE c.status = 'COMPLETED') as completed_claims,
    COUNT(c.id) FILTER (WHERE c.status = 'PENDING') as pending_claims
FROM seasons s
LEFT JOIN claims c ON s.id = c.season_id
WHERE s.is_active = TRUE
GROUP BY s.id;

COMMENT ON VIEW v_active_seasons IS 'Summary of currently active seasons with claim statistics';

-- Season leaderboard (first claimers)
CREATE OR REPLACE VIEW v_season_leaderboard AS
SELECT 
    s.id as season_id,
    s.type as season_type,
    s.season_number,
    c.user_wallet,
    c.phase_type,
    c.timestamp as claim_timestamp,
    c.token_id,
    ROW_NUMBER() OVER (PARTITION BY s.id ORDER BY c.timestamp) as claim_rank
FROM seasons s
INNER JOIN claims c ON s.id = c.season_id
WHERE c.status = 'COMPLETED'
ORDER BY s.id, c.timestamp;

COMMENT ON VIEW v_season_leaderboard IS 'Ranking of claims by timestamp within each season';

-- User claim history
CREATE OR REPLACE VIEW v_user_claim_history AS
SELECT 
    c.user_wallet,
    COUNT(*) as total_claims,
    COUNT(*) FILTER (WHERE c.phase_type = 'vault') as vault_claims,
    COUNT(*) FILTER (WHERE c.phase_type = 'public') as public_claims,
    COUNT(DISTINCT c.season_id) as seasons_participated,
    MIN(c.timestamp) as first_claim,
    MAX(c.timestamp) as last_claim,
    ARRAY_AGG(DISTINCT s.type ORDER BY s.type) as season_types_claimed
FROM claims c
INNER JOIN seasons s ON c.season_id = s.id
WHERE c.status = 'COMPLETED'
GROUP BY c.user_wallet;

COMMENT ON VIEW v_user_claim_history IS 'Per-user statistics across all seasons';

-- Origins eligibility check
CREATE OR REPLACE VIEW v_origins_eligibility AS
SELECT 
    vo.wallet_address,
    vo.source,
    CASE 
        WHEN c.user_wallet IS NOT NULL THEN TRUE
        ELSE FALSE
    END as has_claimed,
    c.season_id as claimed_season_id,
    c.timestamp as claim_timestamp
FROM v_origin_wallets vo
LEFT JOIN claims c ON LOWER(vo.wallet_address) = LOWER(c.user_wallet) 
    AND c.phase_type = 'vault'
    AND c.status = 'COMPLETED';

COMMENT ON VIEW v_origins_eligibility IS 'Shows which Origins wallets have claimed their Vault phase NFTs';

DO $$ BEGIN RAISE NOTICE '✅ Analytics views created'; END $$;

-- ============================================================================
-- 8. HELPER FUNCTIONS
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '⚙️  Creating helper functions...'; END $$;

-- Function to check if wallet is an Origin
CREATE OR REPLACE FUNCTION is_origin_wallet(wallet_addr TEXT)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM v_origin_wallets 
        WHERE LOWER(wallet_address) = LOWER(wallet_addr)
    );
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION is_origin_wallet IS 'Check if a wallet address is eligible for Vault phase (Origin)';

-- Function to get active season for claiming
CREATE OR REPLACE FUNCTION get_active_season()
RETURNS TABLE(
    season_id INTEGER,
    season_type season_type,
    season_number INTEGER,
    start_date TIMESTAMPTZ,
    end_date TIMESTAMPTZ,
    remaining_supply INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        s.id,
        s.type,
        s.season_number,
        s.start_date,
        s.end_date,
        s.remaining_supply
    FROM seasons s
    WHERE s.is_active = TRUE
        AND s.remaining_supply > 0
        AND NOW() BETWEEN s.start_date AND s.end_date
    ORDER BY s.start_date
    LIMIT 1;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION get_active_season IS 'Get the currently active season available for claims';

-- Function to update season supply after claim
CREATE OR REPLACE FUNCTION decrement_season_supply()
RETURNS TRIGGER AS $$
BEGIN
    -- Only decrement if claim is completed
    IF NEW.status = 'COMPLETED' AND (OLD.status IS NULL OR OLD.status != 'COMPLETED') THEN
        UPDATE seasons
        SET 
            remaining_supply = remaining_supply - 1,
            updated_at = NOW()
        WHERE id = NEW.season_id
            AND remaining_supply > 0;
        
        -- Check if season is now sold out
        IF (SELECT remaining_supply FROM seasons WHERE id = NEW.season_id) = 0 THEN
            UPDATE seasons
            SET 
                is_active = FALSE,
                is_completed = TRUE,
                updated_at = NOW()
            WHERE id = NEW.season_id;
        END IF;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to automatically update season supply
DROP TRIGGER IF EXISTS trigger_update_season_supply ON claims;
CREATE TRIGGER trigger_update_season_supply
AFTER INSERT OR UPDATE OF status ON claims
FOR EACH ROW
EXECUTE FUNCTION decrement_season_supply();

COMMENT ON FUNCTION decrement_season_supply IS 'Automatically decrements season supply when a claim is completed';

DO $$ BEGIN RAISE NOTICE '✅ Helper functions and triggers created'; END $$;

-- ============================================================================
-- 9. EXAMPLE DATA (Optional - for testing)
-- ============================================================================
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '💡 Example: Insert a Genesis season'; END $$;
DO $$ BEGIN RAISE NOTICE '   INSERT INTO seasons (type, season_number, start_date, end_date, total_supply, remaining_supply, is_active)'; END $$;
DO $$ BEGIN RAISE NOTICE '   VALUES (''genesis'', 1, ''2024-06-01'', ''2026-12-31'', 10000, 10000, TRUE);'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '💡 Example: Insert a Standard season'; END $$;
DO $$ BEGIN RAISE NOTICE '   INSERT INTO seasons (type, season_number, start_date, end_date, total_supply, remaining_supply, is_active)'; END $$;
DO $$ BEGIN RAISE NOTICE '   VALUES (''standard'', 1, NOW(), NOW() + INTERVAL ''10 days'', 1000, 1000, TRUE);'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;

-- ============================================================================
-- 10. STATISTICS SUMMARY
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '📊 Getting Origins count...'; END $$;

DO $$
DECLARE
    origins_count INTEGER;
BEGIN
    SELECT COUNT(DISTINCT wallet_address) INTO origins_count
    FROM v_origin_wallets;
    
    RAISE NOTICE '✅ Total Origins wallets: %', origins_count;
END $$;

-- ============================================================================
-- COMPLETION
-- ============================================================================
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '✅ PolyStars Seasons System migration complete!'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '📦 Created:'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Enums: season_type, phase_type'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Tables: seasons, claims, season_events_log'; END $$;
DO $$ BEGIN RAISE NOTICE '   - View: v_origin_wallets'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Analytics views: v_active_seasons, v_season_leaderboard, v_user_claim_history, v_origins_eligibility'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Functions: is_origin_wallet(), get_active_season(), decrement_season_supply()'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Trigger: trigger_update_season_supply'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '🎮 Ready to start seasons!'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
