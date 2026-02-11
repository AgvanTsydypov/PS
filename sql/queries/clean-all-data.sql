-- ============================================================================
-- CLEAN ALL DATA FROM POLYSTARS DATABASE
-- ============================================================================
-- This script removes all data from all tables while preserving table structure
-- ⚠️  WARNING: This will delete ALL data! Use with caution!
-- ============================================================================

DO $$ BEGIN RAISE NOTICE '🧹 Starting database cleanup...'; END $$;

-- Disable foreign key checks temporarily for faster cleanup
SET session_replication_role = 'replica';

-- ============================================================================
-- Clear all main data tables
-- ============================================================================

DO $$ BEGIN RAISE NOTICE '📊 Cleaning events and markets...'; END $$;
TRUNCATE TABLE markets CASCADE;
TRUNCATE TABLE events CASCADE;
DO $$ BEGIN RAISE NOTICE '✅ Events and markets cleaned'; END $$;

DO $$ BEGIN RAISE NOTICE '📝 Cleaning fetch_metadata...'; END $$;
TRUNCATE TABLE fetch_metadata RESTART IDENTITY CASCADE;
DO $$ BEGIN RAISE NOTICE '✅ Fetch_metadata cleaned'; END $$;

DO $$ BEGIN RAISE NOTICE '💰 Cleaning redemptions...'; END $$;
TRUNCATE TABLE redemptions RESTART IDENTITY CASCADE;
DO $$ BEGIN RAISE NOTICE '✅ Redemptions cleaned'; END $$;

DO $$ BEGIN RAISE NOTICE '📊 Cleaning user_closed_positions...'; END $$;
TRUNCATE TABLE user_closed_positions RESTART IDENTITY CASCADE;
DO $$ BEGIN RAISE NOTICE '✅ User_closed_positions cleaned'; END $$;

DO $$ BEGIN RAISE NOTICE '🏆 Cleaning trader_leaderboard...'; END $$;
TRUNCATE TABLE trader_leaderboard RESTART IDENTITY CASCADE;
DO $$ BEGIN RAISE NOTICE '✅ Trader_leaderboard cleaned'; END $$;

DO $$ BEGIN RAISE NOTICE '🎨 Cleaning NFT tables...'; END $$;
TRUNCATE TABLE nft_claims RESTART IDENTITY CASCADE;
TRUNCATE TABLE rate_limits RESTART IDENTITY CASCADE;
DO $$ BEGIN RAISE NOTICE '✅ NFT tables cleaned'; END $$;

DO $$ BEGIN RAISE NOTICE '📊 Cleaning data_loads tracking...'; END $$;
TRUNCATE TABLE data_loads RESTART IDENTITY CASCADE;
DO $$ BEGIN RAISE NOTICE '✅ Data_loads cleaned'; END $$;

-- Re-enable foreign key checks
SET session_replication_role = 'origin';

-- ============================================================================
-- Verify cleanup
-- ============================================================================
DO $$ 
DECLARE
    events_count INTEGER;
    markets_count INTEGER;
    redemptions_count INTEGER;
    positions_count INTEGER;
    leaderboard_count INTEGER;
    nft_count INTEGER;
    data_loads_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO events_count FROM events;
    SELECT COUNT(*) INTO markets_count FROM markets;
    SELECT COUNT(*) INTO redemptions_count FROM redemptions;
    SELECT COUNT(*) INTO positions_count FROM user_closed_positions;
    SELECT COUNT(*) INTO leaderboard_count FROM trader_leaderboard;
    SELECT COUNT(*) INTO nft_count FROM nft_claims;
    SELECT COUNT(*) INTO data_loads_count FROM data_loads;
    
    RAISE NOTICE '';
    RAISE NOTICE '✅ DATABASE CLEANUP COMPLETE!';
    RAISE NOTICE '';
    RAISE NOTICE '📊 Verification - All tables should show 0 records:';
    RAISE NOTICE '   - events: % records', events_count;
    RAISE NOTICE '   - markets: % records', markets_count;
    RAISE NOTICE '   - redemptions: % records', redemptions_count;
    RAISE NOTICE '   - user_closed_positions: % records', positions_count;
    RAISE NOTICE '   - trader_leaderboard: % records', leaderboard_count;
    RAISE NOTICE '   - nft_claims: % records', nft_count;
    RAISE NOTICE '   - data_loads: % records', data_loads_count;
    RAISE NOTICE '';
    
    IF events_count = 0 AND markets_count = 0 AND redemptions_count = 0 
       AND positions_count = 0 AND leaderboard_count = 0 THEN
        RAISE NOTICE '✅ All data successfully removed!';
    ELSE
        RAISE NOTICE '⚠️  Warning: Some tables still contain data!';
    END IF;
    RAISE NOTICE '';
END $$;
