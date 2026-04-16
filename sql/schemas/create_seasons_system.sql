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
    recipient_solana_wallet TEXT,
    
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
CREATE INDEX IF NOT EXISTS idx_claims_recipient_solana_wallet ON claims(recipient_solana_wallet) WHERE recipient_solana_wallet IS NOT NULL;
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

-- Per-season collection mint # on claims (1..N within each season_id), mirroring user_generated_cards.
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
    -- Distinct namespace from user_generated_cards (9283741) to avoid cross-lock contention.
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
COMMENT ON COLUMN claims.recipient_solana_wallet IS 'Solana wallet where minted NFT should be delivered';
COMMENT ON COLUMN claims.season_id IS 'Reference to the season being claimed';
COMMENT ON COLUMN claims.phase_type IS 'Claim phase: breach, vault (Origins only), or scavenge';
COMMENT ON COLUMN claims.tx_hash IS 'Blockchain transaction hash for the mint';
COMMENT ON COLUMN claims.token_id IS 'Minted NFT token ID';
COMMENT ON COLUMN claims.asset_address IS 'Minted Solana NFT asset address';

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
-- 6. ORIGIN SNAPSHOTS TABLE (per-season Vault eligibility)
-- ============================================================================
DO $$ BEGIN RAISE NOTICE '🏛️  Creating winner_wallets_nft_to_claim table...'; END $$;

-- Rename old table from previous migration versions.
DO $$
BEGIN
    IF to_regclass('public.season_origin_wallets') IS NOT NULL
       AND to_regclass('public.winner_wallets_nft_to_claim') IS NULL THEN
        ALTER TABLE season_origin_wallets RENAME TO winner_wallets_nft_to_claim;
    END IF;
END $$;

-- Drop compatibility/analytics views early to avoid dependency errors during column migration.
DROP VIEW IF EXISTS v_origins_eligibility;
DROP VIEW IF EXISTS v_origin_wallets;

CREATE TABLE IF NOT EXISTS winner_wallets_nft_to_claim (
    id BIGSERIAL PRIMARY KEY,
    season_id INTEGER NOT NULL,
    proxy_wallet VARCHAR(42) NOT NULL,
    source TEXT NOT NULL DEFAULT 'top_pnl_30d_season_start',
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    entry_cwap NUMERIC(20, 4),
    total_volume NUMERIC(20, 2),
    total_pnl NUMERIC(20, 2),
    roi_percentage NUMERIC(20, 2),
    entry_bracket TEXT,
    edge TEXT,
    yield TEXT,
    gravity TEXT,
    rank INTEGER,
    event_id TEXT,
    event_slug TEXT,
    archetype TEXT,
    archetype_description TEXT,
    archetype_math TEXT,
    rarity_bracket TEXT,
    is_minted BOOLEAN NOT NULL DEFAULT FALSE,
    minted_at TIMESTAMPTZ,
    minted_to_wallet TEXT,
    minted_to_solana_wallet TEXT,
    minted_claim_id BIGINT,
    minted_tx_hash TEXT,
    minted_asset_address TEXT,
    CONSTRAINT fk_origin_snapshot_season
        FOREIGN KEY (season_id) REFERENCES seasons(id) ON DELETE CASCADE,
    CONSTRAINT winner_proxy_wallet_format_check
        CHECK (proxy_wallet ~* '^0x[a-f0-9]{40}$'),
    CONSTRAINT winner_wallet_unique_per_season
        UNIQUE (season_id, proxy_wallet)
);

-- Ensure columns exist for upgraded databases.
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS proxy_wallet VARCHAR(42);
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS event_id TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS event_slug TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS entry_cwap NUMERIC(20, 4);
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS total_volume NUMERIC(20, 2);
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS total_pnl NUMERIC(20, 2);
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS roi_percentage NUMERIC(20, 2);
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS entry_bracket TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS edge TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS yield TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS gravity TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS rank INTEGER;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS is_minted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS minted_at TIMESTAMPTZ;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS minted_to_wallet TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS minted_to_solana_wallet TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS minted_claim_id BIGINT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS minted_tx_hash TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS minted_asset_address TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS archetype TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS archetype_description TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS archetype_math TEXT;
ALTER TABLE winner_wallets_nft_to_claim ADD COLUMN IF NOT EXISTS rarity_bracket TEXT;

