"""
Integration test infrastructure.

The parent tests/conftest.py stubs psycopg2 globally (via setdefault) so unit
tests never need a live database.  This sub-conftest undoes that mock at
import time — before any integration test module is loaded — so that
make_real_connection() and _DirectDBManager return genuine psycopg2 objects.

Database source of truth for these tests is an ephemeral Postgres container
spun up via testcontainers, with sql/schemas/init-db.sql and
sql/schemas/create_seasons_system.sql applied at session start.  Set
POLYSTARS_USE_LIVE_DB=1 to bypass the container and use LOCAL_DB_* / DB_*
env vars instead (legacy path; expects a pre-provisioned DB).

Scripts imported for integration tests (e.g. polystars_card_payload) are
already cached in sys.modules from the unit-test run.  That is intentional:
denormalize_card_onto_claim never calls psycopg2.connect() directly; it calls
manager.get_connection(), which we supply as _DirectDBManager below.  The
cached module state therefore does not affect correctness.
"""

import atexit
import os
import sys

import dotenv

# Load .env so DB_* variables are available when running pytest outside Docker.
dotenv.load_dotenv(
    os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
    override=False,
)

# ------------------------------------------------------------------
# Restore the real psycopg2 — remove whatever the parent conftest put there.
# ------------------------------------------------------------------
for _key in [k for k in list(sys.modules) if k.startswith("psycopg2")]:
    del sys.modules[_key]

import psycopg2          # noqa: E402  — must come after the mock is cleared
import psycopg2.extras  # noqa: E402

_real_psycopg2 = psycopg2  # stable reference used by season_manager patcher

# ------------------------------------------------------------------
# Ephemeral Postgres via testcontainers (default).
# Schemas are applied once per pytest session.  Container is torn down at
# interpreter exit.  Override env vars are populated *before* _DSN is built
# below so make_real_connection() reaches the container transparently.
# ------------------------------------------------------------------
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SCHEMA_FILES = (
    os.path.join(_REPO_ROOT, "sql", "schemas", "init-db.sql"),
    os.path.join(_REPO_ROOT, "sql", "schemas", "create_seasons_system.sql"),
)


def _apply_schemas(dsn: dict) -> None:
    for path in _SCHEMA_FILES:
        with open(path, "r", encoding="utf-8") as fh:
            sql_text = fh.read()
        conn = psycopg2.connect(**dsn)
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql_text)
        finally:
            conn.close()


def _start_ephemeral_postgres() -> None:
    """Start a Postgres container, apply schemas, export LOCAL_DB_* env vars."""
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine")
    container.start()
    atexit.register(container.stop)

    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(5432))
    dbname = container.dbname
    user = container.username
    password = container.password

    _apply_schemas(dict(host=host, port=port, dbname=dbname, user=user, password=password))

    # Force every code path that reads env vars (DataLoadingManager, SeasonManager,
    # etc.) onto the container, regardless of what was in .env.
    os.environ["LOCAL_DB_HOST"] = host
    os.environ["LOCAL_DB_PORT"] = str(port)
    os.environ["LOCAL_DB_NAME"] = dbname
    os.environ["LOCAL_DB_USER"] = user
    os.environ["LOCAL_DB_PASSWORD"] = password
    os.environ["DB_HOST"] = host
    os.environ["DB_PORT"] = str(port)
    os.environ["DB_NAME"] = dbname
    os.environ["DB_USER"] = user
    os.environ["DB_PASSWORD"] = password


if os.getenv("POLYSTARS_USE_LIVE_DB") != "1":
    _start_ephemeral_postgres()

