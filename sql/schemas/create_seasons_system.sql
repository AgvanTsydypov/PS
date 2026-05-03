-- ============================================================================
-- POLYSTARS SEASONS SYSTEM - SQL Migration
-- ============================================================================
-- This migration creates tables for NFT minting seasons system
-- Supports Genesis (historical) and Standard (10-day) seasons
--
-- Prerequisite: run sql/schemas/init-db.sql first. It defines the
-- participants materialized view (including columns such as rarity_bracket)
-- that this file references when backfilling winner_wallets_nft_to_claim.
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
    CREATE TYPE phase_type AS ENUM ('breach', 'vault', 'scavenge');
    RAISE NOTICE '✅ Phase type enum created';
EXCEPTION
    WHEN duplicate_object THEN
        RAISE NOTICE '⚠️  Phase type enum already exists, checking compatibility';
END $$;

-- Ensure enum contains new values for upgraded databases.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'phase_type' AND e.enumlabel = 'breach'
    ) THEN
        ALTER TYPE phase_type ADD VALUE 'breach';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'phase_type' AND e.enumlabel = 'scavenge'
    ) THEN
        ALTER TYPE phase_type ADD VALUE 'scavenge';
    END IF;
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
    asset_address TEXT,
    
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
    CONSTRAINT claims_wallet_check CHECK (
        user_wallet ~* '^0x[a-f0-9]{40}$'
        OR user_wallet ~ '^[1-9A-HJ-NP-Za-km-z]{32,44}$'
    )
);

-- Claims indexes
CREATE INDEX IF NOT EXISTS idx_claims_user_wallet ON claims(user_wallet);
CREATE INDEX IF NOT EXISTS idx_claims_season_id ON claims(season_id);
CREATE INDEX IF NOT EXISTS idx_claims_phase_type ON claims(phase_type);
CREATE INDEX IF NOT EXISTS idx_claims_timestamp ON claims(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_claims_tx_hash ON claims(tx_hash) WHERE tx_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_claims_asset_address ON claims(asset_address) WHERE asset_address IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_claims_season_phase ON claims(season_id, phase_type);
CREATE UNIQUE INDEX IF NOT EXISTS ux_claims_active_season_user_wallet_lower
    ON claims(season_id, LOWER(user_wallet))
    WHERE status IN ('PENDING', 'PROCESSING', 'COMPLETED');

-- Per-season collection mint # on claims (1..N within each season_id), mirroring preview_cards.
DO $$
BEGIN
    IF to_regclass('public.claims') IS NULL THEN
        RETURN;
    END IF;

    ALTER TABLE claims
        ADD COLUMN IF NOT EXISTS collection_mint_number BIGINT;

    ALTER TABLE claims
        ALTER COLUMN collection_mint_number DROP DEFAULT;

    -- Backfill any existing rows that predate this column, numbering within each season
    -- by chronological order so current production data keeps deterministic ordering.
    WITH numbered AS (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY season_id
                ORDER BY COALESCE(timestamp, created_at) ASC, id ASC
            ) AS rn
        FROM claims
        WHERE collection_mint_number IS NULL
    )
    UPDATE claims c
    SET collection_mint_number = n.rn
    FROM numbered n
    WHERE c.id = n.id;

    CREATE UNIQUE INDEX IF NOT EXISTS ux_claims_season_collection_mint
        ON claims(season_id, collection_mint_number);
END $$;

CREATE OR REPLACE FUNCTION claims_assign_season_mint_number()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.collection_mint_number IS NOT NULL THEN
        RETURN NEW;
    END IF;
    -- Distinct namespace from preview_cards (9283741) to avoid cross-lock contention.
    PERFORM pg_advisory_xact_lock(9283742, NEW.season_id);
    SELECT COALESCE(MAX(collection_mint_number), 0) + 1
    INTO NEW.collection_mint_number
    FROM claims
    WHERE season_id = NEW.season_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tr_claims_assign_season_mint ON claims;
CREATE TRIGGER tr_claims_assign_season_mint
    BEFORE INSERT ON claims
    FOR EACH ROW
    EXECUTE PROCEDURE claims_assign_season_mint_number();

-- Add comments
COMMENT ON TABLE claims IS 'NFT claims for each season - tracks all mint requests';
COMMENT ON COLUMN claims.collection_mint_number IS 'Per-season sequential mint number (1..N within each season_id); assigned by trigger';
COMMENT ON COLUMN claims.user_wallet IS 'Ethereum wallet address of the claimant';
COMMENT ON COLUMN claims.season_id IS 'Reference to the season being claimed';
COMMENT ON COLUMN claims.phase_type IS 'Claim phase: breach, vault (Origins only), or scavenge';
COMMENT ON COLUMN claims.tx_hash IS 'Blockchain transaction hash for the mint';
COMMENT ON COLUMN claims.token_id IS 'Minted NFT token ID';
COMMENT ON COLUMN claims.asset_address IS 'Minted NFT asset address (contract/tokenId)';

