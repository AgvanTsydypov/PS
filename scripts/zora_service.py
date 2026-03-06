"""
Zora/Base mint helpers.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from scripts.solana_service import MintedNftResult


class ZoraClient:
    """Thin Python wrapper over Node-based Zora SDK mint script."""

    def __init__(
        self,
        project_root: str | Path,
        node_script_path: str | Path = "scripts/mint_zora_nft.mjs",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        script_path = Path(node_script_path)
        if not script_path.is_absolute():
            script_path = self.project_root / script_path
        self.node_script_path = script_path.resolve()

    def mint_user_nft(
        self,
        user_wallet_address: str,
        pnl_value: float,
        rank: int,
        season_name: str,
        claim_id: int | None = None,
        winner_context: dict[str, Any] | None = None,
    ) -> MintedNftResult:
        resolved_claim_id = claim_id if claim_id is not None else 0
        payload = {
            "user_wallet_address": user_wallet_address,
            "pnl_value": pnl_value,
            "rank": rank,
            "season_name": season_name,
            "claim_id": resolved_claim_id,
            "winner_context": winner_context or {},
        }

        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        ) as temp_payload:
            temp_payload.write(json.dumps(payload, ensure_ascii=False))
            payload_path = Path(temp_payload.name)

        try:
            command = [
                "node",
                str(self.node_script_path),
                "--payload-file",
                str(payload_path),
            ]
            process = subprocess.run(
                command,
                cwd=str(self.project_root),
                check=False,
                capture_output=True,
                text=True,
            )
            if process.returncode != 0:
                error_text = process.stderr.strip() or process.stdout.strip()
                raise RuntimeError(
                    f"Zora mint script failed (exit={process.returncode}): {error_text}"
                )

            stdout = process.stdout.strip()
            if not stdout:
                raise RuntimeError("Zora mint script returned empty output.")

            result = json.loads(stdout)
            required_fields = [
                "asset_address",
                "tx_hash",
                "nft_name",
                "metadata_uri",
                "explorer_tx_url",
                "explorer_asset_url",
            ]
            missing = [field for field in required_fields if not result.get(field)]
            if missing:
                raise RuntimeError(f"Zora mint result missing fields: {', '.join(missing)}")

            return MintedNftResult(
                claim_id=resolved_claim_id,
                asset_address=str(result["asset_address"]),
                tx_hash=str(result["tx_hash"]),
                nft_name=str(result["nft_name"]),
                metadata_uri=str(result["metadata_uri"]),
                explorer_tx_url=str(result["explorer_tx_url"]),
                explorer_asset_url=str(result["explorer_asset_url"]),
            )
        finally:
            try:
                payload_path.unlink(missing_ok=True)
            except Exception:
                pass

