# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**PS** is a data platform for Polymarket ingestion, analytics, season lifecycle management, and NFT card minting on Solana. It combines:
- A scheduled data pipeline pulling from the Polymarket API into PostgreSQL
- AI generation of trading card content (titles, lore, tag colors) via Claude/Gemini
- SVG/PNG card rendering and upload to Cloudflare R2 / Pinata
- An admin workbench (FastAPI + Next.js) to manage seasons, winners, and minting
- A user-facing platform (FastAPI + Next.js) for wallet auth and NFT claims

## Commands

### Python Backend
```bash
python -m venv venv && source venv/bin/activate  # or .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn admin_backend.main:app --port 8001 --reload
uvicorn user_web_backend.main:app --port 8011 --reload
```

### Node Frontend
```bash
cd admin_frontend && npm install && npm run dev        # :3000
cd user_web_frontend && npm install && npm run dev -- -p 3001  # :3001
npm run lint   # Next.js linting (run from within frontend dirs)
npm run build
```

### Docker (full stack)
```bash
docker compose up -d
docker exec polystars_scheduler python scripts/daily_scheduler_simple.py --run
```

### Data Pipeline
```bash
python scripts/daily_scheduler_simple.py --check              # inspect state
python scripts/daily_scheduler_simple.py --historical --auto-catchup  # Genesis load
python scripts/daily_scheduler_simple.py --run                # manual daily run
python scripts/daily_scheduler_simple.py --catch-up           # backfill missing dates
python scripts/backfill_event_cards.py --batch-size 25
python scripts/db/backfill_tag_colors.py --batch-size 50
```

### Database
```bash
psql -h localhost -U postgres -d polystars -f sql/schemas/init-db.sql  # init schema
```

### Tests
```bash
# Run all tests
venv/Scripts/python.exe -m pytest tests/ --tb=short -q

# Run a specific file
venv/Scripts/python.exe -m pytest tests/test_simulate_batch_helpers.py -v

# Run a specific test class or test
venv/Scripts/python.exe -m pytest tests/test_scenarios_service.py::TestApplyAdvancedScenarioValidation -v

# Run only failed tests from last run
venv/Scripts/python.exe -m pytest tests/ --lf --tb=short
```

> Python interpreter: always use `venv/Scripts/python.exe` (not the system `python` at C:\Python314).
> conftest.py stubs psycopg2 and scripts.cardgen.assets globally — no live DB or browser needed.

## Architecture

### Directory Layout
```
admin_backend/main.py          # FastAPI admin API (~3300 lines), port 8001
admin_frontend/app/page.tsx    # React admin UI (~6000 lines), port 3000
user_web_backend/main.py       # FastAPI user API (~3400 lines), port 8011
user_web_frontend/             # React user UI, port 3001
scripts/
  daily_scheduler_simple.py    # Pipeline orchestrator
  season_manager.py            # Season state machine
  solana_service.py            # Solana NFT minting (Metaplex)
  polystars_card_payload.py    # Metadata builder + R2/Pinata upload
  ai/                          # AI card/color generation agents
  cardgen/                     # SVG → PNG rendering (Playwright)
  fetch/                       # Parallel Polymarket API fetchers
  db/                          # DB utilities and backfill scripts
sql/schemas/init-db.sql        # Full PostgreSQL schema
```

### Core Data Flows

**Daily pipeline** (`daily_scheduler_simple.py`):
Polymarket API → parallel fetchers → DataLoadingManager (date validation, volume filter) → PostgreSQL → AI backfill (event_cards, tag_colors) → WebSocket broadcast to admin UI.

- Genesis load: 2024-07-06 to 2026-01-05, 100M USD min volume (one-time)
- Daily: previous day events (1-day lag), redemptions with 3-day lag, 5M USD filter
- Configurable via `GENESIS_*`, `DAILY_MIN_VOLUME`, `POLYSTARS_EVENTS_LAG_DAYS` env vars

**AI card generation** (`scripts/ai/`):
Event data → Agent1 (Claude API) → card title, lore, metrics → `event_cards` table → Agent2 (Gemini) → hex colors → `tags.hex_color`.
Models controlled by `POLYSTARS_EVENT_CARDS_MODEL` and `POLYSTARS_TAG_COLORS_MODEL` (default: `gemini-2.5-flash`).

**Season lifecycle** (`season_manager.py`):
Four phases: Breach (days 1–3, 20% supply cap) → Vault (days 4–6, Origins only) → Scavenge (days 7–9) → Transmission (day 10, closed). Each season's eligible Origin pool is stored in the `participants` partitioned table (one partition per `season_id`).

**NFT minting** (queue model, `polystars_card_payload.py`, `cardgen/`, `daily_scheduler_simple.py:process_mint_queue`):
User clicks Mint → admin/user API allocates a participant row (origin: best-archetype self-row; looter: random unclaimed) and INSERTs a `claims` row with `status='QUEUED'` carrying the full snapshot. The daily cron worker picks QUEUED rows under `FOR UPDATE SKIP LOCKED`, builds the SVG/PNG card, uploads to Pinata, submits the EVM mint, and finalizes the row to `COMPLETED`. Caps (`total_supply`, `per_event_cap`) are enforced at insert time by the `claims_check_caps` trigger on the small claims table, not on the multi-million-row participants table.

**User auth**: SIWE (Sign-In With Ethereum) via wagmi/ConnectKit → verify signature + Polymarket proxy wallet check → JWT cookie → eligibility check via the active season's `participants` partition.

### Key Database Tables
`events`, `markets`, `redemptions`, `positions`, `leaderboard` — Polymarket data.
`event_cards` — AI-generated card content per event.
`tags`, `event_tags` — normalized tag metadata with hex colors.
`seasons`, `participants` (partitioned by season_id), `claims` — season lifecycle, eligible-Origin pool, and NFT mint queue / completion log.
`preview_cards` — preview-only buffer for the home-showcase ticker; cleared by the cron worker on successful mint.

### Admin API Tabs (port 8001)
Overview · Eligibility · Claims · Event Cards · Event Pictures · Scenarios · User Web · Reset

### User API (port 8011)
SIWE auth flow, `/api/me/eligibility`, `/api/me/mint` (queues a claim), `/api/master-collection`.

## Required Environment Variables

```env
# PostgreSQL
DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SSLMODE

# AI
GOOGLE_API_KEY       # Gemini
ANTHROPIC_API_KEY    # Claude

# Solana
SOLANA_RPC_URL
SOLANA_KEYPAIR_PATH  # path to JSON keypair file

# NFT storage (one or both)
R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_ACCESS_KEY_SECRET, R2_BUCKET_NAME
PINATA_JWT

# Auth
SESSION_SECRET       # 32+ char secret for JWT signing
```

Notable optional overrides: `LOCAL_DB_*` vars for local overrides, `DOCKER_DB_HOST=host.docker.internal`, `USER_WEB_WALLET_ACTIONS_DISABLED=1` to freeze minting, `WALLETS_CACHE_TTL_SECONDS`.
