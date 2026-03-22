# PolyStars Admin Workbench — UI Reference

**URL:** `http://localhost` (nginx) → proxied to `admin_backend :8001`  
**Purpose:** Internal tool for testing and operating the NFT season lifecycle.

---

## Global controls

| Element | Description |
|---|---|
| **Refresh All** | Re-fetches `/api/overview` and `/api/seasons`; updates all season dropdowns across every tab |
| **API:** label | Shows the backend base URL (`NEXT_PUBLIC_SEASON_API_BASE_URL`) |
| Status bar | Green = last action succeeded. Red = last action failed with error detail |

A **WebSocket** connection to `/ws/events` is maintained in the background. Mint results and season-update events are pushed in real time and appended to the relevant output panels without a page reload.

---

## Tab: Overview

Displays the current state of all seasons and the recent event log.

**Season update** — clicking **Run --season-update** calls `POST /api/actions/season-update`, which runs `SimplifiedScheduler.run_standard_season_update()`. Use this to transition seasons between phases or close a completed cycle. Output streams via WebSocket.

**Seasons table:**

| Column | Meaning |
|---|---|
| `id` | Primary key |
| `type` | `genesis` or `standard` |
| `season_number` | Sequence number within its type |
| `start_date / end_date` | UTC boundaries of the 10-day cycle (standard only) |
| `total / remaining` | NFT supply counters |
| `active` | Whether this season is open for claims |
| `completed` | Whether the season has been fully closed |

**Season events log** — last 60 rows from `season_events_log`, newest-first. Shows phase transitions, supply changes, and lifecycle events.

---

## Tab: Eligibility

Tests `SeasonManager.check_user_eligibility()` for any wallet against any season.

1. Select a **season** (auto-selects the active standard season).
2. Set **Wallet filter**: `all` · `origin` · `non_origin`.
3. Click **Reload wallets** to bypass the 10-day cache.
4. Pick a wallet and click **Check eligibility**.

Key fields in the JSON response:

```json
{
  "is_origin_wallet": true,
  "genesis":  { "eligible_now": true, "phase": "scavenge", "already_claimed": false },
  "standard": { "eligible_now": false, "phase": "vault", "ineligible_reason": "..." },
  "double_mint": { "can_claim_both_now": false }
}
```

---

## Tab: Claims Mint

Manually triggers an NFT mint for a wallet.

| Field | Description |
|---|---|
| Season | Target season for the claim record |
| Wallet | EVM address of the claimant |
| Phase | `breach` / `vault` / `scavenge` — used when **Auto phase** is off |
| Chain | `solana` (Metaplex) or `base_zora` (Zora on Base) |
| Recipient | Solana pubkey or Base EVM address that receives the NFT |

| Checkbox | Effect |
|---|---|
| **Auto phase** | Detects the current phase automatically; overrides Phase dropdown |
| **DB only** | Inserts a `PENDING` record without calling any blockchain RPC |
| **Force insert** | Bypasses eligibility and phase-guard checks |

**Live timer** (left panel) — local countdown synced once from the server. Shows time alive and time remaining until each phase boundary.

**Season context** (right panel) — supply, current phase, transition rules, and a per-wallet checklist (`is_origin_wallet`, `already_claimed`, `eligible_now`).

---

## Tab: Season Claims

Live view of all claim records for the selected season. Auto-refreshes every **3 seconds**.

**Stats block** — `total_claims`, `completed_claims`, `pending_claims`, `failed_claims`, and per-phase counters.

**Claims table:**

| Column | Description |
|---|---|
| `wallet` | EVM address of the claimant |
| `recipient` | Address that received the NFT |
| `phase` | `breach` / `vault` / `scavenge` |
| `status` | `PENDING` → `PROCESSING` → `COMPLETED` / `FAILED` |
| `tx_hash` | Blockchain transaction hash |
| `asset_address` | On-chain NFT address (Solana) or token ID (Zora) |

---

## Tab: Scenarios

Manipulates season DB records directly for testing phase transitions without waiting real calendar time.

**Quick phase buttons** — set `start_date` so the season is perceived to be at a specific day:

| Button | Days since start | Phase entered |
|---|---|---|
| Set Breach (day 2) | 1 | Breach |
| Set Vault (day 5) | 4 | Vault |
| Set Scavenge (day 8) | 7 | Scavenge |
| Set Transmission (day 10) | 9 | Transmission |

**Date shift** — shifts `start_date` by N days from now. Negative = future start, positive = past start. `end_date` is recalculated preserving the original cycle duration.

**Set remaining_supply** — directly updates `remaining_supply`. Setting it to `0` also marks `is_active=false` and `is_completed=true`.

**Advanced params** — full edit of all season fields at once. **Set now as start (+10d end)** fills dates for a fresh 10-day cycle from the current moment.

---

## Tab: Reset

Wipes all season data using `sql/queries/clear_seasons_logic.sql`.

Affected tables: `seasons`, `claims`, `season_events_log`, `winner_wallets_nft_to_claim`.

Check the confirmation checkbox, then click **Run reset SQL**.

> **Warning:** development/staging only — irreversible.
