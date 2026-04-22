"""
Unit tests for pure static methods on SeasonWorkbenchService
(admin_backend/main.py).

These are imported directly from the class without instantiating it,
so no DB connection is made.
"""

from datetime import datetime, timezone
import pytest


def _svc():
    """Return the SeasonWorkbenchService class without instantiating it."""
    import sys
    # admin_backend.main instantiates service at module level — import only the class
    # by reaching into the module after it has been imported via conftest mocks.
    import importlib
    import unittest.mock as mock

    # Patch the three __init__ helpers that reach out to DB so import succeeds.
    with (
        mock.patch("scripts.data_loading_manager.DataLoadingManager._ensure_tables"),
        mock.patch("scripts.season_manager.SeasonManager.__init__", return_value=None),
        mock.patch("scripts.daily_scheduler_simple.SimplifiedScheduler.__init__", return_value=None),
    ):
        import admin_backend.main as m
    return m.SeasonWorkbenchService


# ---------------------------------------------------------------------------
# fmt_remaining
# ---------------------------------------------------------------------------

class TestFmtRemaining:
    @property
    def fmt(self):
        return _svc().fmt_remaining

    def test_zero_or_negative(self):
        assert self.fmt(0) == "0s"
        assert self.fmt(-10) == "0s"

    def test_seconds_only(self):
        assert self.fmt(45) == "45s"

    def test_one_minute(self):
        assert self.fmt(60) == "1m 0s"

    def test_minutes_and_seconds(self):
        assert self.fmt(90) == "1m 30s"

    def test_one_hour(self):
        assert self.fmt(3600) == "1h 0m 0s"

    def test_hours_minutes_seconds(self):
        assert self.fmt(3661) == "1h 1m 1s"

    def test_one_day(self):
        assert self.fmt(86400) == "1d 0m 0s"

    def test_full_combination(self):
        # 1d + 1h + 1m + 1s = 86400 + 3600 + 60 + 1 = 90061
        assert self.fmt(90061) == "1d 1h 1m 1s"

    def test_fractional_seconds_truncated(self):
        # float input — should truncate, not round
        assert self.fmt(90.9) == "1m 30s"


# ---------------------------------------------------------------------------
# parse_iso_datetime_utc
# ---------------------------------------------------------------------------