DO $$ BEGIN RAISE NOTICE '✅ Claims table created'; END $$;

-- Migrate legacy enum values (public -> scavenge) in existing databases.
DO $$
DECLARE
    has_public_label BOOLEAN;
    has_claims_table BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'phase_type' AND e.enumlabel = 'public'
    ) INTO has_public_label;

    IF NOT has_public_label THEN
        RETURN;
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'claims'
    ) INTO has_claims_table;

    IF has_claims_table THEN
        -- Drop dependent views before changing enum-backed column type.
        -- They are recreated later in this migration.
        DROP VIEW IF EXISTS v_origins_eligibility;
        DROP VIEW IF EXISTS v_user_claim_history;
        DROP VIEW IF EXISTS v_season_leaderboard;
        DROP VIEW IF EXISTS v_active_seasons;

        ALTER TABLE claims ALTER COLUMN phase_type TYPE TEXT USING phase_type::TEXT;
        UPDATE claims
        SET phase_type = 'scavenge'
        WHERE phase_type = 'public';
        DROP TYPE phase_type;
        CREATE TYPE phase_type AS ENUM ('breach', 'vault', 'scavenge');
        ALTER TABLE claims ALTER COLUMN phase_type TYPE phase_type USING phase_type::phase_type;
        RAISE NOTICE '✅ phase_type enum migrated: public -> scavenge';
    ELSE
        DROP TYPE phase_type;
        CREATE TYPE phase_type AS ENUM ('breach', 'vault', 'scavenge');
        RAISE NOTICE '✅ phase_type enum recreated with new values';
    END IF;
END $$;

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
-- 6. LEGACY winner_wallets_nft_to_claim REMOVAL
-- ============================================================================
-- The queue model writes the full participant snapshot directly onto ``claims``
-- at QUEUED time, so the per-season frozen Origins table is no longer needed.
-- Drop dependent views first (they're recreated below from participants when
-- still useful), then drop the table itself. Idempotent.
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🗑️  Dropping legacy winner_wallets_nft_to_claim...'; END $$;

DROP VIEW IF EXISTS v_origins_eligibility;
DROP VIEW IF EXISTS v_origin_wallets;
DROP TABLE IF EXISTS winner_wallets_nft_to_claim CASCADE;

DO $$ BEGIN RAISE NOTICE '✅ winner_wallets_nft_to_claim dropped'; END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- ``preview_cards`` — home-showcase/preview buffer for unminted cards.
--
-- Historically this was ``user_generated_cards`` and quietly dual-roled as the
-- canonical store for minted STAR pages. Stage 2 of the refactor moved minted
-- card data onto ``claims`` (denormalized card fields), turned this table into
-- a strict preview-only buffer, and ``promote_preview_to_claim`` now DELETEs a
-- row from here on mint. The rename to ``preview_cards`` aligns the table
-- name with its actual responsibility (and with the ``/api/preview/{slug}``
-- endpoint that reads it).
-- ─────────────────────────────────────────────────────────────────────────────

-- Idempotent rename from the legacy name. Safe to re-run: it only renames
-- when the legacy table is still around and the new name is still free.
DO $$
BEGIN
    IF to_regclass('public.user_generated_cards') IS NOT NULL
       AND to_regclass('public.preview_cards') IS NULL THEN
        ALTER TABLE user_generated_cards RENAME TO preview_cards;
    END IF;
END $$;

-- Rename any legacy indexes that carried over from the old table name.
--
-- ``ALTER INDEX ... RENAME TO`` has no ``IF NOT EXISTS`` on the target, so
-- a naive rename blows up when the new-name index has already been created
-- by a prior partial run (e.g. the backend's ``_ensure_preview_cards_schema``
-- bootstrap ran ``CREATE INDEX IF NOT EXISTS idx_preview_cards_...`` next
-- to the still-present legacy index). The DO-block below handles both
-- orderings: if the target is free we rename, otherwise we just drop the
-- legacy duplicate so only the canonical name survives.
DO $$
BEGIN
    IF to_regclass('public.idx_preview_cards_owner_wallet_lower') IS NOT NULL THEN
        DROP INDEX IF EXISTS idx_generated_cards_owner_wallet_lower;
    ELSIF to_regclass('public.idx_generated_cards_owner_wallet_lower') IS NOT NULL THEN
        ALTER INDEX idx_generated_cards_owner_wallet_lower
            RENAME TO idx_preview_cards_owner_wallet_lower;
    END IF;

    IF to_regclass('public.idx_preview_cards_created_at') IS NOT NULL THEN
        DROP INDEX IF EXISTS idx_generated_cards_created_at;
    ELSIF to_regclass('public.idx_generated_cards_created_at') IS NOT NULL THEN
        ALTER INDEX idx_generated_cards_created_at
            RENAME TO idx_preview_cards_created_at;
    END IF;

    IF to_regclass('public.ux_preview_cards_season_collection_mint') IS NOT NULL THEN
        DROP INDEX IF EXISTS ux_user_generated_cards_season_collection_mint;
    ELSIF to_regclass('public.ux_user_generated_cards_season_collection_mint') IS NOT NULL THEN
        ALTER INDEX ux_user_generated_cards_season_collection_mint
            RENAME TO ux_preview_cards_season_collection_mint;
    END IF;
