# PS

PS is a data platform for Polymarket ingestion, analytics, season lifecycle operations, and NFT claim workflows.

This repository includes:
- a production-style data pipeline (`events`, `markets`, `redemptions`, `positions`, `leaderboard`),
- a normalized PostgreSQL schema,
- AI generation for `event_cards` and tag colors,
- an admin workbench (FastAPI + Next.js),
- a separate user backend/frontend.

## Repository Structure

- `scripts/` - data loaders, scheduler, backfill tools, AI agents
- `sql/schemas/init-db.sql` - main database schema and analytics views
- `admin_backend/` + `admin_frontend/` - admin workbench API and UI
- `user_web_backend/` + `user_web_frontend/` - user-facing services
- `docs/` - setup and API documentation

## Quick Start (Local)

Requirements:
- Python 3.11+ (3.12 recommended)
- Node.js 18+
- PostgreSQL

### 1) Install dependencies

```bash
# Python
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Frontends
cd admin_frontend && npm install
cd ../user_web_frontend && npm install
cd ..
```

### 2) Configure environment

Create `.env` in the project root:

```env
# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=polystars
DB_USER=postgres
DB_PASSWORD=postgres
DB_SSLMODE=prefer

# Optional local override variables
LOCAL_DB_HOST=localhost
LOCAL_DB_PORT=5432
LOCAL_DB_NAME=polystars
LOCAL_DB_USER=postgres
LOCAL_DB_PASSWORD=postgres

# Gemini / AI
GOOGLE_API_KEY=your_google_api_key
POLYSTARS_EVENT_CARDS_MODEL=gemini-2.5-flash
POLYSTARS_TAG_COLORS_MODEL=gemini-2.5-flash
```

### 3) Initialize the database

```bash
psql -h localhost -U postgres -d polystars -f sql/schemas/init-db.sql
```

### 4) Run services

```bash
# Backend APIs
uvicorn admin_backend.main:app --host 0.0.0.0 --port 8001 --reload
uvicorn user_web_backend.main:app --host 0.0.0.0 --port 8011 --reload

# Frontends (separate terminals)
cd admin_frontend && npm run dev
cd user_web_frontend && npm run dev -- -p 3001
```

For full local run examples, see `LOCAL_RUN.md`.

## Quick Start (Docker)

```bash
docker compose up -d
docker compose ps
```

After startup:
- admin frontend is typically available at `http://localhost`,
- additional local endpoints can be verified using `LOCAL_RUN.md`.

## Core Data Commands

### Scheduler

```bash
# Check current state
python scripts/daily_scheduler_simple.py --check

# Initial historical load (recommended first run)
python scripts/daily_scheduler_simple.py --historical --auto-catchup

# Daily pipeline
python scripts/daily_scheduler_simple.py --run

# Manual catch-up
python scripts/daily_scheduler_simple.py --catch-up
```

### Backfill and AI

```bash
# Backfill normalized metadata: series/tags/event_tags + events.series_id
python scripts/db/backfill_events_metadata.py --missing-only

# Backfill event cards
python scripts/backfill_event_cards.py --batch-size 25

# Backfill tag colors (tags.hex_color)
python scripts/db/backfill_tag_colors.py --batch-size 50
```

## Database and Data Model Highlights

- `tags.hex_color` with hex-format constraint and index
- extended `event_cards` fields:
  - `secondary_tag`
  - `agent_name`
  - `model_name`
  - `prompt_version`
  - `status`
  - `error_text`
- normalized metadata layer:
  - `series`
  - `tags`
  - `event_tags`
  - `events.series_id`
- admin API endpoint `/api/event-cards` includes tag color data in responses

## Admin Workbench

Admin stack:
- API: `admin_backend/main.py` (default `:8001`)
- UI: `admin_frontend/app/page.tsx` (default `:3000`)

Main capabilities:
- season lifecycle controls
- claims workflow and mint operations
- winner wallet management
- event card review/edit/regenerate UI

See `docs/ADMIN_UI.md` for endpoint and tab-level behavior.

## Useful Documentation

- `docs/SETUP.md` - full setup guide
- `docs/DATABASE_SETUP.md` - database setup details
- `docs/API.md` - Flask API (`app.py`) reference
- `docs/ADMIN_UI.md` - admin workbench reference
- `sql/schemas/README.md` - SQL schema notes

## License

MIT. See `LICENSE`.