class TestParseIsoDatetimeUtc:
    @property
    def parse(self):
        return _svc().parse_iso_datetime_utc

    def test_z_suffix_treated_as_utc(self):
        dt = self.parse("2025-06-15T12:00:00Z")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 12

    def test_explicit_utc_offset(self):
        dt = self.parse("2025-06-15T12:00:00+00:00")
        assert dt.tzinfo == timezone.utc

    def test_positive_offset_converted_to_utc(self):
        dt = self.parse("2025-06-15T14:00:00+02:00")
        assert dt.hour == 12  # 14:00+02 → 12:00 UTC

    def test_naive_datetime_assumed_utc(self):
        dt = self.parse("2025-06-15T12:00:00")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 12

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            self.parse("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            self.parse("   ")

    def test_result_is_always_utc(self):
        for s in (
            "2025-01-01T00:00:00Z",
            "2025-01-01T00:00:00+00:00",
            "2025-01-01T05:00:00+05:00",
        ):
            dt = self.parse(s)
            assert dt.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# fmt_dt
# ---------------------------------------------------------------------------

class TestFmtDt:
    @property
    def fmt(self):
        return _svc().fmt_dt

    def test_none_returns_na(self):
        assert self.fmt(None) == "n/a"

    def test_utc_datetime_formatted(self):
        dt = datetime(2025, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
        assert self.fmt(dt) == "2025-06-15 12:30:00 UTC"

    def test_naive_datetime_treated_as_utc(self):
        dt = datetime(2025, 6, 15, 12, 30, 0)
        result = self.fmt(dt)
        assert "2025-06-15 12:30:00 UTC" == result


# ---------------------------------------------------------------------------
# _resolve_stream_for_season_id
# ---------------------------------------------------------------------------

class TestResolveStreamForSeasonId:
    @property
    def resolve(self):
        return _svc()._resolve_stream_for_season_id

    def test_finds_genesis_stream(self):
        eligibility = {"genesis": {"season_id": 5, "type": "genesis"}}
        result = self.resolve(eligibility, 5)
        assert result == {"season_id": 5, "type": "genesis"}

    def test_finds_standard_stream(self):
        eligibility = {
            "genesis": {"season_id": 1},
            "standard": {"season_id": 7},
        }
        result = self.resolve(eligibility, 7)
        assert result == {"season_id": 7}

    def test_returns_none_when_not_found(self):
        eligibility = {"genesis": {"season_id": 1}, "standard": {"season_id": 2}}
        assert self.resolve(eligibility, 99) is None

    def test_returns_none_for_empty_eligibility(self):
        assert self.resolve({}, 1) is None

    def test_genesis_takes_precedence_when_both_match(self):
        # Both streams have same season_id — genesis found first
        eligibility = {
            "genesis": {"season_id": 3},
            "standard": {"season_id": 3},
        }
        result = self.resolve(eligibility, 3)
        assert result == {"season_id": 3}

    def test_non_dict_stream_ignored(self):
        eligibility = {"genesis": None, "standard": {"season_id": 2}}
        result = self.resolve(eligibility, 2)
        assert result == {"season_id": 2}


# ---------------------------------------------------------------------------
# Showcase/mint policy pinning
#
# On successful mint the admin API must promote the preview row into a minted
# ``claims`` row via ``promote_preview_to_claim``. That helper denormalizes
# card-detail fields onto ``claims`` (the canonical store for minted STARs)
# and DELETEs the matching preview row from ``preview_cards`` so the minted
# STAR disappears from the home showcase ticker. The old
# ``persist_user_generated_card_for_mint`` (which wrote the minted card into
# the preview buffer) must be fully replaced — admin_backend should no
# longer import it, so a future refactor can't silently revert to the
# preview-only-table-as-canonical-store shape we're explicitly moving away
# from.
# ---------------------------------------------------------------------------

class TestAdminMintBackendPromotesPreview:
    def _admin_module(self):
        import unittest.mock as mock

        with (
            mock.patch("scripts.data_loading_manager.DataLoadingManager._ensure_tables"),
            mock.patch("scripts.season_manager.SeasonManager.__init__", return_value=None),
            mock.patch(
                "scripts.daily_scheduler_simple.SimplifiedScheduler.__init__",
                return_value=None,
            ),
        ):
            import admin_backend.main as m
        return m

    def test_admin_imports_promote_helper(self):
        m = self._admin_module()
        assert hasattr(m, "promote_preview_to_claim"), (
            "admin_backend must import promote_preview_to_claim so the minted "
            "STAR is written into the canonical ``claims`` table (not only "
            "into the ``preview_cards`` preview buffer)"
        )

    def test_promote_helper_is_callable(self):
        m = self._admin_module()
        assert callable(m.promote_preview_to_claim)

    def test_admin_no_longer_imports_legacy_persist_helper(self):
        m = self._admin_module()
        assert not hasattr(m, "persist_user_generated_card_for_mint"), (
            "admin_backend must not import persist_user_generated_card_for_mint "
            "anymore — its role was replaced by promote_preview_to_claim, and "
            "keeping the legacy symbol around would make it easy to silently "
            "regress the mint flow back to preview-only-table-as-canonical-store"
        )


# ---------------------------------------------------------------------------
# Home-ticker SQL pinning
#
# The home-page showcase (``/api/cards/ticker``) samples ``preview_cards``.
# After Stage 2 this table is a strict preview-only buffer — minted rows are
# deleted by ``promote_preview_to_claim`` — so the ticker SQL must NOT reach
# into ``winner_wallets_nft_to_claim.is_minted`` to filter them out. That old
# JOIN was transition-scaffolding and its lingering presence would indicate
# that the promotion cleanup (DELETE-on-mint) had silently regressed back to
# the dual-write shape.
# ---------------------------------------------------------------------------

def _user_web_module():
    import unittest.mock as mock

    with (
        mock.patch("scripts.data_loading_manager.DataLoadingManager._ensure_tables"),
    ):
        import user_web_backend.main as m
    return m


class TestCardsTickerSqlPreviewOnly:
    @property
    def sql(self) -> str:
        return _user_web_module()._CARDS_TICKER_SAMPLE_SQL

    @property
    def normalized(self) -> str:
        return " ".join(self.sql.split()).lower()

    def test_samples_from_preview_cards(self):
        assert "from preview_cards" in self.normalized

    def test_does_not_join_winner_wallets_for_is_minted_signal(self):
        """preview_cards is preview-only — minted rows are deleted."""
        assert "join winner_wallets_nft_to_claim" not in self.normalized
        assert "is_minted" not in self.normalized

    def test_uses_random_order_and_limit(self):
        assert "order by random()" in self.normalized
        assert "limit %s" in self.normalized

    def test_selects_required_columns(self):
        for column in (
            "gc.slug",
            "gc.card_title",
            "gc.front_image_path",
            "gc.back_image_path",
            "gc.created_at",
        ):
            assert column in self.normalized, f"missing column {column!r}"

    def test_does_not_touch_claims_table(self):
        """Ticker feeds from the preview buffer only; minted STARs live in
        ``claims`` and are served by ``/api/cards/{slug}``."""
        assert "from claims" not in self.normalized
        assert "join claims" not in self.normalized


# ---------------------------------------------------------------------------
# Card-detail endpoint split (preview vs minted)
#
# ``/api/preview/{slug}`` must read the live preview buffer and MUST NOT reach
# into ``claims`` — preview rows are authoritative there and joining claims
# would only confuse the shape. Conversely, ``/api/cards/{slug}`` must read
# denormalized card fields straight off ``claims`` and MUST NOT read
# ``preview_cards`` — otherwise a minted STAR would still depend on
# the preview buffer it was supposed to be promoted out of.
# ---------------------------------------------------------------------------


class TestCardDetailEndpointSplit:
    @property
    def preview_sql(self) -> str:
        return " ".join(_user_web_module()._PREVIEW_CARD_DETAIL_SQL.split()).lower()

    @property
    def minted_sql(self) -> str:
        return " ".join(_user_web_module()._MINTED_CARD_DETAIL_SQL.split()).lower()

    def test_preview_sql_reads_preview_buffer_only(self):
        assert "from preview_cards" in self.preview_sql
        assert "from claims" not in self.preview_sql
        assert "join claims" not in self.preview_sql

    def test_minted_sql_reads_claims_only(self):
        assert "from claims" in self.minted_sql
        assert "from preview_cards" not in self.minted_sql
        assert "join preview_cards" not in self.minted_sql

    def test_minted_sql_exposes_card_slug_alias(self):
        """The claims row stores the card slug as ``card_slug`` — the endpoint
        must alias it to ``slug`` so the shared formatter and the frontend
        can treat preview and minted responses uniformly."""
        assert "c.card_slug as slug" in self.minted_sql

    def test_minted_sql_joins_user_wallet_signins_for_owner_proxy(self):
        """``claims.user_wallet`` is an EOA; the claimer's PM proxy lives on
        ``user_wallet_signins`` and must be joined in so the detail response
        can populate ``owner_proxy_wallet``."""
        assert "user_wallet_signins" in self.minted_sql
        assert "uws.proxy_wallet as owner_proxy_wallet" in self.minted_sql

    def test_minted_sql_surfaces_asset_address_from_claims(self):
        assert "c.asset_address as minted_asset_address" in self.minted_sql

    def test_preview_sql_does_not_expose_collection_mint_number(self):
        """Previews are not part of the minted collection, so the
        ``collection_mint_number`` must not leak through the public API.
        Minted ``/api/cards/{slug}`` still surfaces it (from ``claims``)."""
        assert "collection_mint_number" not in self.preview_sql
        assert "c.collection_mint_number" in self.minted_sql

    def test_endpoints_are_registered(self):
        m = _user_web_module()
        routes = {getattr(r, "path", None) for r in m.app.routes}
        assert "/api/preview/{slug}" in routes
        assert "/api/cards/{slug}" in routes
