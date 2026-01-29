SELECT DISTINCT
    r.event_id,
    r.condition_id,
    e.title AS event_title,
    r.redeemer_address
FROM public.redemptions r
JOIN public.events e ON r.event_id = e.id
WHERE r.event_id IS NOT NULL
  AND e.volume > 100000000
  AND r.payout_usdc > 0
ORDER BY r.event_id, r.condition_id, r.redeemer_address;
