WITH event_stats AS (
    SELECT
        r.event_id,
        COUNT(DISTINCT r.redeemer_address) AS unique_redeemers
    FROM public.redemptions r
    JOIN public.events e ON r.event_id = e.id
    WHERE r.event_id IS NOT NULL
      AND e.volume > 100000000
    GROUP BY r.event_id
),
min_event AS (
    SELECT event_id
    FROM event_stats
    WHERE unique_redeemers = (
        SELECT MIN(unique_redeemers) FROM event_stats
    )
)
SELECT DISTINCT
    r.event_id,
    r.condition_id,
    e.title AS event_title,
    r.redeemer_address
FROM public.redemptions r
JOIN min_event me ON r.event_id = me.event_id
JOIN public.events e ON r.event_id = e.id
ORDER BY r.event_id, r.condition_id, r.redeemer_address;