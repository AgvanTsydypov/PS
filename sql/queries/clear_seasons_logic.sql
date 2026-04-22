BEGIN;

-- 0) Safety check: убедись, что это тестовая БД
-- SELECT current_database(), current_user, now();

-- 1) Удаляем клеймы только для сезонной системы
DELETE FROM claims
WHERE season_id IN (
  SELECT id FROM seasons WHERE type IN ('genesis', 'standard')
);

-- 2) Удаляем старые genesis/standard сезоны
DELETE FROM seasons
WHERE type IN ('genesis', 'standard');

-- -- 3) Создаем чистый Genesis (активный)
-- INSERT INTO seasons (
--   type,
--   season_number,
--   start_date,
--   end_date,
--   total_supply,
--   remaining_supply,
--   is_active,
--   is_completed,
--   created_at,
--   updated_at
-- )
-- VALUES (
--   'genesis',
--   1,
--   NOW() - INTERVAL '30 days',
--   NOW() + INTERVAL '365 days',
--   40,      -- можно поменять
--   40,      -- = total_supply на старте
--   TRUE,
--   FALSE,
--   NOW(),
--   NOW()
-- );

-- -- 4) Создаем чистый Standard (активный, день 1)
-- INSERT INTO seasons (
--   type,
--   season_number,
--   start_date,
--   end_date,
--   total_supply,
--   remaining_supply,
--   is_active,
--   is_completed,
--   created_at,
--   updated_at
-- )
-- VALUES (
--   'standard',
--   1,
--   NOW(),
--   NOW() + INTERVAL '10 days',
--   30,      -- можно поменять (или 2000 для теста scheduler-логики)
--   30,      -- = total_supply на старте
--   TRUE,
--   FALSE,
--   NOW(),
--   NOW()
-- );

-- 5) (Опционально) почистить тех-логи сезонов
DELETE FROM season_events_log;

-- 6) Универсальная очистка (работает и до/после миграций):
--    * winners table rename (`winner_wallets_nft_to_claim`)
--    * preview-buffer rename (`user_generated_cards` → `preview_cards`)
DO $$
DECLARE
  preview_table TEXT := NULL;
BEGIN
  IF to_regclass('public.preview_cards') IS NOT NULL THEN
    preview_table := 'preview_cards';
  ELSIF to_regclass('public.user_generated_cards') IS NOT NULL THEN
    preview_table := 'user_generated_cards';
  END IF;

  IF to_regclass('public.winner_wallets_nft_to_claim') IS NOT NULL THEN
    IF preview_table IS NOT NULL THEN
      EXECUTE format(
        'TRUNCATE TABLE claims, seasons, season_events_log, %I, winner_wallets_nft_to_claim RESTART IDENTITY',
        preview_table
      );
    ELSE
      TRUNCATE TABLE claims, seasons, season_events_log, winner_wallets_nft_to_claim RESTART IDENTITY;
    END IF;
  ELSIF to_regclass('public.season_origin_wallets') IS NOT NULL THEN
    TRUNCATE TABLE claims, seasons, season_events_log, season_origin_wallets RESTART IDENTITY;
  ELSE
    TRUNCATE TABLE claims, seasons, season_events_log RESTART IDENTITY;
  END IF;
END $$;

-- Per-season mint numbers use a trigger (no global sequence). If an old DB still has the
-- legacy global sequence from earlier migrations, reset it so admin "Reset" leaves a clean state.
ALTER SEQUENCE IF EXISTS user_generated_cards_collection_mint_seq RESTART WITH 1;

COMMIT;