END $$;

-- Drop the legacy trigger+function so the definitions below can reintroduce
-- them under the new names without leaving stale duplicates behind.
DO $$
BEGIN
    IF to_regclass('public.preview_cards') IS NOT NULL THEN
        EXECUTE 'DROP TRIGGER IF EXISTS tr_user_generated_cards_assign_season_mint ON preview_cards';
    END IF;
END $$;
DROP FUNCTION IF EXISTS user_generated_cards_assign_season_mint_number();

-- Drop legacy FK + column on existing deployments before recreating the table.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_preview_card_winner_row'
    ) THEN
        ALTER TABLE preview_cards DROP CONSTRAINT fk_preview_card_winner_row;
    END IF;
END $$;
ALTER TABLE IF EXISTS preview_cards DROP COLUMN IF EXISTS winner_row_id;

CREATE TABLE IF NOT EXISTS preview_cards (
    id BIGSERIAL PRIMARY KEY,
    collection_mint_number BIGINT,
    slug TEXT NOT NULL UNIQUE,
    owner_wallet VARCHAR(42) NOT NULL,
    owner_proxy_wallet TEXT,
    season_id INTEGER NOT NULL,
    event_id TEXT,
    event_slug TEXT,
    card_title TEXT,
    primary_tag TEXT,
    secondary_tag TEXT,
    pattern TEXT,
    front_image_path TEXT NOT NULL,
    back_image_path TEXT NOT NULL,
    card_payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_preview_card_season
        FOREIGN KEY (season_id) REFERENCES seasons(id) ON DELETE CASCADE,
    CONSTRAINT preview_card_owner_wallet_format_check
        CHECK (owner_wallet ~* '^0x[a-f0-9]{40}$')
);

CREATE INDEX IF NOT EXISTS idx_preview_cards_owner_wallet_lower
    ON preview_cards(LOWER(owner_wallet), created_at DESC);
CREATE INDEX IF NOT EXISTS idx_preview_cards_created_at
    ON preview_cards(created_at DESC);

-- Per-season collection mint # (1..N within each season_id), not a global sequence.
DO $$
BEGIN
    IF to_regclass('public.preview_cards') IS NULL THEN
        RETURN;
    END IF;

    ALTER TABLE preview_cards
        ADD COLUMN IF NOT EXISTS collection_mint_number BIGINT;

    ALTER TABLE preview_cards
        ALTER COLUMN collection_mint_number DROP DEFAULT;

    DROP SEQUENCE IF EXISTS user_generated_cards_collection_mint_seq CASCADE;

    DROP INDEX IF EXISTS idx_generated_cards_collection_mint_number;

    WITH numbered AS (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY season_id
                ORDER BY created_at ASC, id ASC
            ) AS rn
        FROM preview_cards
    )
    UPDATE preview_cards u
    SET collection_mint_number = n.rn
    FROM numbered n
    WHERE u.id = n.id;

    CREATE UNIQUE INDEX IF NOT EXISTS ux_preview_cards_season_collection_mint
        ON preview_cards(season_id, collection_mint_number);
END $$;

CREATE OR REPLACE FUNCTION preview_cards_assign_season_mint_number()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.collection_mint_number IS NOT NULL THEN
        RETURN NEW;
    END IF;
    PERFORM pg_advisory_xact_lock(9283741, NEW.season_id);
    SELECT COALESCE(MAX(collection_mint_number), 0) + 1
    INTO NEW.collection_mint_number
    FROM preview_cards
    WHERE season_id = NEW.season_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tr_preview_cards_assign_season_mint ON preview_cards;
CREATE TRIGGER tr_preview_cards_assign_season_mint
    BEFORE INSERT ON preview_cards
    FOR EACH ROW
    EXECUTE PROCEDURE preview_cards_assign_season_mint_number();

-- Performance indexes for wallet lookup paths used by /api/wallets.
CREATE INDEX IF NOT EXISTS idx_claims_season_user_wallet_lower
    ON claims(season_id, LOWER(user_wallet));

