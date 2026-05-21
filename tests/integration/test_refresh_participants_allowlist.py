"""
Integration test for the event allowlist on ``refresh_participants_for_season``.

Validates the schema change end-to-end against a real PostgreSQL instance: when
the caller passes an explicit ``p_event_ids`` array (the TOP20/TAG5 selection
for standard seasons), only those events' participants are materialized into the
season partition; passing NULL keeps the previous "everything in the window"
behavior (genesis).

Seeds the minimal base rows the ``participants_analytics`` view reads from
(events, event_resolution_queue, user_closed_positions), then calls the SQL
function directly with and without the allowlist.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from tests.integration.conftest import make_real_connection


_E1 = "fwtest-evt-1"
_E2 = "fwtest-evt-2"
_S1 = "fwtest-slug-1"
_S2 = "fwtest-slug-2"
_W1 = "0x" + "d1" + "0" * 38
_W2 = "0x" + "d2" + "0" * 38
_SEASON_NUMBER = 77777


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _seed(cur, resolution_ready_at: datetime) -> None:
    for eid, slug, wallet in ((_E1, _S1, _W1), (_E2, _S2, _W2)):
        cur.execute(
            "INSERT INTO events (id, slug, title, volume) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET slug = EXCLUDED.slug",
            (eid, slug, "fw test event", 1_000_000),
        )
        cur.execute(
            """
            INSERT INTO event_resolution_queue (event_id, status, closed, resolution_ready_at)
            VALUES (%s, 'processed', TRUE, %s)
            ON CONFLICT (event_id) DO UPDATE
            SET status = 'processed', closed = TRUE, resolution_ready_at = EXCLUDED.resolution_ready_at
            """,
            (eid, resolution_ready_at),
        )
        cur.execute(
            """
            INSERT INTO user_closed_positions
                (proxy_wallet, event_id, event_slug, avg_price, total_bought, realized_pnl, timestamp_unix)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (wallet, eid, slug, 0.50, 100, 25, 1_700_000_000),
        )


def _insert_standard_season(cur) -> int:
    start = _now_utc()
    cur.execute(
        """
        INSERT INTO seasons
            (type, season_number, start_date, end_date, total_supply, remaining_supply, is_active)
        VALUES ('standard', %s, %s, %s, 100, 100, TRUE)
        RETURNING id
        """,
        (_SEASON_NUMBER, start, start + timedelta(days=10)),
    )
    return cur.fetchone()[0]


def _refresh(cur, season_id, ws, we, allowlist):
    cur.execute(
        "SELECT refresh_participants_for_season(%s, %s, %s, %s, %s::text[]) AS n",
        (season_id, ws, we, True, allowlist),
    )
    return cur.fetchone()[0]


def _partition_event_ids(cur, season_id):
    cur.execute(
        "SELECT event_id FROM participants WHERE season_id = %s ORDER BY event_id",
        (season_id,),
    )
    return [r[0] for r in cur.fetchall()]


@pytest.fixture()
def seeded_season():
    conn = make_real_connection()
    sid = None
    try:
        with conn.cursor() as cur:
            sid = _insert_standard_season(cur)
            cur.execute("SELECT participants_ensure_partition(%s)", (sid,))
            _seed(cur, resolution_ready_at=_now_utc())
        conn.commit()
        yield {"conn": conn, "season_id": sid}
    finally:
        with conn.cursor() as cur:
            if sid is not None:
                cur.execute("SELECT participants_drop_partition(%s)", (sid,))
                cur.execute("DELETE FROM seasons WHERE id = %s", (sid,))
            cur.execute("DELETE FROM user_closed_positions WHERE event_id = ANY(%s)", ([_E1, _E2],))
            cur.execute("DELETE FROM event_resolution_queue WHERE event_id = ANY(%s)", ([_E1, _E2],))
            cur.execute("DELETE FROM events WHERE id = ANY(%s)", ([_E1, _E2],))
        conn.commit()
        conn.close()


class TestRefreshParticipantsAllowlist:
    def test_null_allowlist_loads_all_windowed_events(self, seeded_season):
        conn = seeded_season["conn"]
        sid = seeded_season["season_id"]
        ws, we = _now_utc() - timedelta(days=1), _now_utc() + timedelta(days=1)
        with conn.cursor() as cur:
            n = _refresh(cur, sid, ws, we, None)
            conn.commit()
            assert n == 2
            assert _partition_event_ids(cur, sid) == [_E1, _E2]

    def test_allowlist_restricts_partition_to_listed_events(self, seeded_season):
        conn = seeded_season["conn"]
        sid = seeded_season["season_id"]
        ws, we = _now_utc() - timedelta(days=1), _now_utc() + timedelta(days=1)
        with conn.cursor() as cur:
            n = _refresh(cur, sid, ws, we, [_E1])
            conn.commit()
            assert n == 1
            assert _partition_event_ids(cur, sid) == [_E1]

    def test_empty_allowlist_yields_empty_partition(self, seeded_season):
        # An empty (non-NULL) allowlist means "no qualifying events" -> 0 rows,
        # distinct from NULL which means "no cap".
        conn = seeded_season["conn"]
        sid = seeded_season["season_id"]
        ws, we = _now_utc() - timedelta(days=1), _now_utc() + timedelta(days=1)
        with conn.cursor() as cur:
            n = _refresh(cur, sid, ws, we, [])
            conn.commit()
            assert n == 0
            assert _partition_event_ids(cur, sid) == []
