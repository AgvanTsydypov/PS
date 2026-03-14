-- ============================================================================
-- CLEAN ALL DATA FROM POLYSTARS DATABASE
-- ============================================================================
-- This script removes all rows from ALL user tables while preserving schema.
-- It truncates every table in every non-system schema and resets identities.
-- ⚠️  WARNING: This will delete ALL application data from the database!
-- ============================================================================

DO $$ BEGIN RAISE NOTICE '🧹 Starting full database cleanup...'; END $$;

-- ============================================================================
-- Truncate every user table in all non-system schemas
-- ============================================================================
DO $$
DECLARE
    tables_to_truncate TEXT;
BEGIN
    SELECT STRING_AGG(FORMAT('%I.%I', schemaname, tablename), ', ')
    INTO tables_to_truncate
    FROM pg_catalog.pg_tables
    WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
      AND schemaname NOT LIKE 'pg_toast%'
      AND schemaname NOT LIKE 'pg_temp_%';

    IF tables_to_truncate IS NULL THEN
        RAISE NOTICE 'ℹ️  No user tables found to truncate.';
        RETURN;
    END IF;

    EXECUTE FORMAT(
        'TRUNCATE TABLE %s RESTART IDENTITY CASCADE',
        tables_to_truncate
    );

    RAISE NOTICE '✅ Truncated all user tables with RESTART IDENTITY CASCADE.';
END $$;

-- ============================================================================
-- Verify cleanup (all user tables should be empty)
-- ============================================================================
DO $$
DECLARE
    table_rec RECORD;
    table_rows BIGINT;
    non_empty_count INTEGER := 0;
    total_checked INTEGER := 0;
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '📊 Verifying table row counts...';

    FOR table_rec IN
        SELECT schemaname, tablename
        FROM pg_catalog.pg_tables
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
          AND schemaname NOT LIKE 'pg_toast%'
          AND schemaname NOT LIKE 'pg_temp_%'
        ORDER BY schemaname, tablename
    LOOP
        EXECUTE FORMAT(
            'SELECT COUNT(*) FROM %I.%I',
            table_rec.schemaname,
            table_rec.tablename
        ) INTO table_rows;

        total_checked := total_checked + 1;
        RAISE NOTICE '   - %.%: % rows', table_rec.schemaname, table_rec.tablename, table_rows;

        IF table_rows > 0 THEN
            non_empty_count := non_empty_count + 1;
        END IF;
    END LOOP;

    RAISE NOTICE '';
    IF total_checked = 0 THEN
        RAISE NOTICE '✅ Verification complete: no user tables found.';
    ELSIF non_empty_count = 0 THEN
        RAISE NOTICE '✅ DATABASE CLEANUP COMPLETE: all % user tables are empty.', total_checked;
    ELSE
        RAISE NOTICE '⚠️  Cleanup finished, but % table(s) still contain data.', non_empty_count;
    END IF;
    RAISE NOTICE '';
END $$;
