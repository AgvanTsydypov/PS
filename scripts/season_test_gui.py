"""
GUI workbench for testing PolyStars seasons logic.

Run:
    python scripts/season_test_gui.py
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional

import psycopg2.extras

# Add project root to path (same approach as other scripts)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.data_loading_manager import DataLoadingManager
from scripts.daily_scheduler_simple import SimplifiedScheduler
from scripts.season_manager import SeasonManager


class SeasonTestWorkbench:
    """Simple desktop GUI for seasons testing scenarios."""

    def __init__(self) -> None:
        self.manager = DataLoadingManager(use_local_db=True)
        self.season_manager = SeasonManager(use_local_db=True)
        self.scheduler = SimplifiedScheduler(use_local_db=True, dry_run=False)
        self.root = tk.Tk()
        self.root.title("PolyStars Seasons Test Workbench")
        self.root.geometry("1260x860")

        self.sample_wallets: List[str] = []
        self.seasons_lookup: Dict[str, int] = {}
        self.wallet_filter_var = tk.StringVar(value="all")
        self.wallet_filter_var.trace_add("write", self._on_wallet_filter_changed)
        self.auto_refresh_ms = 1000

        self._build_ui()
        self._refresh_all()
        self._start_auto_refresh()

    # ------------------------------
    # UI builders
    # ------------------------------
    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.tab_overview = ttk.Frame(notebook)
        self.tab_eligibility = ttk.Frame(notebook)
        self.tab_claims = ttk.Frame(notebook)
        self.tab_season_claims = ttk.Frame(notebook)
        self.tab_scenarios = ttk.Frame(notebook)
        self.tab_reset = ttk.Frame(notebook)

        notebook.add(self.tab_overview, text="Overview")
        notebook.add(self.tab_eligibility, text="Eligibility")
        notebook.add(self.tab_claims, text="Fake Claims")
        notebook.add(self.tab_season_claims, text="Season Claims")
        notebook.add(self.tab_scenarios, text="Scenarios")
        notebook.add(self.tab_reset, text="Reset")

        self._build_overview_tab()
        self._build_eligibility_tab()
        self._build_claims_tab()
        self._build_season_claims_tab()
        self._build_scenarios_tab()
        self._build_reset_tab()

    def _build_overview_tab(self) -> None:
        controls = ttk.Frame(self.tab_overview)
        controls.pack(fill="x", padx=8, pady=8)

        ttk.Button(controls, text="Refresh All", command=self._refresh_all).pack(side="left", padx=(0, 6))
        ttk.Button(
            controls,
            text="Run --season-update",
            command=self._run_season_lifecycle_update,
        ).pack(side="left", padx=(0, 6))

        columns = (
            "id",
            "type",
            "season_number",
            "start_date",
            "end_date",
            "total_supply",
            "remaining_supply",
            "is_active",
            "is_completed",
        )
        self.seasons_tree = ttk.Treeview(self.tab_overview, columns=columns, show="headings", height=12)
        for col in columns:
            self.seasons_tree.heading(col, text=col)
            self.seasons_tree.column(col, width=130, anchor="w")
        self.seasons_tree.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Label(
            self.tab_overview,
            text="Latest season events log",
        ).pack(anchor="w", padx=8)

        self.logs_text = tk.Text(self.tab_overview, height=22, wrap="none")
        self.logs_text.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    def _build_eligibility_tab(self) -> None:
        frame = ttk.Frame(self.tab_eligibility)
        frame.pack(fill="x", padx=8, pady=8)

        ttk.Label(frame, text="Wallet").grid(row=0, column=0, sticky="w")
        self.elig_wallet_var = tk.StringVar()
        self.elig_wallet_combo = ttk.Combobox(frame, textvariable=self.elig_wallet_var, width=62)
        self.elig_wallet_combo.grid(row=0, column=1, sticky="w", padx=(8, 8))

        ttk.Button(frame, text="Reload wallets", command=self._refresh_wallets).grid(row=0, column=2, sticky="w")
        ttk.Button(frame, text="Check eligibility", command=self._check_eligibility).grid(row=0, column=3, sticky="w", padx=(8, 0))

        ttk.Label(frame, text="Wallet list filter").grid(row=1, column=0, sticky="w", pady=(8, 0))
        wallet_filter_combo = ttk.Combobox(
            frame,
            textvariable=self.wallet_filter_var,
            values=["all", "origin", "non_origin"],
            width=16,
            state="readonly",
        )
        wallet_filter_combo.grid(row=1, column=1, sticky="w", padx=(8, 8), pady=(8, 0))
        wallet_filter_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_wallets())

        self.eligibility_text = tk.Text(self.tab_eligibility, height=40, wrap="word")
        self.eligibility_text.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    def _build_claims_tab(self) -> None:
        frame = ttk.Frame(self.tab_claims)
        frame.pack(fill="x", padx=8, pady=8)

        ttk.Label(frame, text="Wallet").grid(row=0, column=0, sticky="w")
        self.claim_wallet_var = tk.StringVar()
        self.claim_wallet_combo = ttk.Combobox(frame, textvariable=self.claim_wallet_var, width=46)
        self.claim_wallet_combo.grid(row=0, column=1, columnspan=2, sticky="w", padx=(8, 8))

        ttk.Label(frame, text="Wallet list filter").grid(row=0, column=3, sticky="w")
        claims_filter_combo = ttk.Combobox(
            frame,
            textvariable=self.wallet_filter_var,
            values=["all", "origin", "non_origin"],
            width=14,
            state="readonly",
        )
        claims_filter_combo.grid(row=0, column=4, sticky="w", padx=(8, 0))
        claims_filter_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_wallets())

        ttk.Label(frame, text="Season").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.claim_season_var = tk.StringVar()
        self.claim_season_combo = ttk.Combobox(frame, textvariable=self.claim_season_var, width=42, state="readonly")
        self.claim_season_combo.grid(row=1, column=1, sticky="w", padx=(8, 8), pady=(8, 0))

        ttk.Label(frame, text="Phase").grid(row=1, column=2, sticky="w", pady=(8, 0))
        self.claim_phase_var = tk.StringVar(value="breach")
        self.claim_phase_combo = ttk.Combobox(
            frame,
            textvariable=self.claim_phase_var,
            values=["breach", "vault", "scavenge"],
            width=16,
            state="readonly",
        )
        self.claim_phase_combo.grid(row=1, column=3, sticky="w", pady=(8, 0))

        ttk.Label(frame, text="Status").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.claim_status_var = tk.StringVar(value="COMPLETED")
        ttk.Combobox(
            frame,
            textvariable=self.claim_status_var,
            values=["PENDING", "PROCESSING", "COMPLETED", "FAILED"],
            width=16,
            state="readonly",
        ).grid(row=2, column=1, sticky="w", padx=(8, 8), pady=(8, 0))

        ttk.Label(frame, text="Token ID (optional)").grid(row=2, column=2, sticky="w", pady=(8, 0))
        self.claim_token_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.claim_token_var, width=20).grid(row=2, column=3, sticky="w", pady=(8, 0))

        self.generate_tx_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text="Auto-generate tx_hash",
            variable=self.generate_tx_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.auto_phase_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text="Auto phase from season window",
            variable=self.auto_phase_var,
            command=self._on_phase_mode_toggle,
        ).grid(row=3, column=2, sticky="w", pady=(8, 0))

        self.force_insert_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Force insert (ignore eligibility warning)",
            variable=self.force_insert_var,
        ).grid(row=3, column=3, sticky="w", pady=(8, 0))

        ttk.Button(frame, text="Insert fake claim", command=self._insert_fake_claim).grid(row=3, column=4, sticky="e", pady=(8, 0))
        self.claim_season_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_claim_season_changed())
        self.claim_wallet_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_claim_season_info())
        self._on_phase_mode_toggle()

        claims_paned = ttk.Panedwindow(self.tab_claims, orient=tk.VERTICAL)
        claims_paned.pack(fill="both", expand=True, padx=8, pady=(2, 8))

        info_frame = ttk.Frame(claims_paned)
        ttk.Label(info_frame, text="Selected season context").pack(anchor="w")
        self.claim_season_info_text = tk.Text(info_frame, height=11, wrap="word")
        self.claim_season_info_text.pack(fill="both", expand=True, pady=(4, 0))
        self.claim_season_info_text.tag_configure("countdown_active", foreground="#0b5d1e")
        self.claim_season_info_text.tag_configure("season_age", foreground="#0b5ed7")

        output_frame = ttk.Frame(claims_paned)
        ttk.Label(output_frame, text="Fake claims output").pack(anchor="w")
        self.claims_output_text = tk.Text(output_frame, height=22, wrap="word")
        self.claims_output_text.pack(fill="both", expand=True, pady=(4, 0))

        claims_paned.add(info_frame, weight=1)
        claims_paned.add(output_frame, weight=2)

    def _build_season_claims_tab(self) -> None:
        frame = ttk.Frame(self.tab_season_claims)
        frame.pack(fill="x", padx=8, pady=8)

        ttk.Label(frame, text="Season").grid(row=0, column=0, sticky="w")
        self.season_claims_season_var = tk.StringVar()
        self.season_claims_combo = ttk.Combobox(
            frame,
            textvariable=self.season_claims_season_var,
            width=54,
            state="readonly",
        )
        self.season_claims_combo.grid(row=0, column=1, sticky="w", padx=(8, 8))

        ttk.Button(frame, text="Refresh seasons", command=self._refresh_seasons).grid(row=0, column=2, sticky="w")
        ttk.Button(frame, text="Load claims", command=self._refresh_season_claims).grid(row=0, column=3, sticky="w", padx=(8, 0))

        self.season_claims_summary_text = tk.Text(self.tab_season_claims, height=5, wrap="word")
        self.season_claims_summary_text.pack(fill="x", padx=8, pady=(4, 8))

        columns = (
            "id",
            "wallet",
            "phase",
            "status",
            "tx_hash",
            "token_id",
            "timestamp",
            "created_at",
        )
        self.season_claims_tree = ttk.Treeview(self.tab_season_claims, columns=columns, show="headings", height=26)
        for col in columns:
            self.season_claims_tree.heading(col, text=col)
            width = 160
            if col in {"id", "phase", "status", "token_id"}:
                width = 90
            if col == "wallet":
                width = 300
            self.season_claims_tree.column(col, width=width, anchor="w")
        self.season_claims_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _build_scenarios_tab(self) -> None:
        frame = ttk.Frame(self.tab_scenarios)
        frame.pack(fill="x", padx=8, pady=8)

        ttk.Label(frame, text="Target season").grid(row=0, column=0, sticky="w")
        self.scenario_season_var = tk.StringVar()
        self.scenario_season_combo = ttk.Combobox(frame, textvariable=self.scenario_season_var, width=42, state="readonly")
        self.scenario_season_combo.grid(row=0, column=1, sticky="w", padx=(8, 12))
        self.scenario_season_combo.bind("<<ComboboxSelected>>", lambda _e: self._load_scenario_season_params())

        ttk.Button(frame, text="Reload seasons", command=self._refresh_seasons).grid(row=0, column=2, sticky="w")

        quick = ttk.LabelFrame(self.tab_scenarios, text="Quick phase setup (standard season)")
        quick.pack(fill="x", padx=8, pady=(8, 6))
        ttk.Button(quick, text="Set Breach (day 2)", command=lambda: self._set_standard_phase_from_now(1)).pack(side="left", padx=8, pady=8)
        ttk.Button(quick, text="Set Vault (day 5)", command=lambda: self._set_standard_phase_from_now(4)).pack(side="left", padx=8, pady=8)
        ttk.Button(quick, text="Set Scavenge (day 8)", command=lambda: self._set_standard_phase_from_now(7)).pack(side="left", padx=8, pady=8)
        ttk.Button(quick, text="Set Transmission (day 10)", command=lambda: self._set_standard_phase_from_now(9)).pack(side="left", padx=8, pady=8)

        manual = ttk.LabelFrame(self.tab_scenarios, text="Manual controls")
        manual.pack(fill="x", padx=8, pady=(0, 6))

        ttk.Label(manual, text="Shift start_date by days (from now):").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.shift_days_var = tk.StringVar(value="0")
        ttk.Entry(manual, textvariable=self.shift_days_var, width=8).grid(row=0, column=1, sticky="w", pady=8)
        ttk.Button(manual, text="Apply date shift", command=self._apply_manual_date_shift).grid(row=0, column=2, sticky="w", padx=8, pady=8)

        ttk.Label(manual, text="Set remaining_supply:").grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))
        self.remaining_supply_var = tk.StringVar()
        ttk.Entry(manual, textvariable=self.remaining_supply_var, width=12).grid(row=1, column=1, sticky="w", pady=(0, 8))
        ttk.Button(manual, text="Apply supply", command=self._apply_remaining_supply).grid(row=1, column=2, sticky="w", padx=8, pady=(0, 8))

        editor = ttk.LabelFrame(self.tab_scenarios, text="Advanced season editor")
        editor.pack(fill="x", padx=8, pady=(0, 6))

        self.scenario_auto_sync_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            editor,
            text="Auto-sync editor from selected season",
            variable=self.scenario_auto_sync_var,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 8))
        ttk.Button(editor, text="Load selected season params", command=self._load_scenario_season_params).grid(
            row=0, column=3, sticky="w", padx=8, pady=(8, 8)
        )

        ttk.Label(editor, text="season_number").grid(row=1, column=0, sticky="w", padx=8)
        self.scenario_season_number_var = tk.StringVar()
        ttk.Entry(editor, textvariable=self.scenario_season_number_var, width=14).grid(row=1, column=1, sticky="w", pady=(0, 6))

        ttk.Label(editor, text="total_supply").grid(row=1, column=2, sticky="w", padx=8)
        self.scenario_total_supply_var = tk.StringVar()
        ttk.Entry(editor, textvariable=self.scenario_total_supply_var, width=14).grid(row=1, column=3, sticky="w", pady=(0, 6))

        ttk.Label(editor, text="remaining_supply").grid(row=1, column=4, sticky="w", padx=8)
        self.scenario_remaining_supply_var = tk.StringVar()
        ttk.Entry(editor, textvariable=self.scenario_remaining_supply_var, width=14).grid(row=1, column=5, sticky="w", pady=(0, 6))

        ttk.Label(editor, text="start_date (ISO UTC)").grid(row=2, column=0, sticky="w", padx=8)
        self.scenario_start_date_var = tk.StringVar()
        ttk.Entry(editor, textvariable=self.scenario_start_date_var, width=32).grid(row=2, column=1, columnspan=2, sticky="w", pady=(0, 6))

        ttk.Label(editor, text="end_date (ISO UTC)").grid(row=2, column=3, sticky="w", padx=8)
        self.scenario_end_date_var = tk.StringVar()
        ttk.Entry(editor, textvariable=self.scenario_end_date_var, width=32).grid(row=2, column=4, columnspan=2, sticky="w", pady=(0, 6))

        ttk.Label(editor, text="is_active").grid(row=3, column=0, sticky="w", padx=8)
        self.scenario_is_active_var = tk.StringVar(value="true")
        ttk.Combobox(
            editor,
            textvariable=self.scenario_is_active_var,
            values=["true", "false"],
            width=12,
            state="readonly",
        ).grid(row=3, column=1, sticky="w", pady=(0, 8))

        ttk.Label(editor, text="is_completed").grid(row=3, column=2, sticky="w", padx=8)
        self.scenario_is_completed_var = tk.StringVar(value="false")
        ttk.Combobox(
            editor,
            textvariable=self.scenario_is_completed_var,
            values=["true", "false"],
            width=12,
            state="readonly",
        ).grid(row=3, column=3, sticky="w", pady=(0, 8))

        ttk.Button(editor, text="Set now as start (+10d end)", command=self._set_scenario_now_start).grid(
            row=3, column=4, sticky="w", padx=8, pady=(0, 8)
        )
        ttk.Button(editor, text="Apply advanced params", command=self._apply_scenario_params).grid(
            row=3, column=5, sticky="e", padx=8, pady=(0, 8)
        )

        self.scenario_output_text = tk.Text(self.tab_scenarios, height=28, wrap="word")
        self.scenario_output_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _build_reset_tab(self) -> None:
        frame = ttk.Frame(self.tab_reset)
        frame.pack(fill="x", padx=8, pady=8)

        ttk.Label(
            frame,
            text="Reset uses sql/queries/clear_seasons_logic.sql and will wipe seasons/claims/season_events_log/winner_wallets_nft_to_claim.",
        ).pack(anchor="w")

        self.confirm_reset_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="I understand and want to reset test seasons data",
            variable=self.confirm_reset_var,
        ).pack(anchor="w", pady=(8, 8))

        ttk.Button(frame, text="Run reset SQL", command=self._run_reset_sql).pack(anchor="w")

        self.reset_output_text = tk.Text(self.tab_reset, height=34, wrap="word")
        self.reset_output_text.pack(fill="both", expand=True, padx=0, pady=(8, 8))

    # ------------------------------
    # Data refresh helpers
    # ------------------------------
    def _refresh_all(self) -> None:
        self._refresh_wallets()
        self._refresh_seasons()
        self._refresh_overview()
        self._refresh_season_claims()
        self._refresh_claim_season_info()

    def _start_auto_refresh(self) -> None:
        self.root.after(self.auto_refresh_ms, self._auto_refresh_tick)

    def _auto_refresh_tick(self) -> None:
        try:
            self._refresh_seasons()
            self._refresh_claim_season_info()
        except Exception:
            pass
        finally:
            self.root.after(self.auto_refresh_ms, self._auto_refresh_tick)

    def _on_wallet_filter_changed(self, *_args: object) -> None:
        """Refresh wallet candidates immediately when filter changes."""
        if not hasattr(self, "elig_wallet_combo") or not hasattr(self, "claim_wallet_combo"):
            return
        self._refresh_wallets()

    def _refresh_wallets(self) -> None:
        wallet_filter = self.wallet_filter_var.get().strip() or "all"
        if wallet_filter not in {"all", "origin", "non_origin"}:
            wallet_filter = "all"

        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    WITH origin_wallets AS (
                        SELECT lower(wallet_address) AS wallet
                        FROM v_origin_wallets
                    ),
                    position_wallets AS (
                        SELECT lower(proxy_wallet) AS wallet
                        FROM user_closed_positions
                        WHERE proxy_wallet IS NOT NULL
                        ORDER BY lower(proxy_wallet)
                        LIMIT 200
                    ),
                    claimed_wallets AS (
                        SELECT lower(user_wallet) AS wallet
                        FROM claims
                        ORDER BY lower(user_wallet)
                        LIMIT 200
                    ),
                    candidates AS (
                        SELECT wallet FROM origin_wallets
                        UNION
                        SELECT wallet FROM position_wallets
                        UNION
                        SELECT wallet FROM claimed_wallets
                    ),
                    normalized AS (
                        SELECT DISTINCT wallet
                        FROM candidates
                        WHERE wallet ~* '^0x[a-f0-9]{40}$'
                    ),
                    classified AS (
                        SELECT
                            n.wallet,
                            (o.wallet_address IS NOT NULL) AS is_origin
                        FROM normalized n
                        LEFT JOIN v_origin_wallets o
                            ON o.wallet_address = n.wallet
                    )
                    SELECT wallet
                    FROM classified
                    WHERE (
                        %s = 'all'
                        OR (%s = 'origin' AND is_origin = TRUE)
                        OR (%s = 'non_origin' AND is_origin = FALSE)
                    )
                    ORDER BY wallet
                    LIMIT 300
                    """,
                    (wallet_filter, wallet_filter, wallet_filter),
                )
                rows = cursor.fetchall()
            self.sample_wallets = [r[0] for r in rows]
        except Exception:
            self.sample_wallets = []
        finally:
            conn.close()

        self.elig_wallet_combo["values"] = self.sample_wallets
        self.claim_wallet_combo["values"] = self.sample_wallets

        if self.sample_wallets and self.elig_wallet_var.get() not in self.sample_wallets:
            self.elig_wallet_var.set(self.sample_wallets[0])
        elif not self.sample_wallets:
            self.elig_wallet_var.set("")

        if self.sample_wallets and self.claim_wallet_var.get() not in self.sample_wallets:
            self.claim_wallet_var.set(self.sample_wallets[0])
        elif not self.sample_wallets:
            self.claim_wallet_var.set("")

    def _refresh_seasons(self) -> None:
        previous_claim_season_id = self._extract_season_id_from_label(self.claim_season_var.get())
        previous_season_claims_season_id = self._extract_season_id_from_label(self.season_claims_season_var.get())
        previous_scenario_season_id = self._extract_season_id_from_label(self.scenario_season_var.get())

        self.seasons_lookup = {}
        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, type, season_number, start_date, end_date, total_supply, remaining_supply, is_active, is_completed
                    FROM seasons
                    ORDER BY is_active DESC, start_date DESC, id DESC
                    """
                )
                seasons = cursor.fetchall()
        finally:
            conn.close()

        default_label: Optional[str] = None
        labels: List[str] = []
        id_to_label: Dict[int, str] = {}
        latest_standard_active_label: Optional[str] = None
        latest_standard_any_label: Optional[str] = None
        for row in seasons:
            label = (
                f"id={row['id']} | {row['type']}#{row['season_number']} | "
                f"remaining={row['remaining_supply']}/{row['total_supply']} | "
                f"active={row['is_active']}"
            )
            self.seasons_lookup[label] = int(row["id"])
            id_to_label[int(row["id"])] = label
            labels.append(label)
            if row["type"] == "standard" and latest_standard_any_label is None:
                latest_standard_any_label = label
            if row["type"] == "standard" and bool(row["is_active"]) and latest_standard_active_label is None:
                latest_standard_active_label = label

        default_label = latest_standard_active_label or latest_standard_any_label or (labels[0] if labels else None)

        self.claim_season_combo["values"] = labels
        self.season_claims_combo["values"] = labels
        self.scenario_season_combo["values"] = labels

        claim_label = id_to_label.get(previous_claim_season_id) if previous_claim_season_id else None
        season_claims_label = id_to_label.get(previous_season_claims_season_id) if previous_season_claims_season_id else None
        scenario_label = id_to_label.get(previous_scenario_season_id) if previous_scenario_season_id else None

        if claim_label:
            self.claim_season_var.set(claim_label)
        elif default_label:
            self.claim_season_var.set(default_label)

        if season_claims_label:
            self.season_claims_season_var.set(season_claims_label)
        elif default_label:
            self.season_claims_season_var.set(default_label)

        if scenario_label:
            self.scenario_season_var.set(scenario_label)
        elif default_label:
            self.scenario_season_var.set(default_label)

        self._sync_claim_phase_preview()
        if hasattr(self, "scenario_auto_sync_var") and self.scenario_auto_sync_var.get():
            self._load_scenario_season_params(silent=True)

    def _refresh_overview(self) -> None:
        for item in self.seasons_tree.get_children():
            self.seasons_tree.delete(item)

        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, type, season_number, start_date, end_date, total_supply, remaining_supply, is_active, is_completed
                    FROM seasons
                    ORDER BY type, season_number
                    """
                )
                seasons = cursor.fetchall()
                for row in seasons:
                    self.seasons_tree.insert(
                        "",
                        "end",
                        values=(
                            row["id"],
                            row["type"],
                            row["season_number"],
                            str(row["start_date"]),
                            str(row["end_date"]),
                            row["total_supply"],
                            row["remaining_supply"],
                            row["is_active"],
                            row["is_completed"],
                        ),
                    )

                cursor.execute(
                    """
                    SELECT created_at, event_name, season_id, details
                    FROM season_events_log
                    ORDER BY created_at DESC
                    LIMIT 60
                    """
                )
                logs = cursor.fetchall()
        finally:
            conn.close()

        self.logs_text.delete("1.0", "end")
        for row in logs:
            line = (
                f"[{row['created_at']}] event={row['event_name']} "
                f"season_id={row['season_id']} details={row['details']}\n"
            )
            self.logs_text.insert("end", line)

    # ------------------------------
    # Actions
    # ------------------------------
    @staticmethod
    def _extract_season_id_from_label(label: str) -> Optional[int]:
        label = (label or "").strip()
        if not label.startswith("id="):
            return None
        first_part = label.split("|", 1)[0].strip()
        raw = first_part.replace("id=", "").strip()
        try:
            return int(raw)
        except ValueError:
            return None

    def _get_selected_season_id(self, label: str) -> Optional[int]:
        direct = self.seasons_lookup.get(label)
        if direct is not None:
            return direct
        return self._extract_season_id_from_label(label)

    def _get_phase_enum_values(self) -> List[str]:
        """Read current enum labels for claims.phase_type from PostgreSQL."""
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT e.enumlabel
                    FROM pg_type t
                    JOIN pg_enum e ON t.oid = e.enumtypid
                    WHERE t.typname = 'phase_type'
                    ORDER BY e.enumsortorder
                    """
                )
                return [row[0] for row in cursor.fetchall()]
        finally:
            conn.close()

    def _derive_claim_phase_type(self, season_id: int) -> tuple[Optional[str], Optional[str]]:
        try:
            phase_info = self.season_manager.get_current_phase(season_id)
        except Exception as exc:
            return None, f"Could not detect season phase: {exc}"

        current_phase = str(phase_info.get("phase") or "")
        is_claim_open = bool(phase_info.get("is_claim_open"))
        if not is_claim_open:
            return None, f"Claims are closed in phase: {current_phase or 'unknown'}"
        if current_phase in {"breach", "vault", "scavenge"}:
            return current_phase, None
        return None, f"Unsupported claim phase for insert: {current_phase or 'unknown'}"

    def _on_phase_mode_toggle(self) -> None:
        if self.auto_phase_var.get():
            self.claim_phase_combo.configure(state="disabled")
            self._sync_claim_phase_preview()
        else:
            self.claim_phase_combo.configure(state="readonly")
        self._refresh_claim_season_info()

    def _sync_claim_phase_preview(self) -> None:
        if not self.auto_phase_var.get():
            self._refresh_claim_season_info()
            return
        season_id = self._get_selected_season_id(self.claim_season_var.get().strip())
        if not season_id:
            self.claim_phase_var.set("breach")
            self._refresh_claim_season_info()
            return
        detected_phase, _ = self._derive_claim_phase_type(season_id)
        self.claim_phase_var.set(detected_phase or "breach")
        self._refresh_claim_season_info()

    def _on_claim_season_changed(self) -> None:
        self._sync_claim_phase_preview()

    @staticmethod
    def _fmt_dt(value: Optional[datetime]) -> str:
        if value is None:
            return "n/a"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def _fmt_remaining(delta_seconds: float) -> str:
        """Format remaining seconds as compact human-readable duration."""
        if delta_seconds <= 0:
            return "0s"
        total = int(delta_seconds)
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        parts: List[str] = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes or parts:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        return " ".join(parts)

    def _refresh_claim_season_info(self) -> None:
        if not hasattr(self, "claim_season_info_text"):
            return

        season_id = self._get_selected_season_id(self.claim_season_var.get().strip())
        prev_yview = self.claim_season_info_text.yview()
        self.claim_season_info_text.delete("1.0", "end")
        if not season_id:
            self.claim_season_info_text.insert("end", "Select season to show current phase and transition rules.")
            if prev_yview:
                self.claim_season_info_text.yview_moveto(prev_yview[0])
            return

        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, type, season_number, start_date, end_date, total_supply, remaining_supply, is_active, is_completed
                    FROM seasons
                    WHERE id = %s
                    """,
                    (season_id,),
                )
                season = cursor.fetchone()
        finally:
            conn.close()

        if not season:
            self.claim_season_info_text.insert("end", f"Season {season_id} not found.")
            if prev_yview:
                self.claim_season_info_text.yview_moveto(prev_yview[0])
            return

        try:
            phase_info = self.season_manager.get_current_phase(season_id)
            phase = phase_info.get("phase", "unknown")
            phase_reason = phase_info.get("reason", "")
        except Exception as exc:
            phase = "unknown"
            phase_reason = f"Phase detection failed: {exc}"

        total_supply = int(season["total_supply"])
        remaining_supply = int(season["remaining_supply"])
        claimed_supply = max(total_supply - remaining_supply, 0)
        start_date = season["start_date"]
        end_date = season["end_date"]
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        lines: List[str] = [
            f"Season: id={season['id']} | {season['type']}#{season['season_number']} | active={season['is_active']} completed={season['is_completed']}",
            f"Supply: claimed={claimed_supply} / total={total_supply} | remaining={remaining_supply}",
            f"Current phase: {phase} | reason: {phase_reason}",
            f"Window: start={self._fmt_dt(start_date)} | end={self._fmt_dt(end_date)} | now={self._fmt_dt(now)}",
            f"- season_alive_for: {self._fmt_remaining(max((now - start_date).total_seconds(), 0.0))}",
            "",
        ]

        if season["type"] == "genesis":
            lines.extend(
                [
                    "Transition rules (Genesis):",
                    "- Claims are open in Scavenge while remaining_supply > 0.",
                    "- Transition to Transmission happens when remaining_supply reaches 0.",
                    "- No day-based windows are used.",
                ]
            )
        else:
            breach_end = start_date + timedelta(days=3)
            vault_end = start_date + timedelta(days=6)
            scavenge_end = start_date + timedelta(days=9)
            transmission_end = start_date + timedelta(days=10)
            breach_cap = int(total_supply * self.season_manager.BREACH_CAP_PERCENT)
            lines.extend(
                [
                    "Transition rules (Standard):",
                    f"- Breach: day 1-3, open for all, cap {breach_cap}/{total_supply} (20%).",
                    "- Vault: day 4-6 or earlier if Breach cap reached, Origins only.",
                    "- Scavenge: day 7-9, open for all.",
                    "- Transmission: day 10, claims closed.",
                    "",
                    "Timing checkpoints:",
                    f"- breach_end: {self._fmt_dt(breach_end)}",
                    f"- vault_end: {self._fmt_dt(vault_end)}",
                    f"- scavenge_end: {self._fmt_dt(scavenge_end)}",
                    f"- cycle_boundary(day10): {self._fmt_dt(transmission_end)}",
                    "",
                    "Phase timeline (UTC):",
                    f"- Breach:      {self._fmt_dt(start_date)}  ->  {self._fmt_dt(breach_end)}",
                    f"- Vault:       {self._fmt_dt(breach_end)}  ->  {self._fmt_dt(vault_end)}",
                    f"- Scavenge:    {self._fmt_dt(vault_end)}  ->  {self._fmt_dt(scavenge_end)}",
                    f"- Transmission:{self._fmt_dt(scavenge_end)}  ->  {self._fmt_dt(transmission_end)}",
                ]
            )

            if now < start_date:
                next_note = f"Next transition: season starts at {self._fmt_dt(start_date)}"
            elif now < breach_end and claimed_supply < breach_cap:
                next_note = "Next transition: to Vault at day 4 start (or earlier if Breach cap is reached)."
            elif now < breach_end and claimed_supply >= breach_cap:
                next_note = "Breach cap reached: should be in/entering Vault immediately."
            elif now < vault_end:
                next_note = f"Next transition: to Scavenge at {self._fmt_dt(vault_end)}"
            elif now < scavenge_end:
                next_note = f"Next transition: to Transmission at {self._fmt_dt(scavenge_end)}"
            elif now < transmission_end:
                next_note = f"Next transition: cycle rollover at {self._fmt_dt(transmission_end)}"
            else:
                next_note = "Cycle boundary passed: scheduler should have rotated/closed this standard season."
            lines.extend(["", next_note])

            # Explicit countdowns help when manually validating scenario windows.
            if now < breach_end:
                lines.append(f"- time_to_vault_window: {self._fmt_remaining((breach_end - now).total_seconds())}")
            if now < vault_end:
                lines.append(f"- time_to_scavenge_window: {self._fmt_remaining((vault_end - now).total_seconds())}")
            if now < scavenge_end:
                lines.append(f"- time_to_transmission_window: {self._fmt_remaining((scavenge_end - now).total_seconds())}")
            if now < transmission_end:
                lines.append(f"- time_to_cycle_rollover: {self._fmt_remaining((transmission_end - now).total_seconds())}")

        if self.auto_phase_var.get():
            lines.append(f"\nInsert mode: Auto phase ON -> claim phase_type will be '{self.claim_phase_var.get()}'.")
        else:
            lines.append(f"\nInsert mode: Manual phase -> current selected phase_type is '{self.claim_phase_var.get()}'.")

        wallet = self.claim_wallet_var.get().strip().lower()
        lines.extend(["", "Checklist before insert:"])
        if not wallet:
            lines.append("- wallet: not selected")
            lines.append("- verdict: choose wallet first")
        else:
            try:
                eligibility = self.season_manager.check_user_eligibility(wallet)
                stream = self._resolve_stream_for_season_id(eligibility, season_id)

                lines.append(f"- wallet: {wallet}")
                lines.append(f"- is_origin_wallet: {bool(eligibility.get('is_origin_wallet'))}")

                if stream:
                    already_claimed = bool(stream.get("already_claimed"))
                    eligible_now = bool(stream.get("eligible_now"))
                    is_claim_open = bool(stream.get("is_claim_open"))
                    requires_origin = bool(stream.get("requires_origin"))
                    ineligible_reason = stream.get("ineligible_reason")

                    lines.append(
                        f"- stream_phase: {stream.get('phase')} | is_claim_open={is_claim_open} | requires_origin={requires_origin}"
                    )
                    lines.append(f"- already_claimed_in_this_season: {already_claimed}")
                    lines.append(f"- eligible_now: {eligible_now}")
                    if ineligible_reason:
                        lines.append(f"- ineligible_reason: {ineligible_reason}")

                    if eligible_now:
                        lines.append("- verdict: can insert without logic mismatch")
                    else:
                        lines.append("- verdict: insert possible only as negative/override test")
                else:
                    lines.append("- stream_phase: season is not current active genesis/standard stream")
                    lines.append("- eligible_now: false (outside active stream)")
                    lines.append("- verdict: insert possible only as manual test data")
            except Exception as exc:
                lines.append(f"- eligibility_check_error: {exc}")
                lines.append("- verdict: eligibility unknown (manual review)")

        for idx, line in enumerate(lines):
            if line.startswith("- time_to_"):
                self.claim_season_info_text.insert("end", line, ("countdown_active",))
            elif line.startswith("- season_alive_for:"):
                self.claim_season_info_text.insert("end", line, ("season_age",))
            else:
                self.claim_season_info_text.insert("end", line)
            if idx < len(lines) - 1:
                self.claim_season_info_text.insert("end", "\n")
        if prev_yview:
            self.claim_season_info_text.yview_moveto(prev_yview[0])

    @staticmethod
    def _resolve_stream_for_season_id(eligibility: Dict[str, object], season_id: int) -> Optional[Dict[str, object]]:
        genesis = eligibility.get("genesis")
        standard = eligibility.get("standard")
        if isinstance(genesis, dict) and genesis.get("season_id") == season_id:
            return genesis
        if isinstance(standard, dict) and standard.get("season_id") == season_id:
            return standard
        return None

    def _check_eligibility(self) -> None:
        wallet = self.elig_wallet_var.get().strip().lower()
        if not wallet:
            messagebox.showwarning("Wallet required", "Enter wallet address first.")
            return
        try:
            result = self.season_manager.check_user_eligibility(wallet)
            self.eligibility_text.delete("1.0", "end")
            self.eligibility_text.insert("end", json.dumps(result, indent=2, default=str))
        except Exception as exc:
            self.eligibility_text.delete("1.0", "end")
            self.eligibility_text.insert("end", f"Error: {exc}\n\n{traceback.format_exc()}")

    def _insert_fake_claim(self) -> None:
        wallet = self.claim_wallet_var.get().strip().lower()
        season_label = self.claim_season_var.get().strip()
        season_id = self._get_selected_season_id(season_label)
        phase = self.claim_phase_var.get().strip()
        status = self.claim_status_var.get().strip()

        if not wallet or not season_id:
            messagebox.showwarning("Missing fields", "Select wallet and season.")
            return

        auto_phase_reason: Optional[str] = None
        if self.auto_phase_var.get():
            detected_phase, phase_error = self._derive_claim_phase_type(season_id)
            if detected_phase:
                phase = detected_phase
                self.claim_phase_var.set(detected_phase)
            else:
                auto_phase_reason = phase_error or "Could not derive phase from season window"
                if not self.force_insert_var.get():
                    proceed = messagebox.askyesno(
                        "Auto phase warning",
                        f"{auto_phase_reason}\n\nUse manual phase value '{phase}' and continue?",
                    )
                    if not proceed:
                        self._append_text(self.claims_output_text, f"Insert cancelled: {auto_phase_reason}")
                        return

        try:
            eligibility = self.season_manager.check_user_eligibility(wallet)
            stream = self._resolve_stream_for_season_id(eligibility, season_id)
            warning_reason: Optional[str] = None

            if stream:
                stream_is_origin = bool(stream.get("is_origin_wallet", eligibility.get("is_origin_wallet")))
                if not stream.get("eligible_now", False):
                    warning_reason = str(stream.get("ineligible_reason") or "Wallet not eligible for this season now")
                elif phase == "vault" and not stream_is_origin:
                    warning_reason = "Wallet is non-origin but phase='vault'"
            elif phase == "vault" and not bool(eligibility.get("is_origin_wallet")):
                warning_reason = "Wallet is non-origin but phase='vault'"

            if warning_reason and not self.force_insert_var.get():
                proceed = messagebox.askyesno(
                    "Eligibility warning",
                    f"{warning_reason}\n\nInsert claim anyway?",
                )
                if not proceed:
                    self._append_text(self.claims_output_text, f"Insert cancelled: {warning_reason}")
                    return
        except Exception as exc:
            if not self.force_insert_var.get():
                proceed = messagebox.askyesno(
                    "Eligibility check failed",
                    f"Could not run eligibility check: {exc}\n\nInsert claim anyway?",
                )
                if not proceed:
                    return

        tx_hash: Optional[str] = None
        if self.generate_tx_var.get():
            tx_hash = "0x" + secrets.token_hex(32)

        token_id: Optional[int] = None
        if self.claim_token_var.get().strip():
            try:
                token_id = int(self.claim_token_var.get().strip())
            except ValueError:
                messagebox.showwarning("Token ID", "Token ID must be an integer.")
                return

        supported_phases = set(self._get_phase_enum_values())
        if phase not in supported_phases:
            supported = ", ".join(sorted(supported_phases)) if supported_phases else "(none)"
            message = (
                f"DB enum phase_type does not support '{phase}'.\n"
                f"Supported now: {supported}\n\n"
                "Update DB enum to include breach/vault/scavenge."
            )
            messagebox.showerror("phase_type enum mismatch", message)
            self._append_text(self.claims_output_text, f"Insert blocked: {message}")
            return

        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO claims (
                        user_wallet,
                        season_id,
                        phase_type,
                        timestamp,
                        tx_hash,
                        token_id,
                        status,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, NOW(), %s, %s, %s, NOW(), NOW())
                    RETURNING id
                    """,
                    (wallet, season_id, phase, tx_hash, token_id, status),
                )
                new_id = cursor.fetchone()[0]
            conn.commit()
            msg = f"Inserted fake claim id={new_id} wallet={wallet} season_id={season_id} phase={phase} status={status}"
            if auto_phase_reason:
                msg += f" | note={auto_phase_reason}"
            self._append_text(self.claims_output_text, msg)
            self._refresh_all()
        except Exception as exc:
            conn.rollback()
            self._append_text(self.claims_output_text, f"Insert failed: {exc}")
        finally:
            conn.close()

    def _refresh_season_claims(self) -> None:
        selected_label = self.season_claims_season_var.get().strip()
        season_id = self._get_selected_season_id(selected_label)

        for item in self.season_claims_tree.get_children():
            self.season_claims_tree.delete(item)
        self.season_claims_summary_text.delete("1.0", "end")

        if not season_id:
            self.season_claims_summary_text.insert("end", "Select season to view claims.")
            return

        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, user_wallet, phase_type, status, tx_hash, token_id, timestamp, created_at
                    FROM claims
                    WHERE season_id = %s
                    ORDER BY created_at DESC, id DESC
                    """,
                    (season_id,),
                )
                rows = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS total_claims,
                        COUNT(*) FILTER (WHERE status = 'COMPLETED') AS completed_claims,
                        COUNT(*) FILTER (WHERE status = 'PENDING') AS pending_claims,
                        COUNT(*) FILTER (WHERE status = 'PROCESSING') AS processing_claims,
                        COUNT(*) FILTER (WHERE status = 'FAILED') AS failed_claims,
                        COUNT(*) FILTER (WHERE phase_type::text = 'breach') AS breach_claims,
                        COUNT(*) FILTER (WHERE phase_type::text = 'vault') AS vault_claims,
                        COUNT(*) FILTER (WHERE phase_type::text = 'scavenge') AS scavenge_claims,
                        COUNT(*) FILTER (WHERE phase_type::text = 'public') AS legacy_public_claims
                    FROM claims
                    WHERE season_id = %s
                    """,
                    (season_id,),
                )
                stats = cursor.fetchone()
        finally:
            conn.close()

        summary = (
            f"season_id={season_id} | total={stats['total_claims']} | completed={stats['completed_claims']} | "
            f"pending={stats['pending_claims']} | processing={stats['processing_claims']} | failed={stats['failed_claims']} | "
            f"breach={stats['breach_claims']} | vault={stats['vault_claims']} | "
            f"scavenge={stats['scavenge_claims']} | legacy_public={stats['legacy_public_claims']}"
        )
        self.season_claims_summary_text.insert("end", summary)

        for row in rows:
            self.season_claims_tree.insert(
                "",
                "end",
                values=(
                    row["id"],
                    row["user_wallet"],
                    row["phase_type"],
                    row["status"],
                    row["tx_hash"],
                    row["token_id"],
                    str(row["timestamp"]),
                    str(row["created_at"]),
                ),
            )

    def _run_season_lifecycle_update(self) -> None:
        try:
            self.scheduler.run_standard_season_update()
            self._append_text(self.logs_text, "\n[GUI] run_standard_season_update finished.\n")
            self._refresh_all()
        except Exception as exc:
            self._append_text(self.logs_text, f"\n[GUI] season update failed: {exc}\n")

    @staticmethod
    def _parse_iso_datetime_utc(value: str) -> datetime:
        raw = value.strip()
        if not raw:
            raise ValueError("Datetime value is empty")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt

    @staticmethod
    def _bool_text(value: bool) -> str:
        return "true" if value else "false"

    def _load_scenario_season_params(self, silent: bool = False) -> None:
        season_id = self._get_selected_season_id(self.scenario_season_var.get().strip())
        if not season_id:
            if not silent:
                messagebox.showwarning("Season required", "Select target season first.")
            return

        conn = self.manager.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT id, type, season_number, start_date, end_date, total_supply, remaining_supply, is_active, is_completed
                    FROM seasons
                    WHERE id = %s
                    """,
                    (season_id,),
                )
                row = cursor.fetchone()
        finally:
            conn.close()

        if not row:
            if not silent:
                messagebox.showerror("Not found", f"Season {season_id} not found.")
            return

        self.scenario_season_number_var.set(str(int(row["season_number"])))
        self.scenario_total_supply_var.set(str(int(row["total_supply"])))
        self.scenario_remaining_supply_var.set(str(int(row["remaining_supply"])))

        start_dt = row["start_date"]
        end_dt = row["end_date"]
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        self.scenario_start_date_var.set(start_dt.astimezone(timezone.utc).isoformat())
        self.scenario_end_date_var.set(end_dt.astimezone(timezone.utc).isoformat())
        self.scenario_is_active_var.set(self._bool_text(bool(row["is_active"])))
        self.scenario_is_completed_var.set(self._bool_text(bool(row["is_completed"])))

        if not silent:
            self._append_text(
                self.scenario_output_text,
                f"Loaded params for season {season_id}: {row['type']}#{row['season_number']}",
            )

    def _set_scenario_now_start(self) -> None:
        season_id, season_type = self._get_selected_season_meta()
        if not season_id:
            messagebox.showwarning("Season required", "Select target season first.")
            return

        now = datetime.now(timezone.utc)
        self.scenario_start_date_var.set(now.isoformat())
        if season_type == "standard":
            self.scenario_end_date_var.set((now + timedelta(days=10)).isoformat())
        self._append_text(self.scenario_output_text, f"Prepared new start/end for season {season_id}.")

    def _apply_scenario_params(self) -> None:
        season_id = self._get_selected_season_id(self.scenario_season_var.get().strip())
        if not season_id:
            messagebox.showwarning("Season required", "Select target season first.")
            return

        try:
            new_season_number = int(self.scenario_season_number_var.get().strip())
            new_total_supply = int(self.scenario_total_supply_var.get().strip())
            new_remaining_supply = int(self.scenario_remaining_supply_var.get().strip())
            new_start_date = self._parse_iso_datetime_utc(self.scenario_start_date_var.get())
            new_end_date = self._parse_iso_datetime_utc(self.scenario_end_date_var.get())
            new_is_active = self.scenario_is_active_var.get().strip().lower() == "true"
            new_is_completed = self.scenario_is_completed_var.get().strip().lower() == "true"
        except Exception as exc:
            messagebox.showwarning("Invalid input", str(exc))
            return

        if new_season_number <= 0:
            messagebox.showwarning("Invalid season_number", "season_number must be > 0.")
            return
        if new_total_supply <= 0:
            messagebox.showwarning("Invalid total_supply", "total_supply must be > 0.")
            return
        if new_remaining_supply < 0 or new_remaining_supply > new_total_supply:
            messagebox.showwarning("Invalid remaining_supply", "remaining_supply must be between 0 and total_supply.")
            return
        if new_end_date <= new_start_date:
            messagebox.showwarning("Invalid dates", "end_date must be later than start_date.")
            return

        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE seasons
                    SET
                        season_number = %s,
                        start_date = %s,
                        end_date = %s,
                        total_supply = %s,
                        remaining_supply = %s,
                        is_active = %s,
                        is_completed = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        new_season_number,
                        new_start_date,
                        new_end_date,
                        new_total_supply,
                        new_remaining_supply,
                        new_is_active,
                        new_is_completed,
                        season_id,
                    ),
                )
            conn.commit()
            self._append_text(
                self.scenario_output_text,
                (
                    f"Applied advanced params for season {season_id}: "
                    f"number={new_season_number}, supply={new_remaining_supply}/{new_total_supply}, "
                    f"active={new_is_active}, completed={new_is_completed}"
                ),
            )
            self._refresh_all()
        except Exception as exc:
            conn.rollback()
            self._append_text(self.scenario_output_text, f"Advanced update failed: {exc}")
        finally:
            conn.close()

    def _set_standard_phase_from_now(self, days_since_start: int) -> None:
        season_id, season_type = self._get_selected_season_meta()
        if not season_id:
            messagebox.showwarning("Season required", "Select target season first.")
            return
        if season_type != "standard":
            messagebox.showwarning("Standard only", "Quick phase buttons are only for standard season.")
            return
        now = datetime.now(timezone.utc)
        new_start = now - timedelta(days=days_since_start)
        new_end = new_start + timedelta(days=10)
        self._update_season_dates(season_id, new_start, new_end)
        self._append_text(
            self.scenario_output_text,
            f"Updated season {season_id} start_date={new_start.isoformat()} end_date={new_end.isoformat()}",
        )
        self._refresh_all()

    def _apply_manual_date_shift(self) -> None:
        season_id, _ = self._get_selected_season_meta()
        if not season_id:
            messagebox.showwarning("Season required", "Select target season first.")
            return
        try:
            shift_days = int(self.shift_days_var.get().strip())
        except ValueError:
            messagebox.showwarning("Invalid value", "Shift days must be an integer.")
            return

        original_dates = self._get_season_dates(season_id)
        if original_dates is None:
            messagebox.showerror("Season missing", "Could not load season dates.")
            return
        original_start, original_end = original_dates
        duration = original_end - original_start
        now = datetime.now(timezone.utc)
        start_date = now + timedelta(days=shift_days)
        end_date = start_date + duration
        self._update_season_dates(season_id, start_date, end_date)
        self._append_text(
            self.scenario_output_text,
            f"Applied manual date shift for season {season_id}: start={start_date.isoformat()} end={end_date.isoformat()}",
        )
        self._refresh_all()

    def _apply_remaining_supply(self) -> None:
        season_id = self._get_selected_season_id(self.scenario_season_var.get().strip())
        if not season_id:
            messagebox.showwarning("Season required", "Select target season first.")
            return
        try:
            new_remaining = int(self.remaining_supply_var.get().strip())
        except ValueError:
            messagebox.showwarning("Invalid value", "remaining_supply must be integer.")
            return

        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE seasons
                    SET
                        remaining_supply = %s,
                        is_active = CASE WHEN %s > 0 THEN is_active ELSE FALSE END,
                        is_completed = CASE WHEN %s > 0 THEN is_completed ELSE TRUE END,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (new_remaining, new_remaining, new_remaining, season_id),
                )
            conn.commit()
            self._append_text(
                self.scenario_output_text,
                f"Updated season {season_id} remaining_supply={new_remaining}",
            )
            self._refresh_all()
        except Exception as exc:
            conn.rollback()
            self._append_text(self.scenario_output_text, f"Supply update failed: {exc}")
        finally:
            conn.close()

    def _run_reset_sql(self) -> None:
        if not self.confirm_reset_var.get():
            messagebox.showwarning("Confirmation required", "Enable confirmation checkbox before reset.")
            return
        sql_path = Path(__file__).resolve().parents[1] / "sql" / "queries" / "clear_seasons_logic.sql"
        if not sql_path.exists():
            self._append_text(self.reset_output_text, f"Reset SQL not found: {sql_path}")
            return

        sql = sql_path.read_text(encoding="utf-8")
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql)
            conn.commit()
            self._append_text(self.reset_output_text, "Reset SQL executed successfully.")
            self._refresh_all()
        except Exception as exc:
            conn.rollback()
            self._append_text(self.reset_output_text, f"Reset failed: {exc}")
        finally:
            conn.close()

    # ------------------------------
    # Low-level DB helpers
    # ------------------------------
    def _get_selected_season_meta(self) -> tuple[Optional[int], Optional[str]]:
        season_id = self._get_selected_season_id(self.scenario_season_var.get().strip())
        if not season_id:
            return None, None
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT type FROM seasons WHERE id = %s", (season_id,))
                row = cursor.fetchone()
                return season_id, row[0] if row else None
        finally:
            conn.close()

    def _get_season_dates(self, season_id: int) -> Optional[tuple[datetime, datetime]]:
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT start_date, end_date FROM seasons WHERE id = %s", (season_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                return row[0], row[1]
        finally:
            conn.close()

    def _update_season_dates(self, season_id: int, start_date: datetime, end_date: datetime) -> None:
        conn = self.manager.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE seasons
                    SET start_date = %s, end_date = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (start_date, end_date, season_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _append_text(widget: tk.Text, text: str) -> None:
        widget.insert("end", text + "\n")
        widget.see("end")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = SeasonTestWorkbench()
    app.run()


if __name__ == "__main__":
    main()
