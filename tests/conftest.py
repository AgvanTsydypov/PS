"""
Shared pytest fixtures and global mocks.

psycopg2 is mocked before any project module is imported so that tests
that instantiate DataLoadingManager / SeasonManager / SeasonWorkbenchService
do not require a live database connection.
"""

import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub psycopg2 globally so DB-touching __init__ methods do not fail.
# ---------------------------------------------------------------------------

_mock_cursor = MagicMock()
_mock_cursor.fetchall.return_value = []
_mock_cursor.fetchone.return_value = None
_mock_cursor.__enter__ = lambda s: s
_mock_cursor.__exit__ = MagicMock(return_value=False)

_mock_conn = MagicMock()
_mock_conn.cursor.return_value = _mock_cursor
_mock_conn.__enter__ = lambda s: s
_mock_conn.__exit__ = MagicMock(return_value=False)

_mock_psycopg2 = MagicMock()
_mock_psycopg2.connect.return_value = _mock_conn
_mock_psycopg2.extras = MagicMock()
_mock_psycopg2.extras.RealDictCursor = MagicMock()
_mock_psycopg2.errors = MagicMock()

# ``psycopg2.pool.ThreadedConnectionPool`` is used by user_web_backend's
# connection pool. Tests don't exercise the pool directly, but the lazy
# init path imports ``psycopg2.pool`` — we stub it so the import resolves
# and any incidental ``pool.getconn()`` returns the same mock connection
# that ``psycopg2.connect()`` does.
_mock_pool_instance = MagicMock()
_mock_pool_instance.getconn.return_value = _mock_conn
_mock_psycopg2.pool = MagicMock()
_mock_psycopg2.pool.ThreadedConnectionPool = MagicMock(return_value=_mock_pool_instance)

sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.extras", _mock_psycopg2.extras)
sys.modules.setdefault("psycopg2.errors", _mock_psycopg2.errors)
sys.modules.setdefault("psycopg2.pool", _mock_psycopg2.pool)

# ---------------------------------------------------------------------------
# Stub scripts.cardgen.assets so polystars_card_payload can be imported
# without Playwright or a browser being installed.
# ---------------------------------------------------------------------------

_mock_cardgen_assets = MagicMock()
sys.modules.setdefault("scripts.cardgen.assets", _mock_cardgen_assets)
