-- ============================================================================
-- Get Unique Wallet Addresses from Redemptions
-- ============================================================================
-- This query is used by fetch_trader_leaderboard_parallel.py with --from-db flag
-- to get list of wallets for leaderboard rank fetching
--
-- Source: redemptions table (same as closed positions)
-- Ensures consistency between different data fetching scripts
--
-- Usage in script:
--   python fetch_trader_leaderboard_parallel.py --upload --local --from-db
--
-- Manual usage:
--   psql -U your_user -d your_database -f sql/queries/unique_wallet_addresses.sql

-- ============================================================================
-- MAIN QUERY
-- ============================================================================

SELECT DISTINCT redeemer_address
FROM public.redemptions
WHERE redeemer_address IS NOT NULL
  AND redeemer_address != ''
ORDER BY redeemer_address;

-- ============================================================================
-- STATISTICS
-- ============================================================================

-- Count unique wallets
-- SELECT COUNT(DISTINCT redeemer_address) as unique_wallets
-- FROM public.redemptions
-- WHERE redeemer_address IS NOT NULL
--   AND redeemer_address != '';

-- ============================================================================
-- SAMPLE OUTPUT
-- ============================================================================

-- redeemer_address
-- ----------------------------------------
-- 0x0000000000000000000000000000000000000001
-- 0x0000000000000000000000000000000000000002
-- 0x0123456789abcdef0123456789abcdef01234567
-- ...

-- ============================================================================
-- NOTES
-- ============================================================================

-- 1. Same source as fetch_user_closed_positions_parallel.py
--    Ensures we track the same set of users across different metrics
--
-- 2. Memory-efficient batch processing
--    The script uses server-side cursors to process wallets in batches
--    Default batch size: 10,000 wallets
--
-- 3. Can be filtered further if needed
--    Example: Filter by specific markets or events
--    
--    SELECT DISTINCT r.redeemer_address
--    FROM public.redemptions r
--    JOIN public.events e ON e.condition_id = r.condition_id
--    WHERE e.volume > 100000000  -- Only high-volume events
--      AND r.redeemer_address IS NOT NULL;
--
-- 4. Performance considerations
--    - Uses DISTINCT: efficient with indexes on redeemer_address
--    - ORDER BY: ensures consistent ordering for pagination
--    - Batch processing: avoids memory issues with large datasets
