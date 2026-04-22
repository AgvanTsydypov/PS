"""
End-to-end style integration tests: real DB rows + HTTP handlers.

1. Admin TestClient: eligibility for a concrete ``season_id`` then claim season info.
2. User-web TestClient: public ``/api/seasons/active`` with patched ``SeasonManager`` + psycopg2.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import mock

import psycopg2
import pytest
from fastapi.testclient import TestClient

import scripts.season_manager as season_manager_mod
import user_web_backend.main as user_web_mod

from tests.integration.conftest import make_real_connection

_JOURNEY_SEASON_NUMBER = 88991
_JOURNEY_WALLET = "0x" + "5" * 40


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture()
def breach_season_and_origin_wallet():
    """Standard season in Breach + one origin row; tear down in FK-safe order."""
    conn = make_real_connection()
    season_id = None
    try:
        with conn.cursor() as cur:
            start = _now_utc() - timedelta(days=1)
            end = start + timedelta(days=30)
            cur.execute(
                """
                INSERT INTO seasons
                    (type, season_number, start_date, end_date,
                     total_supply, remaining_supply, is_active)
                VALUES ('standard', %s, %s, %s, 100, 100, TRUE)
                RETURNING id
                """,
                (_JOURNEY_SEASON_NUMBER, start, end),
            )
            season_id = int(cur.fetchone()[0])
            cur.execute(
                """
                INSERT INTO winner_wallets_nft_to_claim
                    (season_id, proxy_wallet, window_start, window_end,
                     is_minted, minted_to_wallet)
                VALUES (%s, %s, NOW(), NOW() + INTERVAL '30 days', FALSE, NULL)
                """,
                (season_id, _JOURNEY_WALLET),
            )
        conn.commit()
        yield season_id, _JOURNEY_WALLET
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            with conn.cursor() as cur:
                if season_id is not None:
                    cur.execute("DELETE FROM claims WHERE season_id = %s", (season_id,))
                    cur.execute(
                        "DELETE FROM winner_wallets_nft_to_claim WHERE season_id = %s",
                        (season_id,),
                    )
                    cur.execute("DELETE FROM seasons WHERE id = %s", (season_id,))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()


class TestAdminUserJourney:

    def test_eligibility_then_claim_season_info(
        self, admin_api_client, breach_season_and_origin_wallet
    ):
        season_id, wallet = breach_season_and_origin_wallet

        r1 = admin_api_client.post(
            "/api/eligibility",
            json={"wallet": wallet, "season_id": season_id},
        )
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1.get("selected_season_id") == season_id
        sel = body1.get("selected_season") or {}
        assert sel.get("phase") == "breach"
        assert sel.get("eligible_now") is True

        r2 = admin_api_client.get(
            "/api/claims/season-info",
            params={
                "season_id": season_id,
                "wallet": wallet,
                "auto_phase": "true",
                "manual_phase": "breach",
            },
        )
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2.get("phase") == "breach"
        assert "lines" in body2
        joined = "\n".join(body2["lines"])
        assert "eligible_now: True" in joined


class TestUserWebPublicJourney:

    def test_active_seasons_returns_list(self):
        with mock.patch.object(season_manager_mod, "psycopg2", psycopg2):
            with mock.patch.object(user_web_mod, "psycopg2", psycopg2):
                prev = user_web_mod.season_manager
                user_web_mod.season_manager = season_manager_mod.SeasonManager(use_local_db=True)
                try:
                    client = TestClient(user_web_mod.app)
                    r = client.get("/api/seasons/active")
                finally:
                    user_web_mod.season_manager = prev

        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        if data:
            row = data[0]
            assert "id" in row and "type" in row
