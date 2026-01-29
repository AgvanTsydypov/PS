SELECT
    proxy_wallet,
    condition_id,
    avg_price,
    event_id,
    market_id,
    title,
    outcome
FROM public.user_closed_positions
WHERE condition_id IS NOT NULL
  AND NOT (
    (avg_price >= 0.0 AND avg_price < 0.3) OR
    (avg_price >= 0.3 AND avg_price < 0.6) OR
    (avg_price >= 0.6 AND avg_price < 0.9) OR
    (avg_price >= 0.9)
  )
ORDER BY avg_price;