-- Backward-compatible migration from legacy column naming.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'winner_wallets_nft_to_claim'
          AND column_name = 'wallet_address'
    ) THEN
        EXECUTE '
            UPDATE winner_wallets_nft_to_claim
            SET proxy_wallet = LOWER(wallet_address)
            WHERE proxy_wallet IS NULL
              AND wallet_address IS NOT NULL
        ';
    END IF;
END $$;

-- Remove legacy business columns after migrating to participants payload model.
ALTER TABLE winner_wallets_nft_to_claim DROP COLUMN IF EXISTS wallet_address;
ALTER TABLE winner_wallets_nft_to_claim DROP COLUMN IF EXISTS total_pnl_window;
ALTER TABLE winner_wallets_nft_to_claim DROP COLUMN IF EXISTS pnl_rank;
ALTER TABLE winner_wallets_nft_to_claim DROP COLUMN IF EXISTS market_id;
ALTER TABLE winner_wallets_nft_to_claim DROP COLUMN IF EXISTS condition_id;
ALTER TABLE winner_wallets_nft_to_claim DROP COLUMN IF EXISTS event_title;

CREATE INDEX IF NOT EXISTS idx_winners_snapshot_season
    ON winner_wallets_nft_to_claim(season_id);
CREATE INDEX IF NOT EXISTS idx_winners_snapshot_proxy_wallet
    ON winner_wallets_nft_to_claim(proxy_wallet);
CREATE UNIQUE INDEX IF NOT EXISTS ux_winners_snapshot_season_proxy_wallet
    ON winner_wallets_nft_to_claim(season_id, proxy_wallet);
CREATE INDEX IF NOT EXISTS idx_winners_snapshot_season_rank
    ON winner_wallets_nft_to_claim(season_id, rank);
CREATE INDEX IF NOT EXISTS idx_winners_snapshot_event_id
    ON winner_wallets_nft_to_claim(event_id);
CREATE INDEX IF NOT EXISTS idx_winners_snapshot_event_slug
    ON winner_wallets_nft_to_claim(event_slug);

COMMENT ON TABLE winner_wallets_nft_to_claim IS 'Frozen per-season randomized participants allocation. Technical mint metadata + participant analytics payload.';
COMMENT ON COLUMN winner_wallets_nft_to_claim.window_start IS 'Inclusive lower bound used to derive season working events';
COMMENT ON COLUMN winner_wallets_nft_to_claim.window_end IS 'Exclusive upper bound used to derive season working events';
COMMENT ON COLUMN winner_wallets_nft_to_claim.event_id IS 'Participant event id sampled from participants materialized view';
COMMENT ON COLUMN winner_wallets_nft_to_claim.event_slug IS 'Participant event slug sampled from participants materialized view';
COMMENT ON COLUMN winner_wallets_nft_to_claim.archetype IS 'Archetype label frozen from participants at snapshot time';
COMMENT ON COLUMN winner_wallets_nft_to_claim.rarity_bracket IS 'Occurrence band text frozen from participants at snapshot time';

DO $$ BEGIN RAISE NOTICE '✅ winner_wallets_nft_to_claim table created'; END $$;

CREATE TABLE IF NOT EXISTS user_generated_cards (
    id BIGSERIAL PRIMARY KEY,
    collection_mint_number BIGINT,
    slug TEXT NOT NULL UNIQUE,
    owner_wallet VARCHAR(42) NOT NULL,
    owner_proxy_wallet TEXT,
    winner_row_id BIGINT NOT NULL UNIQUE,
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
    CONSTRAINT fk_generated_card_winner_row
        FOREIGN KEY (winner_row_id) REFERENCES winner_wallets_nft_to_claim(id) ON DELETE CASCADE,
    CONSTRAINT fk_generated_card_season
        FOREIGN KEY (season_id) REFERENCES seasons(id) ON DELETE CASCADE,
    CONSTRAINT generated_card_owner_wallet_format_check
        CHECK (owner_wallet ~* '^0x[a-f0-9]{40}$')
);

