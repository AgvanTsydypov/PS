-- ⚠️ ВАЖНО: Этот запрос будет параметризован из Python кода
-- Параметры: date_from, date_to, min_volume
-- Фильтрует redemptions ТОЛЬКО для событий закрывшихся в указанную дату

SELECT DISTINCT
    r.event_id,
    r.condition_id,
    e.title AS event_title,
    r.redeemer_address,
    e.end_date  -- Добавляем для отладки
FROM public.redemptions r
JOIN public.events e ON r.event_id = e.id
WHERE r.event_id IS NOT NULL
  AND e.volume >= %(min_volume)s  -- Динамический параметр из конфигурации
  AND r.payout_usdc > 0
  -- Фильтры добавляются динамически из Python:
  -- AND e.end_date::date >= :date_from
  -- AND e.end_date::date <= :date_to
ORDER BY r.event_id, r.condition_id, r.redeemer_address;