DO $$
BEGIN
    IF to_regclass('public.user_closed_positions') IS NOT NULL THEN
        EXECUTE '
            CREATE INDEX IF NOT EXISTS idx_ucp_timestamp_human_wallet_lower
            ON user_closed_positions(timestamp_human, LOWER(proxy_wallet))
            WHERE timestamp_human IS NOT NULL
        ';
        EXECUTE '
            CREATE INDEX IF NOT EXISTS idx_ucp_timestamp_unix_wallet_lower
            ON user_closed_positions(timestamp_unix, LOWER(proxy_wallet))
        ';
    END IF;
END $$;


-- ============================================================================
-- 6b. CLAIMS DENORMALIZATION FOR PUBLIC /cards/{slug} PAGE
-- ============================================================================
-- Adds denormalized card-detail columns to ``claims`` so the public permalink
-- for a minted STAR can be served from ``claims`` directly. The cron mint
-- worker (`process_mint_queue`) calls ``denormalize_card_onto_claim`` after a
-- successful on-chain mint to populate these and to drop the matching
-- ``preview_cards`` row so minted STARs disappear from the showcase ticker.
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🎯 Adding claims denormalization columns for /cards/{slug}...'; END $$;

ALTER TABLE claims ADD COLUMN IF NOT EXISTS card_slug          TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS card_title         TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS front_image_url    TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS back_image_url     TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS primary_tag        TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS secondary_tag      TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS pattern            TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS card_payload_json  JSONB;

-- Drop legacy FK + column from prior deployments. The queue model no longer
-- uses winner_row_id; the snapshot lives directly on the claims row.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_claims_winner_row'
    ) THEN
        ALTER TABLE claims DROP CONSTRAINT fk_claims_winner_row;
    END IF;
END $$;
DROP INDEX IF EXISTS idx_claims_winner_row_id;
ALTER TABLE claims DROP COLUMN IF EXISTS winner_row_id;

CREATE UNIQUE INDEX IF NOT EXISTS ux_claims_card_slug
    ON claims(card_slug)
    WHERE card_slug IS NOT NULL;

COMMENT ON COLUMN claims.card_slug IS 'Public permalink slug for /cards/{slug}; mirrors qr_payload baked into the on-chain NFT';
COMMENT ON COLUMN claims.card_title IS 'Denormalized card title for rendering /cards/{slug} without reading preview_cards';
COMMENT ON COLUMN claims.front_image_url IS 'Pinata/IPFS URL for the front card image baked into the on-chain NFT';
COMMENT ON COLUMN claims.back_image_url IS 'Pinata/IPFS URL for the back card image baked into the on-chain NFT';
COMMENT ON COLUMN claims.card_payload_json IS 'Full polystars_card payload snapshot at mint time, mirroring preview_cards.card_payload_json';

DO $$ BEGIN RAISE NOTICE '✅ Claims denormalization columns ready'; END $$;

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
    COUNT(c.id) FILTER (WHERE c.phase_type = 'breach') as breach_claims,
    COUNT(c.id) FILTER (WHERE c.phase_type = 'vault') as vault_claims,
    COUNT(c.id) FILTER (WHERE c.phase_type = 'scavenge') as scavenge_claims,
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
    COUNT(*) FILTER (WHERE c.phase_type = 'breach') as breach_claims,
    COUNT(*) FILTER (WHERE c.phase_type = 'vault') as vault_claims,
    COUNT(*) FILTER (WHERE c.phase_type = 'scavenge') as scavenge_claims,
    COUNT(DISTINCT c.season_id) as seasons_participated,
    MIN(c.timestamp) as first_claim,
    MAX(c.timestamp) as last_claim,
    ARRAY_AGG(DISTINCT s.type ORDER BY s.type) as season_types_claimed
FROM claims c
INNER JOIN seasons s ON c.season_id = s.id
WHERE c.status = 'COMPLETED'
GROUP BY c.user_wallet;

COMMENT ON VIEW v_user_claim_history IS 'Per-user statistics across all seasons';

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
        WITH active_standard AS (
            SELECT s.id
            FROM seasons s
            WHERE s.type = 'standard'
              AND s.is_active = TRUE
            ORDER BY s.start_date DESC, s.id DESC
            LIMIT 1
        )
        SELECT 1
        FROM participants p
        JOIN active_standard a ON a.id = p.season_id
        WHERE LOWER(p.proxy_wallet) = LOWER(wallet_addr)
    );
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION is_origin_wallet IS 'Check if a wallet has at least one row in the currently active standard season''s participants partition.';

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

-- The ``participants`` partitioned table is created later in §12. This stats
-- block is intentionally guarded so the migration can run end-to-end on a
-- fresh database where ``participants`` does not yet exist at this point.
DO $$
DECLARE
    origins_count INTEGER;
