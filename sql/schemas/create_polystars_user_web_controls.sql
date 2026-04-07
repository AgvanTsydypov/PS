-- Runtime toggle for user_web_backend wallet-linked routes (eligibility, mint, /api/me/*, etc.).
-- Created automatically on admin_backend startup; this file is for manual / migration use.

CREATE TABLE IF NOT EXISTS polystars_user_web_controls (
    singleton_id SMALLINT PRIMARY KEY CHECK (singleton_id = 1),
    wallet_actions_disabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO polystars_user_web_controls (singleton_id, wallet_actions_disabled)
VALUES (1, FALSE)
ON CONFLICT (singleton_id) DO NOTHING;
