"""
Database Uploader for Polymarket Events (PostgreSQL only)

ПРЯМОЙ ЗАПУСК:
==============
    python db_uploader.py [filepath] [--redemptions]

Примеры:
    python db_uploader.py                           # Последний JSON → PostgreSQL
    python db_uploader.py data.json                 # data.json → PostgreSQL
    python db_uploader.py redeem.json --redemptions # Загрузить redemptions

ПРОГРАММНОЕ ИСПОЛЬЗОВАНИЕ:
===========================
    from db.db_uploader import DbUploader as SupabaseUploader
    uploader = SupabaseUploader()
    uploader.upload_redemptions_batch(redemptions_list)

ТРЕБОВАНИЯ:
===========
- Python 3.8+
- pip install psycopg2-binary python-dotenv
- Файл .env с настройками PostgreSQL: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
"""

from __future__ import annotations

import io
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

try:
    # Works when project root is on PYTHONPATH (e.g. scripts run as package).
    from scripts.ai import Agent2ColoristGenerator
except ModuleNotFoundError as exc:
    # Fallback for direct script execution where only /app/scripts is on sys.path.
    if getattr(exc, "name", None) != "scripts":
        raise
    from ai import Agent2ColoristGenerator

load_dotenv()


class DbUploader:
    """Handles uploading Polymarket data to PostgreSQL"""

    TABLE_EVENTS = "events"
    TABLE_SERIES = "series"
    TABLE_TAGS = "tags"
    TABLE_EVENT_TAGS = "event_tags"
    TABLE_MARKETS = "markets"
    TABLE_REDEMPTIONS = "redemptions"

    def __init__(self, use_local_db: bool = True):
        # use_local_db kept for backwards compatibility — always PostgreSQL
        ssl_mode = os.getenv("DB_SSLMODE", "require")
        self.connection_params = {
            "host": os.getenv("LOCAL_DB_HOST", os.getenv("DB_HOST")),
            "port": os.getenv("LOCAL_DB_PORT", os.getenv("DB_PORT", "5432")),
            "database": os.getenv("LOCAL_DB_NAME", os.getenv("DB_NAME")),
            "user": os.getenv("LOCAL_DB_USER", os.getenv("DB_USER")),
            "password": os.getenv("LOCAL_DB_PASSWORD", os.getenv("DB_PASSWORD")),
            "sslmode": ssl_mode,
        }
        self.use_local_db = True  # always PostgreSQL
        self.stats = {
            "events_inserted": 0,
            "series_upserted": 0,
            "tags_upserted": 0,
            "tag_colors_generated": 0,
            "event_tags_upserted": 0,
            "markets_inserted": 0,
            "redemptions_inserted": 0,
            "errors": [],
        }
        self.tag_colors_model = os.getenv("POLYSTARS_TAG_COLORS_MODEL", "").strip()
        self.tag_colors_prompt_version = (
            os.getenv("POLYSTARS_TAG_COLORS_PROMPT_VERSION", "v1").strip() or "v1"
        )
        self._tag_color_generator: Optional[Agent2ColoristGenerator] = None
        self._test_connection()

    def _get_tag_color_generator(self) -> Agent2ColoristGenerator:
        if self._tag_color_generator is None:
            self._tag_color_generator = Agent2ColoristGenerator(
                model=self.tag_colors_model or None,
                prompt_version=self.tag_colors_prompt_version,
            )
            self.tag_colors_model = self._tag_color_generator.model
        return self._tag_color_generator

    @staticmethod
    def _ensure_tags_color_schema(cursor) -> None:
        cursor.execute("ALTER TABLE tags ADD COLUMN IF NOT EXISTS hex_color TEXT")
        cursor.execute("ALTER TABLE tags ADD COLUMN IF NOT EXISTS is_primary BOOLEAN NOT NULL DEFAULT FALSE")
        cursor.execute(
            """
            ALTER TABLE tags
            DROP CONSTRAINT IF EXISTS tags_hex_color_format
            """
        )
        cursor.execute(
            """
            ALTER TABLE tags
            ADD CONSTRAINT tags_hex_color_format
            CHECK (hex_color IS NULL OR hex_color ~* '^#[0-9a-f]{6}$')
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_hex_color ON tags(hex_color)")

    def _assign_missing_tag_colors(self, cursor, candidate_tag_ids: List[str]) -> int:
        if not candidate_tag_ids:
            return 0

        cursor.execute(
            """
            SELECT id, COALESCE(NULLIF(BTRIM(label), ''), id) AS effective_label
            FROM tags
            WHERE id = ANY(%s)
              AND is_primary = TRUE
              AND hex_color IS NULL
            ORDER BY id ASC
            """,
            (candidate_tag_ids,),
        )
        missing_rows = cursor.fetchall()
        if not missing_rows:
            return 0

        cursor.execute(
            """
            SELECT DISTINCT COALESCE(NULLIF(BTRIM(label), ''), id) AS tag_label, hex_color
            FROM tags
            WHERE is_primary = TRUE
              AND hex_color IS NOT NULL
            ORDER BY tag_label ASC, hex_color ASC
            """
        )
        palette = [
            {"tag_label": str(row[0]), "hex_color": str(row[1])}
            for row in cursor.fetchall()
            if row and row[1]
        ]
        generator = self._get_tag_color_generator()

        generated = 0
        for tag_id, effective_label in missing_rows:
            try:
                out = generator.generate(
                    {
                        "new_primary_tag": str(effective_label or tag_id),
                        "existing_palette": palette,
                    }
                )
            except Exception as exc:
                self.stats["errors"].append(f"Tag color generation failed for tag_id={tag_id}: {exc}")
                continue

            cursor.execute(
                """
                UPDATE tags
                SET hex_color = %s
                WHERE id = %s
                  AND hex_color IS NULL
                """,
                (out.hex_color, tag_id),
            )
            if cursor.rowcount:
                generated += 1
                palette.append({"tag_label": str(effective_label or tag_id), "hex_color": out.hex_color})
        return generated

    def _test_connection(self):
        try:
            conn = psycopg2.connect(**self.connection_params)
            conn.close()
        except Exception as e:
            raise ConnectionError(
                f"Failed to connect to PostgreSQL: {e}\n"
                f"Host: {self.connection_params['host']}:{self.connection_params['port']}\n"
                f"Database: {self.connection_params['database']}"
            )

    # ── Data preparation ──────────────────────────────────────────────────────

    def load_json_data(self, filepath: str) -> Dict:
        print(f"[*] Loading data from {filepath}...")
        with open(filepath, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        if "events" in raw_data:
            data = raw_data
        elif isinstance(raw_data, list):
            data = {"events": raw_data, "metadata": {}}
        elif isinstance(raw_data, dict) and "id" in raw_data:
            data = {"events": [raw_data], "metadata": {}}
        else:
            data = raw_data

        print(f"[OK] Loaded {len(data.get('events', []))} events")
        return data

    def _extract_series_object(self, event: Dict) -> Optional[Dict]:
        """
        Normalize event['series'] payload to a single dict.
        Polymarket can return either:
        - {"series": {...}}
        - {"series": [{...}]}
        """
        series_raw = event.get("series")
        if isinstance(series_raw, dict):
            return series_raw
        if isinstance(series_raw, list) and series_raw:
            first = series_raw[0]
            if isinstance(first, dict):
                return first
        return None

    def prepare_event_data(self, event: Dict) -> Dict:
        import re

        def camel_to_snake(name):
            name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
            return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()

        allowed_fields = {
            "id", "ticker", "slug", "title", "description",
            "start_date", "creation_date", "end_date", "created_at", "updated_at", "closed_time",
            "image", "icon",
            "active", "closed", "archived", "new", "featured", "restricted", "neg_risk", "enable_order_book",
            "volume", "volume24hr", "volume1wk", "volume1mo", "volume1yr",
            "liquidity", "open_interest", "liquidity_amm", "liquidity_clob",
            "competitive", "comment_count", "series_id",
        }

        event_data = {}
        for k, v in event.items():
            if k == "markets":
                continue
            snake_key = camel_to_snake(k)
            if snake_key in allowed_fields:
                event_data[snake_key] = v

        # Normalize relation to series table.
        # Prefer explicit series_id if provided; otherwise derive from nested series object.
        if not event_data.get("series_id"):
            series_obj = self._extract_series_object(event)
            if series_obj:
                series_id = series_obj.get("id")
                if series_id is not None:
                    event_data["series_id"] = str(series_id)

        for date_field in ["start_date", "creation_date", "end_date", "created_at", "updated_at", "closed_time"]:
            if date_field in event_data and event_data[date_field]:
                date_val = event_data[date_field]
                if isinstance(date_val, str):
                    event_data[date_field] = date_val.replace("Z", "+00:00")

        if "volume" in event_data:
            event_data["volume"] = float(event_data["volume"]) if event_data["volume"] else 0.0

        for vol_field in ["volume24hr", "volume1wk", "volume1mo", "volume1yr"]:
            if vol_field in event_data:
                event_data[vol_field] = float(event_data[vol_field]) if event_data[vol_field] else 0.0

        for field in ["comment_count", "competitive"]:
            if field in event_data and event_data[field] is not None:
                try:
                    event_data[field] = int(float(event_data[field]))
                except (ValueError, TypeError):
                    event_data[field] = 0

        return event_data

    def prepare_series_data(self, event: Dict) -> Optional[Dict]:
        """Extract normalized series payload from event."""
        series = self._extract_series_object(event)
        if not series:
            return None

        series_id = series.get("id")
        if not series_id:
            return None

        return {
            "id": str(series_id),
            "ticker": series.get("ticker"),
            "slug": series.get("slug"),
            "title": series.get("title"),
            "subtitle": series.get("subtitle"),
            "series_type": series.get("seriesType") if "seriesType" in series else series.get("series_type"),
            "recurrence": series.get("recurrence"),
            "description": series.get("description"),
        }

    def prepare_tag_data(self, tag: Dict) -> Optional[Dict]:
        """Normalize a tag object from Polymarket payload."""
        if not isinstance(tag, dict):
            return None

        tag_id = tag.get("id") or tag.get("slug") or tag.get("label")
        if not tag_id:
            return None

        return {
            "id": str(tag_id),
            "label": tag.get("label"),
        }

    def prepare_market_data(self, market: Dict, event_id: str) -> Dict:
        import re

        def camel_to_snake(name):
            name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
            return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()

        allowed_fields = {
            "id", "question", "condition_id", "slug", "question_id",
            "end_date", "start_date", "created_at", "updated_at", "closed_time",
            "uma_end_date", "accepting_orders_timestamp", "deploying_timestamp",
            "image", "icon", "description", "outcomes", "outcome_prices",
            "volume", "volume_num", "volume24hr", "volume1wk", "volume1mo", "volume1yr",
            "volume_clob", "volume24hr_clob", "volume1wk_clob", "volume1mo_clob", "volume1yr_clob",
            "liquidity", "liquidity_num", "liquidity_amm", "liquidity_clob",
            "active", "closed", "new", "featured", "archived", "restricted", "enable_order_book",
            "neg_risk", "ready", "funded", "cyom", "approved",
            "automatically_resolved", "automatically_active", "clear_book_on_start", "manual_activation",
            "neg_risk_other", "pending_deployment", "deploying", "rfq_enabled", "holding_rewards_enabled",
            "fees_enabled", "requires_translation", "accepting_orders", "has_reviewed_dates",
            "resolved_by", "uma_resolution_status", "uma_resolution_statuses", "uma_bond", "uma_reward",
            "market_maker_address", "submitted_by", "group_item_title", "group_item_threshold",
            "clob_token_ids", "neg_risk_request_id", "end_date_iso", "start_date_iso",
            "order_price_min_tick_size", "order_min_size", "rewards_min_size", "rewards_max_spread", "spread",
            "one_day_price_change", "one_week_price_change", "last_trade_price", "best_bid", "best_ask",
            "competitive", "custom_liveness",
        }

        market_data = {}
        for k, v in market.items():
            snake_key = camel_to_snake(k)
            if snake_key in allowed_fields:
                market_data[snake_key] = v

        market_data["event_id"] = event_id

        for date_field in ["end_date", "start_date", "created_at", "updated_at", "uma_end_date",
                           "closed_time", "accepting_orders_timestamp", "deploying_timestamp"]:
            if date_field in market_data and market_data[date_field]:
                date_val = market_data[date_field]
                if isinstance(date_val, str):
                    if "Z" in date_val:
                        market_data[date_field] = date_val.replace("Z", "+00:00")
                    elif "+00" not in date_val:
                        try:
                            dt = datetime.fromisoformat(date_val.replace("Z", "+00:00"))
                            market_data[date_field] = dt.isoformat()
                        except Exception:
                            pass

        float_fields = [
            "volume_num", "liquidity_num",
            "volume24hr", "volume1wk", "volume1mo", "volume1yr",
            "volume24hr_clob", "volume1wk_clob", "volume1mo_clob", "volume1yr_clob",
            "volume_clob", "liquidity_amm", "liquidity_clob",
            "order_price_min_tick_size", "order_min_size", "rewards_min_size", "rewards_max_spread",
            "spread", "one_day_price_change", "one_week_price_change", "last_trade_price",
            "best_bid", "best_ask",
        ]
        for field in float_fields:
            if field in market_data and market_data[field] is not None:
                try:
                    market_data[field] = float(market_data[field])
                except (ValueError, TypeError):
                    market_data[field] = 0.0

        for field in ["competitive", "custom_liveness"]:
            if field in market_data and market_data[field] is not None:
                try:
                    market_data[field] = int(float(market_data[field]))
                except (ValueError, TypeError):
                    market_data[field] = 0

        return market_data

    # ── Upload: events ────────────────────────────────────────────────────────

    def upload_events(self, events: List[Dict], batch_size: int = 500) -> None:
        from psycopg2.extras import execute_batch

        print(f"\n[*] Uploading {len(events)} events to PostgreSQL...")
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        try:
            self._ensure_tags_color_schema(cursor)
            conn.commit()

            upsert_series_sql = f"""
                INSERT INTO {self.TABLE_SERIES} (
                    id, ticker, slug, title, subtitle, series_type, recurrence, description
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    ticker = EXCLUDED.ticker,
                    slug = EXCLUDED.slug,
                    title = EXCLUDED.title,
                    subtitle = EXCLUDED.subtitle,
                    series_type = EXCLUDED.series_type,
                    recurrence = EXCLUDED.recurrence,
                    description = EXCLUDED.description
            """

            # Keep latest label in case API metadata changes over time.
            upsert_tags_sql = f"""
                INSERT INTO {self.TABLE_TAGS} (id, label)
                VALUES (%s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    label = EXCLUDED.label
            """

            insert_events_sql = f"""
                INSERT INTO {self.TABLE_EVENTS} (
                    id, ticker, slug, title, description,
                    start_date, creation_date, end_date, closed_time, created_at, updated_at,
                    image, icon,
                    active, closed, archived, new, featured, restricted, neg_risk, enable_order_book,
                    volume, volume24hr, volume1wk, volume1mo, volume1yr,
                    liquidity, open_interest, liquidity_amm, liquidity_clob,
                    competitive, comment_count, series_id
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    ticker = EXCLUDED.ticker, slug = EXCLUDED.slug, title = EXCLUDED.title,
                    description = EXCLUDED.description, start_date = EXCLUDED.start_date,
                    creation_date = EXCLUDED.creation_date, end_date = EXCLUDED.end_date,
                    closed_time = EXCLUDED.closed_time, updated_at = EXCLUDED.updated_at,
                    image = EXCLUDED.image, icon = EXCLUDED.icon,
                    active = EXCLUDED.active, closed = EXCLUDED.closed, archived = EXCLUDED.archived,
                    new = EXCLUDED.new, featured = EXCLUDED.featured, restricted = EXCLUDED.restricted,
                    neg_risk = EXCLUDED.neg_risk, enable_order_book = EXCLUDED.enable_order_book,
                    volume = EXCLUDED.volume, volume24hr = EXCLUDED.volume24hr,
                    volume1wk = EXCLUDED.volume1wk, volume1mo = EXCLUDED.volume1mo,
                    volume1yr = EXCLUDED.volume1yr, liquidity = EXCLUDED.liquidity,
                    open_interest = EXCLUDED.open_interest, liquidity_amm = EXCLUDED.liquidity_amm,
                    liquidity_clob = EXCLUDED.liquidity_clob, competitive = EXCLUDED.competitive,
                    comment_count = EXCLUDED.comment_count,
                    series_id = EXCLUDED.series_id
            """

            upsert_event_tags_sql = f"""
                INSERT INTO {self.TABLE_EVENT_TAGS} (event_id, tag_id)
                VALUES (%s, %s)
                ON CONFLICT (event_id, tag_id) DO NOTHING
            """

            for i in range(0, len(events), batch_size):
                batch = events[i : i + batch_size]
                series_rows = []
                tags_rows = []
                event_rows = []
                event_tag_rows = []
                unique_series_ids = set()
                unique_tag_ids = set()
                unique_event_tag_pairs = set()

                for event in batch:
                    # 1) Series (dimension) first
                    series_data = self.prepare_series_data(event)
                    if series_data and series_data["id"] not in unique_series_ids:
                        unique_series_ids.add(series_data["id"])
                        series_rows.append((
                            series_data.get("id"),
                            series_data.get("ticker"),
                            series_data.get("slug"),
                            series_data.get("title"),
                            series_data.get("subtitle"),
                            series_data.get("series_type"),
                            series_data.get("recurrence"),
                            series_data.get("description"),
                        ))

                    # 2) Tags (dimension)
                    for raw_tag in event.get("tags", []) or []:
                        tag_data = self.prepare_tag_data(raw_tag)
                        if not tag_data:
                            continue
                        if tag_data["id"] not in unique_tag_ids:
                            unique_tag_ids.add(tag_data["id"])
                            tags_rows.append((tag_data.get("id"), tag_data.get("label")))

                        event_id_raw = event.get("id")
                        if event_id_raw:
                            pair = (str(event_id_raw), tag_data["id"])
                            if pair not in unique_event_tag_pairs:
                                unique_event_tag_pairs.add(pair)
                                event_tag_rows.append(pair)

                    # 3) Event (fact) with series_id FK
                    d = self.prepare_event_data(event)
                    event_rows.append((
                        d.get("id"), d.get("ticker"), d.get("slug"), d.get("title"), d.get("description"),
                        d.get("start_date"), d.get("creation_date"), d.get("end_date"),
                        d.get("closed_time"), d.get("created_at"), d.get("updated_at"),
                        d.get("image"), d.get("icon"),
                        d.get("active", False), d.get("closed", False), d.get("archived", False),
                        d.get("new", False), d.get("featured", False), d.get("restricted", False),
                        d.get("neg_risk", False), d.get("enable_order_book", False),
                        d.get("volume"), d.get("volume24hr"), d.get("volume1wk"),
                        d.get("volume1mo"), d.get("volume1yr"),
                        d.get("liquidity"), d.get("open_interest"),
                        d.get("liquidity_amm"), d.get("liquidity_clob"),
                        d.get("competitive"), d.get("comment_count"), d.get("series_id"),
                    ))
                try:
                    # One transaction per batch: series -> tags -> events -> event_tags.
                    if series_rows:
                        execute_batch(cursor, upsert_series_sql, series_rows, page_size=100)
                    if tags_rows:
                        execute_batch(cursor, upsert_tags_sql, tags_rows, page_size=200)
                    generated_tag_colors = self._assign_missing_tag_colors(
                        cursor,
                        [str(row[0]) for row in tags_rows],
                    )

                    execute_batch(cursor, insert_events_sql, event_rows, page_size=100)

                    if event_tag_rows:
                        execute_batch(cursor, upsert_event_tags_sql, event_tag_rows, page_size=300)

                    conn.commit()
                    self.stats["series_upserted"] += len(series_rows)
                    self.stats["tags_upserted"] += len(tags_rows)
                    self.stats["tag_colors_generated"] += generated_tag_colors
                    self.stats["events_inserted"] += len(event_rows)
                    self.stats["event_tags_upserted"] += len(event_tag_rows)
                    print(
                        f"  [OK] Batch {i // batch_size + 1}: "
                        f"{len(event_rows)} events, {len(series_rows)} series, "
                        f"{len(tags_rows)} tags, {generated_tag_colors} tag_colors, "
                        f"{len(event_tag_rows)} event_tags"
                    )
                except Exception as e:
                    conn.rollback()
                    msg = f"Error uploading events batch {i // batch_size + 1}: {e}"
                    print(f"  [ERROR] {msg}")
                    self.stats["errors"].append(msg)
        finally:
            cursor.close()
            conn.close()

    # ── Upload: markets ───────────────────────────────────────────────────────

    def upload_markets(self, events: List[Dict], batch_size: int = 500) -> None:
        from psycopg2.extras import execute_batch

        all_markets = []
        for event in events:
            event_id = event.get("id")
            for market in event.get("markets", []):
                all_markets.append(self.prepare_market_data(market, event_id))

        print(f"\n[*] Uploading {len(all_markets)} markets to PostgreSQL...")
        conn = psycopg2.connect(**self.connection_params)
        cursor = conn.cursor()
        try:
            insert_sql = f"""
                INSERT INTO {self.TABLE_MARKETS} (
                    id, event_id, question, condition_id, slug, question_id,
                    end_date, start_date, created_at, updated_at, closed_time,
                    image, icon, description, outcomes, outcome_prices,
                    volume, volume_num, volume24hr, liquidity, liquidity_num,
                    active, closed, new, featured, archived, restricted, enable_order_book,
                    neg_risk, ready, funded
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    question = EXCLUDED.question, condition_id = EXCLUDED.condition_id,
                    updated_at = EXCLUDED.updated_at, closed_time = EXCLUDED.closed_time,
                    outcome_prices = EXCLUDED.outcome_prices, volume_num = EXCLUDED.volume_num,
                    volume24hr = EXCLUDED.volume24hr, liquidity_num = EXCLUDED.liquidity_num,
                    active = EXCLUDED.active, closed = EXCLUDED.closed
            """
            for i in range(0, len(all_markets), batch_size):
                batch = all_markets[i : i + batch_size]
                rows = []
                for m in batch:
                    outcomes_str = json.dumps(m.get("outcomes")) if m.get("outcomes") else None
                    prices_str = json.dumps(m.get("outcome_prices")) if m.get("outcome_prices") else None
                    rows.append((
                        m.get("id"), m.get("event_id"), m.get("question"), m.get("condition_id"),
                        m.get("slug"), m.get("question_id"),
                        m.get("end_date"), m.get("start_date"), m.get("created_at"),
                        m.get("updated_at"), m.get("closed_time"),
                        m.get("image"), m.get("icon"), m.get("description"),
                        outcomes_str, prices_str,
                        m.get("volume"), m.get("volume_num"), m.get("volume24hr"),
                        m.get("liquidity"), m.get("liquidity_num"),
                        m.get("active", False), m.get("closed", False), m.get("new", False),
                        m.get("featured", False), m.get("archived", False),
                        m.get("restricted", False), m.get("enable_order_book", False),
                        m.get("neg_risk", False), m.get("ready", False), m.get("funded", False),
                    ))
                try:
                    execute_batch(cursor, insert_sql, rows, page_size=100)
                    conn.commit()
                    self.stats["markets_inserted"] += len(rows)
                    print(f"  [OK] Batch {i // batch_size + 1}: {len(rows)} markets")
                except Exception as e:
                    conn.rollback()
                    msg = f"Error uploading markets batch {i // batch_size + 1}: {e}"
                    print(f"  [ERROR] {msg}")
                    self.stats["errors"].append(msg)
        finally:
            cursor.close()
            conn.close()

    # ── Upload: redemptions ───────────────────────────────────────────────────

    def upload_redemptions_batch(
        self,
        redemptions: List[Dict],
        chunk_size: int = 5000,
        use_new_client: bool = True,  # kept for backwards compat
    ) -> bool:
        if not redemptions:
            return True
        return self._upload_to_local_postgres(redemptions, chunk_size)

    def _upload_to_local_postgres(self, redemptions: List[Dict], chunk_size: int = 5000) -> bool:
        conn = None
        cursor = None
        try:
            conn = psycopg2.connect(**self.connection_params)
            cursor = conn.cursor()

            temp_table = f"{self.TABLE_REDEMPTIONS}_temp_{os.getpid()}"
            cursor.execute(f"""
                CREATE TEMP TABLE {temp_table} (LIKE {self.TABLE_REDEMPTIONS} INCLUDING ALL)
                ON COMMIT DROP
            """)

            total_uploaded = 0
            num_chunks = (len(redemptions) + chunk_size - 1) // chunk_size
            show_progress = len(redemptions) > 1000

            if show_progress:
                print(f"      TURBO MODE: PostgreSQL COPY — {len(redemptions)} records in chunks of {chunk_size}", flush=True)

            for i in range(0, len(redemptions), chunk_size):
                chunk = redemptions[i : i + chunk_size]
                chunk_num = i // chunk_size + 1
                if show_progress and num_chunks > 1:
                    print(f"      Chunk {chunk_num}/{num_chunks} ({len(chunk)} records)...", end=" ", flush=True)

                def escape(val):
                    if val is None:
                        return "\\N"
                    return str(val).replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")

                csv_buf = io.StringIO()
                for r in chunk:
                    csv_buf.write("\t".join([
                        escape(r.get("transaction_hash")),
                        escape(r.get("condition_id")),
                        escape(r.get("event_id")),
                        escape(r.get("market_id")),
                        escape(r.get("market_question")),
                        escape(r.get("event_title", "")),
                        escape(r.get("redeemer_address")),
                        escape(float(r.get("payout_usdc", 0))),
                        escape(int(r.get("timestamp_unix", 0))),
                        escape(r.get("timestamp_human")),
                    ]) + "\n")
                csv_buf.seek(0)

                try:
                    cursor.copy_from(
                        csv_buf,
                        temp_table,
                        columns=["transaction_hash", "condition_id", "event_id", "market_id",
                                 "market_question", "event_title", "redeemer_address", "payout_usdc",
                                 "timestamp_unix", "timestamp_human"],
                    )
                    total_uploaded += len(chunk)
                    if show_progress and num_chunks > 1:
                        print("OK", flush=True)
                except Exception as e:
                    conn.rollback()
                    msg = str(e)[:200]
                    print(f"\n      COPY ERROR chunk {chunk_num}: {msg}")
                    self.stats["errors"].append(msg)
                    return False

            cursor.execute(f"""
                INSERT INTO {self.TABLE_REDEMPTIONS}
                SELECT * FROM {temp_table}
                ON CONFLICT (transaction_hash, redeemer_address)
                DO UPDATE SET
                    payout_usdc = EXCLUDED.payout_usdc,
                    timestamp_unix = EXCLUDED.timestamp_unix,
                    timestamp_human = EXCLUDED.timestamp_human
            """)
            conn.commit()
            self.stats["redemptions_inserted"] += total_uploaded
            if show_progress:
                print(f"      Merged {total_uploaded} records OK", flush=True)
            return True

        except Exception as e:
            if conn:
                conn.rollback()
            msg = str(e)[:200]
            print(f"\n      UPLOAD FAILED: {msg}")
            self.stats["errors"].append(msg)
            return False
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    # ── High-level ────────────────────────────────────────────────────────────

    def upload_json_file(self, filepath: str) -> None:
        print("[*] Starting PostgreSQL upload...")
        print("=" * 70)
        data = self.load_json_data(filepath)
        self.upload_events(data.get("events", []))
        self.upload_markets(data.get("events", []))
        self.print_summary()

    def print_summary(self):
        print("\n" + "=" * 70)
        print("UPLOAD SUMMARY")
        print("=" * 70)
        if self.stats["events_inserted"]:
            print(f"[OK] Events inserted/updated: {self.stats['events_inserted']}")
        if self.stats["series_upserted"]:
            print(f"[OK] Series inserted/updated: {self.stats['series_upserted']}")
        if self.stats["tags_upserted"]:
            print(f"[OK] Tags inserted/updated: {self.stats['tags_upserted']}")
        if self.stats["tag_colors_generated"]:
            print(f"[OK] Tag colors generated: {self.stats['tag_colors_generated']}")
        if self.stats["event_tags_upserted"]:
            print(f"[OK] Event tags inserted/updated: {self.stats['event_tags_upserted']}")
        if self.stats["markets_inserted"]:
            print(f"[OK] Markets inserted/updated: {self.stats['markets_inserted']}")
        if self.stats["redemptions_inserted"]:
            print(f"[OK] Redemptions inserted/updated: {self.stats['redemptions_inserted']}")
        if self.stats["errors"]:
            print(f"\n[WARN] Errors: {len(self.stats['errors'])}")
            for err in self.stats["errors"][:5]:
                print(f"   - {err}")
        else:
            print("\n[SUCCESS] Upload completed with no errors!")
        print("=" * 70)


def main():
    import sys

    args = sys.argv[1:]
    is_redemptions = "--redemptions" in args or "-r" in args
    show_help = "--help" in args or "-h" in args

    filepath = next((a for a in args if not a.startswith("-")), None)

    if show_help:
        print("Usage: python db_uploader.py [filepath] [--redemptions]")
        return

    if not filepath:
        json_dir = "json_output"
        if os.path.exists(json_dir):
            files = sorted(
                [f for f in os.listdir(json_dir) if f.endswith(".json")],
                key=lambda x: os.path.getmtime(os.path.join(json_dir, x)),
                reverse=True,
            )
            if files:
                filepath = os.path.join(json_dir, files[0])
                print(f"[*] Using latest file: {filepath}")
            else:
                print("[ERROR] No JSON files in json_output/")
                return
        else:
            print("[ERROR] json_output/ directory not found")
            return

    if not os.path.exists(filepath):
        print(f"[ERROR] File not found: {filepath}")
        return

    try:
        uploader = SupabaseUploader()

        if is_redemptions:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            all_redemptions: List[Dict] = []
            if isinstance(data, list):
                if data and "redemptions" in data[0]:
                    for item in data:
                        all_redemptions.extend(item.get("redemptions", []))
                else:
                    all_redemptions = data
            else:
                print(f"[ERROR] Expected list, got {type(data)}")
                return

            print(f"[*] Uploading {len(all_redemptions)} redemptions...")
            success = uploader.upload_redemptions_batch(all_redemptions)
            if success:
                print(f"[OK] Uploaded {uploader.stats['redemptions_inserted']} redemptions")
            else:
                print("[ERROR] Failed to upload redemptions")
        else:
            uploader.upload_json_file(filepath)

    except ConnectionError as e:
        print(f"[ERROR] {e}")
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