CREATE INDEX IF NOT EXISTS idx_generated_cards_owner_wallet_lower
    ON user_generated_cards(LOWER(owner_wallet), created_at DESC);
CREATE INDEX IF NOT EXISTS idx_generated_cards_created_at
    ON user_generated_cards(created_at DESC);

-- Per-season collection mint # (1..N within each season_id), not a global sequence.
DO $$
BEGIN
    IF to_regclass('public.user_generated_cards') IS NULL THEN
        RETURN;
    END IF;

    ALTER TABLE user_generated_cards
        ADD COLUMN IF NOT EXISTS collection_mint_number BIGINT;

    ALTER TABLE user_generated_cards
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
        FROM user_generated_cards
    )
    UPDATE user_generated_cards u
    SET collection_mint_number = n.rn
    FROM numbered n
    WHERE u.id = n.id;

    CREATE UNIQUE INDEX IF NOT EXISTS ux_user_generated_cards_season_collection_mint
        ON user_generated_cards(season_id, collection_mint_number);
END $$;

CREATE OR REPLACE FUNCTION user_generated_cards_assign_season_mint_number()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.collection_mint_number IS NOT NULL THEN
        RETURN NEW;
    END IF;
    PERFORM pg_advisory_xact_lock(9283741, NEW.season_id);
    SELECT COALESCE(MAX(collection_mint_number), 0) + 1
    INTO NEW.collection_mint_number
    FROM user_generated_cards
    WHERE season_id = NEW.season_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tr_user_generated_cards_assign_season_mint ON user_generated_cards;
CREATE TRIGGER tr_user_generated_cards_assign_season_mint
    BEFORE INSERT ON user_generated_cards
    FOR EACH ROW
    EXECUTE PROCEDURE user_generated_cards_assign_season_mint_number();

-- Performance indexes for wallet lookup paths used by /api/wallets.
CREATE INDEX IF NOT EXISTS idx_claims_season_user_wallet_lower
    ON claims(season_id, LOWER(user_wallet));
CREATE INDEX IF NOT EXISTS idx_winners_snapshot_season_wallet_lower
    ON winner_wallets_nft_to_claim(season_id, LOWER(proxy_wallet));

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

-- Backfill snapshots for existing standard/genesis seasons (idempotent).
-- Uses randomized rows from participants constrained to season "working events".
DO $$
DECLARE
    backfilled_count INTEGER := 0;