BEGIN
    IF to_regclass('public.participants') IS NULL THEN
        RAISE NOTICE 'ℹ️  participants table not created yet — Origins count will be reported after §12';
        RETURN;
    END IF;
    SELECT COUNT(DISTINCT LOWER(proxy_wallet)) INTO origins_count
    FROM participants;
    RAISE NOTICE '✅ Total Origins wallets across all season partitions: %', origins_count;
END $$;

-- ============================================================================
-- 11. CLAIMS QUEUE MODEL (denormalized snapshot + caps + QUEUED status)
-- ============================================================================
-- Rebuilds claims as the single source of truth for both mint state AND the
-- participant snapshot that goes onto the card. Replaces the indirection via
-- winner_wallets_nft_to_claim + participants MV: when a user clicks "Mint",
-- a row is INSERTed here with status 'QUEUED', carrying every field needed
-- to render the card. A daily cron worker picks QUEUED rows and runs the
-- on-chain mint, transitioning them to PROCESSING -> COMPLETED.
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🧾 Extending claims for queue model...'; END $$;

-- 11.1 Per-season cap configuration columns on seasons.
ALTER TABLE seasons ADD COLUMN IF NOT EXISTS per_event_cap INTEGER;
-- Drop the legacy per_tag_cap column (and any matching default trigger
-- output) — tag-level diversification is now intentionally not enforced.
ALTER TABLE seasons DROP COLUMN IF EXISTS per_tag_cap;

COMMENT ON COLUMN seasons.per_event_cap IS 'Max claims per (season, event_id). NULL = unlimited.';

-- Backfill default per_event_cap derived from total_supply (50% of supply).
-- Applied only to seasons that have not been configured yet (cap IS NULL).
UPDATE seasons
SET per_event_cap = GREATEST(1, CEIL(total_supply * 0.5)::INTEGER)
WHERE per_event_cap IS NULL AND total_supply IS NOT NULL;

-- 11.1b Auto-fill per_event_cap on INSERT when omitted, derived from
--       total_supply. Mirrors the backfill above so Python-side season
--       creation does not need to know about cap columns.
CREATE OR REPLACE FUNCTION seasons_default_caps()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.total_supply IS NOT NULL AND NEW.total_supply > 0 THEN
        IF NEW.per_event_cap IS NULL THEN
            NEW.per_event_cap := GREATEST(1, CEIL(NEW.total_supply * 0.5)::INTEGER);
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tr_seasons_default_caps ON seasons;
CREATE TRIGGER tr_seasons_default_caps
    BEFORE INSERT ON seasons
    FOR EACH ROW
    EXECUTE PROCEDURE seasons_default_caps();

-- 11.2 Snapshot columns on claims (frozen at queue-insert time).
ALTER TABLE claims ADD COLUMN IF NOT EXISTS proxy_wallet          VARCHAR(42);
ALTER TABLE claims ADD COLUMN IF NOT EXISTS event_id              TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS event_slug            TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS snapshot_at           TIMESTAMPTZ;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS entry_cwap            NUMERIC(20, 4);
ALTER TABLE claims ADD COLUMN IF NOT EXISTS total_volume          NUMERIC(20, 2);
ALTER TABLE claims ADD COLUMN IF NOT EXISTS total_pnl             NUMERIC(20, 2);
ALTER TABLE claims ADD COLUMN IF NOT EXISTS roi_percentage        NUMERIC(20, 2);
ALTER TABLE claims ADD COLUMN IF NOT EXISTS entry_bracket         TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS edge                  TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS yield                 TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS gravity               TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS archetype             TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS archetype_description TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS archetype_math        TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS rarity_bracket        TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS participant_rank      INTEGER;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS claim_type            TEXT;
ALTER TABLE claims ADD COLUMN IF NOT EXISTS recipient_address     TEXT;

COMMENT ON COLUMN claims.proxy_wallet      IS 'Polymarket proxy wallet of the trader represented on this card. Differs from user_wallet (claimer EOA).';
COMMENT ON COLUMN claims.claim_type        IS 'origin = claimer minted their own card; looter = claimer minted someone else''s random card.';
COMMENT ON COLUMN claims.snapshot_at       IS 'When the participant snapshot for this card was frozen onto this row.';
COMMENT ON COLUMN claims.recipient_address IS 'On-chain recipient of the minted NFT. Frozen at queue-insert time. Defaults to user_wallet for self-mints; admin can specify a different EOA.';

-- 11.3 Extend status CHECK to allow 'QUEUED'. Inline CHECK constraints get
--      auto-generated names; drop any matching one before recreating.
DO $$
DECLARE
    cons_name TEXT;
