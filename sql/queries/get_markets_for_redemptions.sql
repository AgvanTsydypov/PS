-- Get all markets for redemption fetching
-- Used by: fetch_redemptions.py
--
-- Retrieves markets with:
-- - condition_id (required for Goldsky GraphQL API)
-- - event information (via JOIN)
-- - volume (handles text/numeric conversion)
-- - sorted by volume DESC (largest markets first)
--
-- Additional filters (closed, volume, limit) added dynamically in Python

SELECT 
    m.id as market_id,
    m.condition_id,
    m.question,
    m.event_id,
    e.title as event_title,
    m.closed,
    COALESCE(
        CASE 
            WHEN m.volume IS NOT NULL AND m.volume <> '' 
            THEN m.volume::numeric 
            ELSE NULL 
        END,
        m.volume_num,
        0
    ) as volume
FROM markets m
LEFT JOIN events e ON m.event_id = e.id
WHERE m.condition_id IS NOT NULL
-- ORDER BY COALESCE(
--     CASE 
--         WHEN m.volume IS NOT NULL AND m.volume <> '' 
--         THEN m.volume::numeric 
--         ELSE NULL 
--     END,
--     m.volume_num,
--     0
-- ) DESC