BEGIN
    IF to_regclass('public.user_closed_positions') IS NULL THEN
        RAISE NOTICE '⚠️  user_closed_positions table not found, skipping winners backfill';
        RETURN;
    END IF;
    IF to_regclass('public.participants') IS NULL THEN
        RAISE NOTICE '⚠️  participants relation not found, skipping winners backfill';
        RETURN;
    END IF;

    WITH target_seasons AS (
        SELECT
            s.id,
            s.type,
            CASE
                WHEN s.type = 'standard'
                    THEN s.start_date - INTERVAL '3 days' - INTERVAL '10 days'
                ELSE TIMESTAMPTZ '2024-06-01 00:00:00+00'
            END AS window_start,
            CASE
                WHEN s.type = 'standard'
                    THEN s.start_date - INTERVAL '3 days'
                ELSE TIMESTAMPTZ '2026-02-07 00:00:00+00'
            END AS window_end,
            CASE
                WHEN s.type = 'standard' THEN 10
                ELSE 20
            END AS rank_limit,
            CASE
                WHEN s.type = 'standard' THEN 'participants_randomized_standard_backfill'
                ELSE 'participants_randomized_genesis_backfill'
            END AS snapshot_source
        FROM seasons s
        WHERE s.type IN ('standard', 'genesis')
          AND NOT EXISTS (
              SELECT 1
              FROM winner_wallets_nft_to_claim w
              WHERE w.season_id = s.id
          )
    ),
    inserted AS (
        INSERT INTO winner_wallets_nft_to_claim (
            season_id,
            proxy_wallet,
            source,
            window_start,
            window_end,
            snapshot_at,
            event_id,
            event_slug,
            entry_cwap,
            total_volume,
            total_pnl,
            roi_percentage,
            entry_bracket,
            edge,
            yield,
            gravity,
            rank,
            archetype,
            archetype_description,
            archetype_math,
            rarity_bracket
        )
        SELECT
            ts.id AS season_id,
            picked.proxy_wallet,
            ts.snapshot_source::TEXT AS source,
            ts.window_start AS window_start,
            ts.window_end AS window_end,
            NOW() AS snapshot_at,
            picked.event_id,
            picked.event_slug,
            picked.entry_cwap,
            picked.total_volume,
            picked.total_pnl,
            picked.roi_percentage,
            picked.entry_bracket,
            picked.edge,
            picked.yield,
            picked.gravity,
            picked.rank,
            picked.archetype,
            picked.archetype_description,
            picked.archetype_math,
            picked.rarity_bracket
        FROM target_seasons ts
        CROSS JOIN LATERAL (
            WITH position_base AS (
                SELECT
                    LOWER(ucp.proxy_wallet) AS proxy_wallet,
                    COALESCE(
                        ucp.end_date_parsed,
                        ucp.timestamp_human,
                        TO_TIMESTAMP(ucp.timestamp_unix)
                    ) AS position_time,
                    COALESCE(
                        ucp.event_id,
                        e_by_slug.id
                    ) AS event_id,
                    COALESCE(ucp.event_slug, e_by_id.slug, e_by_slug.slug) AS event_slug
                FROM user_closed_positions ucp
                LEFT JOIN events e_by_id
                    ON ucp.event_id IS NOT NULL
                   AND e_by_id.id = ucp.event_id
                LEFT JOIN LATERAL (
                    SELECT e.id, e.slug
                    FROM events e
                    WHERE ucp.event_slug IS NOT NULL
                      AND e.slug = ucp.event_slug
                    LIMIT 1
                ) e_by_slug ON TRUE
                WHERE ucp.proxy_wallet IS NOT NULL
            ),
            resolved_positions AS (
                SELECT
                    pb.event_id,
                    pb.event_slug,
                    CASE
                        WHEN ts.type = 'standard' THEN COALESCE(erq.resolution_ready_at, erq.closed_time)
                        ELSE pb.position_time
                    END AS season_anchor_at
                FROM position_base pb
                LEFT JOIN event_resolution_queue erq
                    ON erq.event_id = pb.event_id
                WHERE (
                    ts.type = 'standard'
                    AND pb.event_id IS NOT NULL
                    AND COALESCE(erq.closed, FALSE) = TRUE
                    AND COALESCE(erq.resolution_ready_at, erq.closed_time) IS NOT NULL
                    AND COALESCE(erq.resolution_ready_at, erq.closed_time) >= ts.window_start
                    AND COALESCE(erq.resolution_ready_at, erq.closed_time) < ts.window_end
                ) OR (
                    ts.type = 'genesis'
                    AND pb.position_time IS NOT NULL
                    AND pb.position_time >= ts.window_start
                    AND pb.position_time < ts.window_end
                )
            ),
            working_events AS (
                SELECT DISTINCT event_id, event_slug
                FROM resolved_positions
                WHERE event_id IS NOT NULL OR event_slug IS NOT NULL
            ),
            candidate_participants AS (
                SELECT p.*
                FROM participants p
                WHERE p.proxy_wallet IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM working_events we
                      WHERE (
                          we.event_id IS NOT NULL
                          AND p.event_id = we.event_id
                      ) OR (
                          we.event_slug IS NOT NULL
                          AND p.event_slug = we.event_slug
                      )
                  )
            ),
            per_wallet_random AS (
                SELECT DISTINCT ON (LOWER(p.proxy_wallet))
                    LOWER(p.proxy_wallet) AS proxy_wallet,
                    p.event_id,
                    p.event_slug,
                    p.entry_cwap,
                    p.total_volume,
                    p.total_pnl,
                    p.roi_percentage,
                    p.entry_bracket,
                    p.edge,
                    p.yield,
                    p.gravity,
                    p.rank,
                    p.archetype,
                    p.archetype_description,
                    p.archetype_math,
                    p.rarity_bracket
                FROM candidate_participants p
                ORDER BY LOWER(p.proxy_wallet), RANDOM()
            )
            SELECT *
            FROM per_wallet_random
            ORDER BY RANDOM()
            LIMIT ts.rank_limit
        ) AS picked
        RETURNING 1
    )
    SELECT COUNT(*) INTO backfilled_count
    FROM inserted;

    RAISE NOTICE '✅ winner_wallets_nft_to_claim backfill rows inserted: %', backfilled_count;