BEGIN
    FOR cons_name IN
        SELECT c.conname
        FROM   pg_constraint c
        JOIN   pg_class      t ON t.oid = c.conrelid
        WHERE  t.relname = 'claims'
          AND  c.contype = 'c'
          AND  pg_get_constraintdef(c.oid) ILIKE '%status%IN%'
    LOOP
        EXECUTE 'ALTER TABLE claims DROP CONSTRAINT ' || quote_ident(cons_name);
    END LOOP;
END $$;

ALTER TABLE claims
    ADD CONSTRAINT claims_status_check
    CHECK (status IN ('QUEUED', 'PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'));

-- 11.5 Replace the legacy strict UNIQUE(user_wallet, season_id) with a
--      partial index that also covers 'QUEUED'. The strict version blocked
--      retries after FAILED; the partial form scopes uniqueness to the
--      active set so a user can re-queue after a permanent failure.
DO $$
DECLARE
    cons_name TEXT;
BEGIN
    FOR cons_name IN
        SELECT c.conname
        FROM   pg_constraint c
        JOIN   pg_class      t ON t.oid = c.conrelid
        WHERE  t.relname = 'claims'
          AND  c.contype = 'u'
          AND  pg_get_constraintdef(c.oid) ILIKE '%user_wallet%season_id%'
    LOOP
        EXECUTE 'ALTER TABLE claims DROP CONSTRAINT ' || quote_ident(cons_name);
    END LOOP;
END $$;

DROP INDEX IF EXISTS ux_claims_active_season_user_wallet_lower;
CREATE UNIQUE INDEX ux_claims_active_season_user_wallet_lower
    ON claims (season_id, LOWER(user_wallet))
    WHERE status IN ('QUEUED', 'PENDING', 'PROCESSING', 'COMPLETED');

DROP INDEX IF EXISTS ux_claims_active_proxy_wallet;
CREATE UNIQUE INDEX ux_claims_active_proxy_wallet
    ON claims (season_id, LOWER(proxy_wallet))
    WHERE proxy_wallet IS NOT NULL
      AND status IN ('QUEUED', 'PENDING', 'PROCESSING', 'COMPLETED');

DROP INDEX IF EXISTS idx_claims_active_season_event;
CREATE INDEX idx_claims_active_season_event
    ON claims (season_id, event_id)
    WHERE event_id IS NOT NULL
      AND status IN ('QUEUED', 'PENDING', 'PROCESSING', 'COMPLETED');

-- Per-tag index removed along with per_tag_cap.
DROP INDEX IF EXISTS idx_claims_active_season_tag;

-- Cron worker scan: ORDER BY (season_id, created_at) FOR UPDATE SKIP LOCKED.
DROP INDEX IF EXISTS idx_claims_queued_pickup;
CREATE INDEX idx_claims_queued_pickup
    ON claims (season_id, created_at)
    WHERE status = 'QUEUED';

-- 11.6 BEFORE INSERT trigger that enforces total_supply and per_event cap.
--      Runs only when the new row enters the active set (status != FAILED).
--      Uses partial-index counts (cheap: claims << participants).
CREATE OR REPLACE FUNCTION claims_check_caps()
RETURNS TRIGGER AS $$
DECLARE
    v_total_supply  INTEGER;
    v_event_cap     INTEGER;
    v_active_count  INTEGER;
    v_event_count   INTEGER;
BEGIN
    IF NEW.status = 'FAILED' THEN
        RETURN NEW;
    END IF;

    -- Serialize cap evaluation per-season. Without this, two concurrent
    -- INSERTs in READ COMMITTED both observe COUNT(*) = N, both pass the
    -- < total_supply check, and oversupply slips through. The lock is
    -- released at COMMIT, so the next tx's COUNT sees the just-committed
    -- INSERT. Distinct seed from cmn allocator (9283742) and preview_cards
    -- (9283741) so cap-check doesn't queue behind unrelated per-season work.
    PERFORM pg_advisory_xact_lock(9283740, NEW.season_id);

    SELECT total_supply, per_event_cap
    INTO   v_total_supply, v_event_cap
    FROM   seasons
    WHERE  id = NEW.season_id;

    IF v_total_supply IS NULL THEN
        RAISE EXCEPTION 'Season % not found or has no total_supply', NEW.season_id
            USING ERRCODE = 'P0002';
    END IF;

    -- Total supply cap (always enforced when total_supply > 0).
    IF v_total_supply > 0 THEN
        SELECT COUNT(*) INTO v_active_count
        FROM   claims
        WHERE  season_id = NEW.season_id
          AND  status IN ('QUEUED', 'PENDING', 'PROCESSING', 'COMPLETED');

        IF v_active_count >= v_total_supply THEN
            RAISE EXCEPTION 'season % total supply (%) exhausted', NEW.season_id, v_total_supply
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- Per-event cap (skipped if NULL or event_id missing).
    IF v_event_cap IS NOT NULL AND NEW.event_id IS NOT NULL THEN
        SELECT COUNT(*) INTO v_event_count
        FROM   claims
        WHERE  season_id = NEW.season_id
          AND  event_id  = NEW.event_id
          AND  status IN ('QUEUED', 'PENDING', 'PROCESSING', 'COMPLETED');

        IF v_event_count >= v_event_cap THEN
            RAISE EXCEPTION
                'season % event % cap (%) reached',
                NEW.season_id, NEW.event_id, v_event_cap
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tr_claims_check_caps ON claims;
CREATE TRIGGER tr_claims_check_caps
    BEFORE INSERT ON claims
    FOR EACH ROW
    EXECUTE PROCEDURE claims_check_caps();

