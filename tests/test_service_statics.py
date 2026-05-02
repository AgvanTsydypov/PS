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
# Home-ticker SQL pinning
#
# The home-page showcase (``/api/cards/ticker``) now samples minted STARs
# directly from ``claims`` (queue model). It must select COMPLETED rows
# only, must alias the claims-side column names (``card_slug``,
# ``front_image_url``, ``back_image_url``) to the ticker's historical
# names (``slug``, ``front_image_path``, ``back_image_path``) so the
# downstream cache and frontend code stay stable, and must never reach
# into any legacy winner-wallets table.
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

    def test_samples_from_claims_completed(self):
        assert "from claims" in self.normalized
        assert "status = 'completed'" in self.normalized

    def test_does_not_join_legacy_winner_wallets(self):
        assert "winner_wallets_nft_to_claim" not in self.normalized
        assert "is_minted" not in self.normalized

    def test_uses_random_order_and_limit(self):
        assert "order by random()" in self.normalized
        assert "limit %s" in self.normalized

    def test_aliases_claim_columns_to_ticker_shape(self):
        """Frontend depends on these legacy column names — keep the alias."""
        for alias in (
            "as slug",
            "as front_image_path",
            "as back_image_path",
            "as created_at",
        ):
            assert alias in self.normalized, f"missing alias {alias!r}"

    def test_filters_out_unrenderable_claims(self):
        """QUEUED/PENDING/PROCESSING rows have no card_slug yet; FAILED rows
        have no on-chain artefact. Both must be excluded."""
        assert "card_slug is not null" in self.normalized
        assert "front_image_url is not null" in self.normalized
        assert "back_image_url" in self.normalized


# ---------------------------------------------------------------------------
# Unified card-detail endpoint (/api/cards/{slug})
#
# A single URL serves both minted STARs and preview cards. The endpoint
# tries the ``claims`` SQL first; on miss it falls back to the
# ``preview_cards`` SQL and stamps ``is_preview=true`` on the response.
# The two SQL strings keep the same alias shape so the shared formatter
# can render either source. Splitting the legacy ``/api/preview/{slug}``
# endpoint off was a regression — slugs naturally migrate from preview to
# minted inside the cron worker (``denormalize_card_onto_claim`` writes
# the slug to claims and deletes the preview row in one transaction), so
# the URL must be stable across that transition.
# ---------------------------------------------------------------------------


