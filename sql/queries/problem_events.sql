WITH event_red_stats AS (
      SELECT
          r.event_id,
          COUNT(*)                                        AS redemptions_total,
          COUNT(*) FILTER (WHERE r.payout_usdc > 0)       AS winning_redemptions,
          COUNT(DISTINCT r.redeemer_address)
              FILTER (WHERE r.payout_usdc > 0)            AS distinct_winners,
          COUNT(DISTINCT r.condition_id)
              FILTER (WHERE r.payout_usdc > 0)            AS distinct_winning_markets,
          MAX(r.timestamp_human)                          AS last_redemption_at
      FROM public.redemptions r
      WHERE r.event_id IS NOT NULL
      GROUP BY r.event_id
  ),
  event_pos_stats AS (
      SELECT event_id, COUNT(*) AS positions_loaded
      FROM public.user_closed_positions
      WHERE event_id IS NOT NULL
      GROUP BY event_id
  )
  SELECT
      e.id                                AS event_id,
      e.title,
      e.volume,
      e.end_date,
      erq.status                          AS queue_status,
      erq.closed_time,
      erq.resolution_ready_at,
      erq.processed_at,
      ers.redemptions_total,
      ers.winning_redemptions,
      ers.distinct_winners,
      ers.distinct_winning_markets,
      COALESCE(eps.positions_loaded, 0)   AS positions_loaded,
      -- наиболее вероятная причина пропуска
      CASE
          WHEN ers.winning_redemptions = 0
              THEN 'no_winning_redemptions (payout_usdc > 0 = 0)'
          WHEN e.volume IS NULL OR e.volume < 5000000
              THEN 'volume_below_min_volume_filter ($5M)'
          WHEN COALESCE(eps.positions_loaded, 0) = 0
              THEN 'api_or_retry_failure (check positions_fetch_*.log)'
          ELSE 'other'
      END                                 AS likely_cause
  FROM public.events e
  JOIN event_red_stats ers ON ers.event_id = e.id
  LEFT JOIN event_pos_stats eps ON eps.event_id = e.id
  LEFT JOIN public.event_resolution_queue erq ON erq.event_id = e.id
  WHERE COALESCE(eps.positions_loaded, 0) = 0
    AND ers.winning_redemptions > 0
  ORDER BY e.volume DESC NULLS LAST, ers.winning_redemptions DESC;