DO $$ BEGIN RAISE NOTICE '✅ Claims queue model ready (snapshot columns, QUEUED status, cap trigger)'; END $$;

-- ============================================================================
-- 12. PARTICIPANTS PARTITIONED TABLE (replaces materialized view)
-- ============================================================================
-- Replaces the global ``participants`` materialized view with a regular table
-- partitioned BY LIST(season_id). Each season gets its own partition created
-- alongside the season row. The daily scheduler refreshes each active
-- partition by calling refresh_participants_for_season(season_id, ...).
-- The analytic CTE body now lives in the view participants_analytics
-- (init-db.sql §11), which feeds the per-season INSERT.
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🧬 Migrating participants MV → partitioned table...'; END $$;

-- 12.1 Drop the legacy materialized view if it still exists. CASCADE clears
--      the auto-built indexes that lived on the MV. Code reading from
--      ``participants`` continues to work because the partitioned TABLE we
--      create below answers to the same name.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'participants'
          AND c.relkind = 'm'
    ) THEN
        EXECUTE 'DROP MATERIALIZED VIEW participants CASCADE';
        RAISE NOTICE '🗑️  Dropped legacy participants materialized view';
    END IF;
END $$;

-- 12.2 Create the partitioned table. Same column shape as the old MV plus
--      season_id as the partition key. Primary key includes season_id (a
--      partition key column is required to be in every unique constraint).
CREATE TABLE IF NOT EXISTS participants (
    season_id              BIGINT NOT NULL,
    proxy_wallet           VARCHAR(42) NOT NULL,
    event_id               TEXT,
    event_slug             TEXT NOT NULL,
    entry_cwap             NUMERIC(20, 4),
    total_volume           NUMERIC(20, 2),
    total_pnl              NUMERIC(20, 2),
    roi_percentage         NUMERIC(20, 2),
    entry_bracket          TEXT,
    edge                   TEXT,
    yield                  TEXT,
    gravity                TEXT,
    archetype              TEXT,
    archetype_description  TEXT,
    archetype_math         TEXT,
    rarity_bracket         TEXT,
    rank                   INTEGER,
    PRIMARY KEY (season_id, proxy_wallet, event_slug)
) PARTITION BY LIST (season_id);

COMMENT ON TABLE participants IS
    'Per-season frozen participant pool. Each partition is one season; '
    'refreshed once per day via refresh_participants_for_season(season_id). '
    'Replaces the previous global participants materialized view.';

-- Default catch-all partition (rows whose season_id has no dedicated partition).
CREATE TABLE IF NOT EXISTS participants_default
    PARTITION OF participants DEFAULT;

-- Shared indexes inherited by every partition.
CREATE INDEX IF NOT EXISTS idx_participants_event_id
    ON participants (season_id, event_id);
CREATE INDEX IF NOT EXISTS idx_participants_event_slug
    ON participants (season_id, event_slug);
CREATE INDEX IF NOT EXISTS idx_participants_lower_proxy_wallet
    ON participants (season_id, LOWER(proxy_wallet));
CREATE INDEX IF NOT EXISTS idx_participants_archetype
    ON participants (season_id, LOWER(proxy_wallet), archetype);

-- 12.3 Helper functions for partition management.

-- Create the partition for a season if it does not exist yet. Idempotent.
CREATE OR REPLACE FUNCTION participants_ensure_partition(p_season_id BIGINT)
RETURNS VOID AS $$
DECLARE
    v_partition_name TEXT;
BEGIN
    v_partition_name := 'participants_season_' || p_season_id;
    IF to_regclass('public.' || quote_ident(v_partition_name)) IS NULL THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF participants FOR VALUES IN (%L)',
            v_partition_name,
            p_season_id
        );
    END IF;
END;
$$ LANGUAGE plpgsql;

-- DETACH and DROP a season's partition. Safe for closed/archived seasons.
CREATE OR REPLACE FUNCTION participants_drop_partition(p_season_id BIGINT)
RETURNS VOID AS $$
DECLARE
    v_partition_name TEXT;