class TestCardDetailEndpoint:
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
        ``collection_mint_number`` must not leak through the API even
        through the unified endpoint. Minted rows still surface it (from
        ``claims``); the page hides the chip when ``is_preview`` is true."""
        assert "collection_mint_number" not in self.preview_sql
        assert "c.collection_mint_number" in self.minted_sql

    def test_unified_cards_endpoint_is_registered(self):
        m = _user_web_module()
        routes = {getattr(r, "path", None) for r in m.app.routes}
        assert "/api/cards/{slug}" in routes

    def test_legacy_preview_endpoint_is_removed(self):
        """The old ``/api/preview/{slug}`` route was unified into
        ``/api/cards/{slug}``. Asserting it's gone makes sure a future
        refactor doesn't accidentally re-add a parallel path that drifts
        away from the unified one."""
        m = _user_web_module()
        routes = {getattr(r, "path", None) for r in m.app.routes}
        assert "/api/preview/{slug}" not in routes

    def test_unified_endpoint_attempts_minted_then_preview(self):
        """The endpoint body must reference both SQL strings so a slug
        served by either store is reachable. Source-text check guards
        against a refactor that drops the preview fallback and silently
        404s the ticker links."""
        import inspect
        from user_web_backend.main import card_by_slug
        src = inspect.getsource(card_by_slug)
        assert "_MINTED_CARD_DETAIL_SQL" in src
        assert "_PREVIEW_CARD_DETAIL_SQL" in src
        # ``is_preview`` flag must be stamped onto both branches so the
        # frontend can render the right chips.
        assert 'is_preview' in src
        # Order matters: minted first, preview as fallback. A reversed
        # order would surface stale preview data even after a successful
        # mint (between the moment the claims row is written and the
        # moment the preview row is deleted, both could match the slug).
        minted_pos = src.index("_MINTED_CARD_DETAIL_SQL")
        preview_pos = src.index("_PREVIEW_CARD_DETAIL_SQL")
        assert minted_pos < preview_pos


# ---------------------------------------------------------------------------
# /api/me/cards — owned-on-chain PolyStars NFTs
#
# Replaces a previously deleted Solana/Metaplex DAS implementation. The
# endpoint MUST query claims (the new queue model writes the on-chain
# artefacts there) and MUST filter to COMPLETED rows with an asset_address.
# Pre-merge guards on the SQL shape make sure a future refactor doesn't
# silently break the dashboard panel by selecting QUEUED/PROCESSING rows
# (no asset_address yet) or losing the season-name JOIN that surfaces
# "Genesis #1" / "Standard #2" labels.
# ---------------------------------------------------------------------------


class TestMeCardsSql:
    @property
    def sql(self) -> str:
        return " ".join(_user_web_module()._ME_CARDS_CLAIMS_SQL.split()).lower()

    def test_reads_claims_only(self):
        assert "from claims" in self.sql
        assert "from preview_cards" not in self.sql

    def test_filters_to_completed_with_asset_address(self):
        assert "c.status = 'completed'" in self.sql
        assert "c.asset_address is not null" in self.sql
        assert "c.asset_address <> ''" in self.sql

    def test_matches_recipient_with_legacy_user_wallet_fallback(self):
        """Queue-model rows store the NFT recipient on ``recipient_address``;
        legacy rows from before the column existed instead carry the recipient
        on ``user_wallet``. The endpoint must accept both so old claims still
        appear on the dashboard after the migration."""
        assert "lower(c.recipient_address) = %s" in self.sql
        assert "c.recipient_address is null" in self.sql
        assert "lower(c.user_wallet) = %s" in self.sql

    def test_joins_seasons_for_label(self):
        """``season_type`` and ``season_number`` come from ``seasons``, not
        the claim row itself; without this join the dashboard cannot render
        "Genesis #1" / "Standard #2" labels."""
        assert "left join seasons s on s.id = c.season_id" in self.sql
        assert "s.type" in self.sql
        assert "s.season_number" in self.sql

    def test_aliases_match_frontend_response_shape(self):
        """Frontend ``MyMintedNftItem`` (UserDashboard.tsx ~L121) expects
        these exact keys on each item — keep the SQL aliases in sync."""
        for alias in (
            "as claim_id",
            "as asset_address",
            "as tx_hash",
            "as metadata_uri",
            "as season_id",
            "as season_type",
            "as season_number",
            "as phase",
            "as collection_mint_number",
            "as name",
            "as front_image_url",
            "as back_image_url",
            "as card_slug",
            "as minted_at",
        ):
            assert alias in self.sql, f"missing alias {alias!r}"

    def test_endpoint_is_registered(self):
        m = _user_web_module()
        routes = {getattr(r, "path", None) for r in m.app.routes}
        assert "/api/me/cards" in routes


class TestEvmServiceParseAssetAddress:
    """``parse_asset_address`` is the bridge between our DB's ``asset_address``
    string ``"<contract>/<tokenId>"`` and the EVM reader's integer-only
    ``ownerOf(tokenId)`` call. A regression here would silently drop minted
    NFTs from the dashboard."""

    @property
    def parse(self):
        from scripts.evm_service import parse_asset_address
        return parse_asset_address

    def test_well_formed_pair(self):
        contract, token_id = self.parse("0xABC1234567890123456789012345678901234567/42")
        assert contract == "0xABC1234567890123456789012345678901234567"
        assert token_id == 42

    def test_handles_surrounding_whitespace(self):
        contract, token_id = self.parse("  0xfeedface00000000000000000000000000000000/0  ")
        assert contract == "0xfeedface00000000000000000000000000000000"
        assert token_id == 0

    def test_empty_returns_none_pair(self):
        assert self.parse("") == (None, None)
        assert self.parse(None) == (None, None)  # type: ignore[arg-type]

    def test_no_slash_returns_none_pair(self):
        assert self.parse("0xABC1234567890123456789012345678901234567") == (None, None)

    def test_non_integer_token_id_returns_contract_with_none_tid(self):
        contract, token_id = self.parse("0xABC1234567890123456789012345678901234567/notanumber")
        assert contract == "0xABC1234567890123456789012345678901234567"
        assert token_id is None


class TestEvmServiceExplorerUrls:
    """Explorer URL builders are the SOLE source of links the user can use to
    inspect their NFT on-chain — wrong base for the configured chain ID
    would land them on Etherscan mainnet for a Sepolia token (404). Lock
    the chain → base mapping with a static test."""

    @property
    def builders(self):
        from scripts.evm_service import etherscan_base_url, etherscan_nft_url, etherscan_tx_url
        return etherscan_base_url, etherscan_nft_url, etherscan_tx_url

    def test_sepolia_uses_sepolia_etherscan(self):
        base, nft, tx = self.builders
        assert base(11155111) == "https://sepolia.etherscan.io"
        assert tx("0xdead", 11155111) == "https://sepolia.etherscan.io/tx/0xdead"
        assert (
            nft("0xCAFEBABECAFEBABECAFEBABECAFEBABECAFEBABE", 7, 11155111)
            == "https://sepolia.etherscan.io/nft/0xCAFEBABECAFEBABECAFEBABECAFEBABECAFEBABE/7"
        )

    def test_mainnet_uses_root_etherscan(self):
        base, _nft, tx = self.builders
        assert base(1) == "https://etherscan.io"
        assert "https://etherscan.io/tx/0xabc" == tx("0xabc", 1)

    def test_unknown_chain_falls_back_to_mainnet_etherscan(self):
        base, _nft, _tx = self.builders
        assert base(999999) == "https://etherscan.io"


# ---------------------------------------------------------------------------
# collection_mint_number — allocated post-pickup,
# MAX over PROCESSING ∪ COMPLETED (with non-null cmn).
#
# Original behaviour: a BEFORE-INSERT trigger assigned the next number on
# every claims INSERT (including QUEUED rows that may never mint). Result:
# the user-facing "mint #N" inflated past the actual on-chain mint count,
# so a user could see "you got mint #42" while only 30 NFTs had ever
# landed.
#
# Intermediate behaviour: the cron worker allocated the number under
# pg_advisory_xact_lock(season_id) right after pickup using
# MAX(collection_mint_number) + 1 over COMPLETED rows only. This fixed
# the inflation bug but introduced a blind-spot race: a claim sitting in
# PROCESSING (rendered, sometimes already on-chain, but not yet flipped
# to COMPLETED) was invisible to the next allocator, so two claims could
# each receive the same cmn baked into their card PNG. Recovery later
# patched the DB row by renumbering, but the IPFS / on-chain image is
# immutable — the visual divergence could not be undone.
#
# Current behaviour: the worker allocates over PROCESSING ∪ COMPLETED
# (filtered to rows with collection_mint_number IS NOT NULL — pre-mint
# FAILED rows are released by NULLing their cmn). This eliminates the
# blind spot at the cost of leaving gaps when a stuck PROCESSING row is
# requeued back to QUEUED and its cmn freed. Gaps in numbering are
# strictly cheaper than two NFTs sharing the same on-chain number on
# their card image.
#
# These tests guard the static SQL strings — a future refactor that
# accidentally restored the trigger, narrowed the predicate back to
# COMPLETED-only, or widened it to all rows would re-introduce a known
# bug.
# ---------------------------------------------------------------------------


class TestCollectionMintNumberAllocationPolicy:
    @property
    def schema_migration_source(self) -> str:
        """Read the schema-migration method body as plain source text. The
        method runs ``cursor.execute(...)`` with several SQL strings; checking
        the literal source is sufficient for the policy guards we want to lock
        in."""
        import inspect
        from admin_backend.claims_mint import ClaimsMintMixin
        return inspect.getsource(ClaimsMintMixin.ensure_claims_schema_for_mint)

    @property
    def worker_source(self) -> str:
        """Read the cron worker method as plain source text."""
        import inspect
        from scripts.daily_scheduler_simple import SimplifiedScheduler
        return inspect.getsource(SimplifiedScheduler.process_mint_queue)

    @property
    def recovery_source(self) -> str:
        import inspect
        from scripts.daily_scheduler_simple import SimplifiedScheduler
        return inspect.getsource(SimplifiedScheduler._recover_stale_processing)

    def test_legacy_assign_trigger_is_dropped(self):
        """The BEFORE-INSERT trigger MUST be dropped — leaving it in place
        would double-allocate (trigger at INSERT + worker at pickup) and
        violate the partial unique index."""
        sql = self.schema_migration_source
        assert "DROP TRIGGER IF EXISTS tr_claims_assign_season_mint" in sql
        assert "DROP FUNCTION IF EXISTS claims_assign_season_mint_number" in sql
        # And the legacy CREATE TRIGGER / CREATE FUNCTION must NOT come back.
        assert "CREATE TRIGGER tr_claims_assign_season_mint" not in sql
        assert "claims_assign_season_mint_number()" not in sql or "DROP" in sql

    def test_unique_index_is_partial_on_completed(self):
        """A non-partial unique on (season_id, collection_mint_number) would
        block reuse of a number after a pre-mint FAILED row (until manually
        cleared). The partial predicate makes the unique apply only to rows
        the user actually sees — COMPLETED ones."""
        sql = self.schema_migration_source
        assert "CREATE UNIQUE INDEX" in sql
        assert "ux_claims_season_collection_mint" in sql
        assert "WHERE status = 'COMPLETED'" in sql

    def test_worker_allocates_under_advisory_lock_per_season(self):
        """Without per-season locking, two workers (or two iterations
        bypassing the global advisory lock) could allocate the same MAX+1
        and collide on commit when both rows reach COMPLETED."""
        src = self.worker_source
        assert "pg_advisory_xact_lock(9283742, %s)" in src

    def test_worker_allocation_reads_max_over_processing_and_completed(self):
        """The current policy: the next number is
        ``MAX(collection_mint_number) + 1`` over PROCESSING ∪ COMPLETED
        claims (with non-null cmn). Including PROCESSING closes the
        blind-spot race that let two claims bake the same number onto
        their card PNG. The non-null filter excludes pre-mint FAILED
        rows, whose cmn is NULL by the failure path. Widening to all
        rows would re-introduce the original inflation bug; narrowing
        back to COMPLETED only would re-introduce the blind-spot race."""
        src = self.worker_source
        assert "FROM   claims" in src or "FROM claims" in src
        assert (
            "status    IN ('PROCESSING', 'COMPLETED')" in src
            or "status IN ('PROCESSING', 'COMPLETED')" in src
        )
        assert "collection_mint_number IS NOT NULL" in src
        assert "COALESCE(MAX(collection_mint_number), 0) + 1" in src

    def test_worker_allocation_is_idempotent(self):
        """The allocation UPDATE must be guarded by
        ``WHERE collection_mint_number IS NULL`` so a stuck-PROCESSING row
        re-picked up after recovery doesn't get a fresh number that
        contradicts what was already rendered onto its card image."""
        src = self.worker_source
        assert "AND  collection_mint_number IS NULL" in src

    def test_pre_mint_failure_clears_collection_mint_number(self):
        """Pre-mint failure must release the number back into the pool
        (set to NULL) so the next claim reuses it. The post-mint failure
        path (``on_chain_completed=True``) MUST NOT clear it because the
        EVM tx already committed the metadata that references this number."""
        src = self.worker_source
        assert "collection_mint_number = NULL" in src

    def test_recovery_pass_clears_number_only_on_requeue_branch(self):
        """A row reset to QUEUED must lose its tentative number (the next
        worker pickup will allocate a fresh one). A row auto-completed
        because tx_hash is present MUST keep its number — the on-chain NFT
        already references it."""
        src = self.recovery_source
        assert "status                 = 'QUEUED'" in src
        # The QUEUED branch nulls the number.
        assert "collection_mint_number = NULL" in src
        # The COMPLETED branch in the same method MUST NOT touch the column.
        # Find the second UPDATE (auto-completed) by splitting on the first
        # branch's text — sanity check that the COMPLETED branch doesn't set
        # collection_mint_number = NULL.
        completed_branch = src.split("auto-completed", 1)[-1]
        assert "collection_mint_number = NULL" not in completed_branch