_DSN: dict = dict(
    host=os.getenv("LOCAL_DB_HOST", os.getenv("DB_HOST", "localhost")),
    port=int(os.getenv("LOCAL_DB_PORT", os.getenv("DB_PORT", 5432))),
    dbname=os.getenv("LOCAL_DB_NAME", os.getenv("DB_NAME", "Polystars")),
    user=os.getenv("LOCAL_DB_USER", os.getenv("DB_USER", "postgres")),
    password=os.getenv("LOCAL_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
)


def make_real_connection():
    """Open and return a fresh real psycopg2 connection."""
    return psycopg2.connect(**_DSN)


class _DirectDBManager:
    """Minimal DataLoadingManager substitute.

    denormalize_card_onto_claim only calls manager.get_connection(); this class
    satisfies that contract by returning a genuine psycopg2 connection without
    going through DataLoadingManager (which has the mock baked in from unit
    test setup).
    """

    def get_connection(self):
        return make_real_connection()


import contextlib      # noqa: E402
import unittest.mock   # noqa: E402
import pytest          # noqa: E402


@contextlib.contextmanager
def _patch_season_manager_psycopg2():
    """Replace the psycopg2 reference baked into scripts.season_manager with
    the real package.  The module was cached while the unit-test mock was
    active, so its module-level ``psycopg2`` name points to a MagicMock.
    Patching the attribute gives SeasonManager a live DB connection without
    re-importing the module."""
    import scripts.season_manager as _sm
    with unittest.mock.patch.object(_sm, "psycopg2", _real_psycopg2):
        yield _sm


@pytest.fixture()
def real_season_manager():
    """SeasonManager wired to the real PostgreSQL instance."""
    with _patch_season_manager_psycopg2() as sm_mod:
        yield sm_mod.SeasonManager()


@contextlib.contextmanager
def _patch_admin_backend_psycopg2():
    """Replace psycopg2 reference baked into admin_backend.main with real package."""
    import admin_backend.main as _ab
    with unittest.mock.patch.object(_ab, "psycopg2", _real_psycopg2):
        yield _ab


@contextlib.contextmanager
def _patch_card_payload_psycopg2():
    """Replace psycopg2 reference baked into scripts.polystars_card_payload."""
    import scripts.polystars_card_payload as _cp
    with unittest.mock.patch.object(_cp, "psycopg2", _real_psycopg2):
        yield _cp


@contextlib.contextmanager
def _patch_scheduler_psycopg2():
    """Replace psycopg2 reference baked into scripts.daily_scheduler_simple."""
    import scripts.daily_scheduler_simple as _sched
    with unittest.mock.patch.object(_sched, "psycopg2", _real_psycopg2):
        yield _sched


@contextlib.contextmanager
def _patch_data_loading_manager_psycopg2():
    """Replace psycopg2 baked into scripts.data_loading_manager (DataLoadingManager)."""
    import scripts.data_loading_manager as _dlm
    with unittest.mock.patch.object(_dlm, "psycopg2", _real_psycopg2):
        yield _dlm


@contextlib.contextmanager
def integration_full_workbench_service():
    """Fully initialized SeasonWorkbenchService against real PostgreSQL.

    Patches module-level psycopg2 in all layers touched by ``SeasonWorkbenchService.__init__``
    (DataLoadingManager, SeasonManager, SimplifiedScheduler, admin_backend).
    """
    import admin_backend.main as _ab
    import scripts.data_loading_manager as _dlm
    import scripts.daily_scheduler_simple as _sched
    import scripts.season_manager as _sm
    with unittest.mock.patch.object(_dlm, "psycopg2", _real_psycopg2):
        with unittest.mock.patch.object(_sm, "psycopg2", _real_psycopg2):
            with unittest.mock.patch.object(_sched, "psycopg2", _real_psycopg2):
                with unittest.mock.patch.object(_ab, "psycopg2", _real_psycopg2):
                    yield _ab.SeasonWorkbenchService()


@pytest.fixture()
def workbench():
    """SeasonWorkbenchService wired to real PostgreSQL, bypassing DataLoadingManager init.

    Uses object.__new__ to skip __init__ and injects _DirectDBManager directly so
    psycopg2.connect() is never called through DataLoadingManager (which has the
    mock baked in). The module-level psycopg2 reference is patched so that
    RealDictCursor and other extras are the real implementations.
    """
    with _patch_admin_backend_psycopg2() as ab:
        svc = object.__new__(ab.SeasonWorkbenchService)
        svc.manager = _DirectDBManager()
        svc._wallets_cache = {}
        svc.ensure_claims_schema_for_mint()
        svc.ensure_user_web_controls_schema()
        yield svc


@pytest.fixture()
def real_scheduler():
    """SimplifiedScheduler with real psycopg2, for snapshot integration tests."""
    with _patch_scheduler_psycopg2() as sched_mod:
        scheduler = object.__new__(sched_mod.SimplifiedScheduler)
        yield scheduler


@pytest.fixture()
def admin_api_client():
    """FastAPI TestClient for ``admin_backend.main:app`` with real DB service."""
    from fastapi.testclient import TestClient

    import admin_backend.main as ab

    with integration_full_workbench_service() as svc:
        prev = ab.service
        ab.service = svc
        try:
            yield TestClient(ab.app)
        finally:
            ab.service = prev