END $$;

-- Compatibility view: Origins for currently active standard season.
DO $$ BEGIN RAISE NOTICE '🏛️  Creating v_origin_wallets compatibility view...'; END $$;

-- Recreate view from scratch because old deployments may have different column types.
-- v_origins_eligibility depends on v_origin_wallets and is recreated later in this migration.
DROP VIEW IF EXISTS v_origins_eligibility;
DROP VIEW IF EXISTS v_origin_wallets;

CREATE OR REPLACE VIEW v_origin_wallets AS
WITH active_standard AS (
    SELECT s.id
    FROM seasons s
    WHERE s.type = 'standard'
      AND s.is_active = TRUE
    ORDER BY s.start_date DESC, s.id DESC
    LIMIT 1
)
SELECT
    sow.proxy_wallet AS wallet_address,
    sow.source,
    sow.total_pnl AS total_pnl_30d,
    sow.rank AS pnl_rank
FROM winner_wallets_nft_to_claim sow
JOIN active_standard a ON a.id = sow.season_id
ORDER BY sow.rank NULLS LAST, sow.proxy_wallet;

COMMENT ON VIEW v_origin_wallets IS 'Compatibility view returning Origins wallets snapshot for currently active standard season.';

DO $$ BEGIN RAISE NOTICE '✅ v_origin_wallets compatibility view created'; END $$;

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
        WITH active_standard AS (
            SELECT s.id
            FROM seasons s
            WHERE s.type = 'standard'
              AND s.is_active = TRUE
            ORDER BY s.start_date DESC, s.id DESC
            LIMIT 1
        )
        SELECT 1
        FROM winner_wallets_nft_to_claim sow
        JOIN active_standard a ON a.id = sow.season_id
        WHERE LOWER(sow.proxy_wallet) = LOWER(wallet_addr)
    );
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION is_origin_wallet IS 'Check if a wallet address is eligible for Vault phase in the currently active standard season (from frozen snapshot table)';

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
    SELECT COUNT(*) INTO origins_count
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
DO $$ BEGIN RAISE NOTICE '   - Tables: seasons, claims, season_events_log, winner_wallets_nft_to_claim'; END $$;
DO $$ BEGIN RAISE NOTICE '   - View: v_origin_wallets (compatibility)'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Analytics views: v_active_seasons, v_season_leaderboard, v_user_claim_history, v_origins_eligibility'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Functions: is_origin_wallet(), get_active_season(), decrement_season_supply()'; END $$;
DO $$ BEGIN RAISE NOTICE '   - Trigger: trigger_update_season_supply'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
DO $$ BEGIN RAISE NOTICE '🎮 Ready to start seasons!'; END $$;
DO $$ BEGIN RAISE NOTICE ''; END $$;
