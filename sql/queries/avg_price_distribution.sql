WITH categorized AS (
    SELECT
        proxy_wallet,
        condition_id,
        CASE
            WHEN avg_price >= 0.0 AND avg_price < 0.3 THEN '0.0 to 0.3'
            WHEN avg_price >= 0.3 AND avg_price < 0.6 THEN '0.3 to 0.6'
            WHEN avg_price >= 0.6 AND avg_price < 0.9 THEN '0.6 to 0.9'
            WHEN avg_price >= 0.9 THEN 'over 0.9'
            ELSE 'other'
        END AS price_range,
        CASE
            WHEN avg_price >= 0.0 AND avg_price < 0.3 THEN 1
            WHEN avg_price >= 0.3 AND avg_price < 0.6 THEN 2
            WHEN avg_price >= 0.6 AND avg_price < 0.9 THEN 3
            WHEN avg_price >= 0.9 THEN 4
            ELSE 5
        END AS sort_order
    FROM public.user_closed_positions
    WHERE condition_id IS NOT NULL
)
SELECT
    price_range,
    COUNT(DISTINCT (proxy_wallet, condition_id)) AS unique_count
FROM categorized
GROUP BY price_range, sort_order
ORDER BY sort_order;