BEGIN
    v_partition_name := 'participants_season_' || p_season_id;
    IF to_regclass('public.' || quote_ident(v_partition_name)) IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE participants DETACH PARTITION %I',
            v_partition_name
        );
        EXECUTE format('DROP TABLE %I', v_partition_name);
    END IF;
END;
$$ LANGUAGE plpgsql;

-- TRUNCATE the partition for a season and reload it from participants_analytics
-- filtered to the season's working events. Caller passes window bounds and
-- whether to anchor by event_resolution_queue (standard) or events.end_date
-- (genesis). Returns the count of rows inserted.
CREATE OR REPLACE FUNCTION refresh_participants_for_season(
    p_season_id              BIGINT,
    p_window_start           TIMESTAMPTZ,
    p_window_end             TIMESTAMPTZ,
    p_use_resolution_anchor  BOOLEAN
)
RETURNS INTEGER AS $$
DECLARE
    v_partition_name TEXT;
    v_count          INTEGER;
BEGIN
    PERFORM participants_ensure_partition(p_season_id);
    v_partition_name := 'participants_season_' || p_season_id;
    EXECUTE 'TRUNCATE TABLE ' || quote_ident(v_partition_name);

    INSERT INTO participants (
        season_id, proxy_wallet, event_id, event_slug,
        entry_cwap, total_volume, total_pnl, roi_percentage,
        entry_bracket, edge, yield, gravity,
        archetype, archetype_description, archetype_math, rarity_bracket,
        rank
    )
    SELECT
        p_season_id,
        pa.proxy_wallet,
        pa.event_id,
        pa.event_slug,
        pa.entry_cwap, pa.total_volume, pa.total_pnl, pa.roi_percentage,
        pa.entry_bracket, pa.edge, pa.yield, pa.gravity,
        pa.archetype, pa.archetype_description, pa.archetype_math, pa.rarity_bracket,
        pa.rank
    FROM participants_analytics pa
    WHERE pa.proxy_wallet IS NOT NULL
      AND pa.event_slug   IS NOT NULL
      AND EXISTS (
          SELECT 1
          FROM events e
          LEFT JOIN event_resolution_queue erq ON erq.event_id = e.id
          WHERE (
                  (pa.event_id IS NOT NULL AND e.id = pa.event_id)
              OR  (e.slug = pa.event_slug)
          )
          AND (
              (
                  p_use_resolution_anchor
                  AND COALESCE(erq.closed, FALSE) = TRUE
                  AND erq.status = 'processed'
                  AND erq.resolution_ready_at IS NOT NULL
                  AND erq.resolution_ready_at >= p_window_start
                  AND erq.resolution_ready_at <  p_window_end
              )
              OR
              (
                  NOT p_use_resolution_anchor
                  AND COALESCE(e.end_date, e.creation_date, e.start_date) >= p_window_start
                  AND COALESCE(e.end_date, e.creation_date, e.start_date) <  p_window_end
              )
          )
          -- 1 event ↔ 1 season: skip events already loaded into another partition.
          AND NOT EXISTS (
              SELECT 1
              FROM participants other
              WHERE other.season_id <> p_season_id
                AND (
                    (e.id IS NOT NULL AND other.event_id = e.id)
                 OR (other.event_slug = e.slug)
                )
          )
      );

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION refresh_participants_for_season(BIGINT, TIMESTAMPTZ, TIMESTAMPTZ, BOOLEAN) IS
    'TRUNCATE the season''s participants partition and reload it from '
    'participants_analytics filtered to the season''s working events. '
    'Caller decides the window and resolution anchor based on season type.';

-- 12.4 Pre-create partitions for every existing season so the partitioned
--      table is immediately usable. Initial population happens lazily on the
--      first refresh_participants_for_season() call from the daily scheduler.
DO $$
DECLARE
    s RECORD;
BEGIN
    FOR s IN SELECT id FROM seasons LOOP
        PERFORM participants_ensure_partition(s.id::BIGINT);
    END LOOP;
END $$;

DO $$ BEGIN RAISE NOTICE '✅ participants partitioned table ready (call refresh_participants_for_season per active season)'; END $$;

-- ============================================================================
-- COMPLETION
-- ============================================================================
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '✅ PolyStars Seasons System migration complete!'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '📦 Created:'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Enums: season_type, phase_type'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Tables: seasons, claims, season_events_log, participants (partitioned)'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Analytics views: v_active_seasons, v_season_leaderboard, v_user_claim_history, participants_analytics'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Functions: is_origin_wallet(), get_active_season(), decrement_season_supply()'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Trigger: trigger_update_season_supply'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '🎮 Ready to start seasons!'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
