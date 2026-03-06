"""
Upload collection metadata JSON to IPFS via Pinata.

Usage:
  python scripts/upload_collection_metadata.py \
    --metadata-file data/metadata/master_collection.devnet.json \
    --image-url "https://.../image.png" \
    --external-url "https://your-site.com" \
    --update-env

Requires:
  PINATA_JWT in environment or .env
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


PINATA_API_URL = "https://api.pinata.cloud/pinning/pinJSONToIPFS"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METADATA_FILE = PROJECT_ROOT / "data/metadata/master_collection.devnet.json"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
ENV_METADATA_URI_KEY = "MASTER_COLLECTION_METADATA_URI"


def _upsert_env_value(env_path: Path, key: str, value: str) -> None:
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    next_line = f"{key}={value}"
    replaced = False
    output = []
    for line in lines:
        if line.startswith(f"{key}="):
            output.append(next_line)
            replaced = True
        else:
            output.append(line)
    if not replaced:
        if output and output[-1] != "":
            output.append("")
        output.append(next_line)

    env_path.write_text("\n".join(output) + "\n", encoding="utf-8")


def _load_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Metadata file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Metadata JSON must be an object.")
    return data


def _save_metadata(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _upload_to_pinata(metadata: dict[str, Any], jwt: str) -> tuple[str, str]:
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
    }
    payload = {
        "pinataContent": metadata,
        "pinataMetadata": {"name": f"{metadata.get('name', 'collection')}.json"},
    }
    response = requests.post(PINATA_API_URL, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    body = response.json()
    ipfs_hash = body.get("IpfsHash")
    if not ipfs_hash:
        raise RuntimeError(f"Pinata response missing IpfsHash: {body}")
    ipfs_uri = f"ipfs://{ipfs_hash}"
    https_uri = f"https://gateway.pinata.cloud/ipfs/{ipfs_hash}"
    return ipfs_uri, https_uri


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Upload collection metadata JSON to IPFS.")
    parser.add_argument(
        "--metadata-file",
        default=str(DEFAULT_METADATA_FILE),
        help="Path to metadata JSON file.",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help="Path to .env file.",
    )
    parser.add_argument(
        "--image-url",
        default="",
        help="Optional image URL to write into metadata before upload.",
    )
    parser.add_argument(
        "--external-url",
        default="",
        help="Optional external_url to write into metadata before upload.",
    )
    parser.add_argument(
        "--update-file",
        action="store_true",
        help="Persist updated fields to metadata file before upload.",
    )
    parser.add_argument(
        "--update-env",
        action="store_true",
        help=f"Save resulting HTTPS URI to .env as {ENV_METADATA_URI_KEY}.",
    )
    args = parser.parse_args()

    jwt = os.getenv("PINATA_JWT", "").strip()
    if not jwt:
        raise RuntimeError("PINATA_JWT is missing. Add it to .env or shell environment.")

    metadata_path = Path(args.metadata_file).resolve()
    env_path = Path(args.env_file).resolve()
    metadata = _load_metadata(metadata_path)

    if args.image_url:
        metadata["image"] = args.image_url.strip()
    if args.external_url:
        metadata["external_url"] = args.external_url.strip()
    if args.update_file:
        _save_metadata(metadata_path, metadata)

    ipfs_uri, https_uri = _upload_to_pinata(metadata, jwt)

    print(f"IPFS URI: {ipfs_uri}")
    print(f"HTTPS URI: {https_uri}")

    if args.update_env:
        _upsert_env_value(env_path, ENV_METADATA_URI_KEY, https_uri)
        print(f"Saved to .env: {ENV_METADATA_URI_KEY}={https_uri}")


if __name__ == "__main__":
    main()
