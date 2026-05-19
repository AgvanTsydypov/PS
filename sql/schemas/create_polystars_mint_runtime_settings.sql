-- Runtime gas-tier selector for the cron mint worker.
-- Singleton row read by scripts/daily_scheduler_simple.process_mint_queue:
-- the worker picks GasTrackerSnapshot.<tier>_gwei for the broadcast and
-- <tier>_usd for the price-gate threshold comparison. Admin mutates this
-- via PUT /api/mint-settings/speed-tier so changes apply to the next claim
-- picked off the queue without restarting any container.

CREATE TABLE IF NOT EXISTS polystars_mint_runtime_settings (
    singleton_id    SMALLINT PRIMARY KEY CHECK (singleton_id = 1),
    mint_speed_tier TEXT NOT NULL DEFAULT 'safe'
        CHECK (mint_speed_tier IN ('safe', 'propose', 'rapid')),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO polystars_mint_runtime_settings (singleton_id, mint_speed_tier)
VALUES (1, 'safe')
ON CONFLICT (singleton_id) DO NOTHING;
