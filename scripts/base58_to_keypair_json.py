"""
Convert a Phantom Base58 private key string into Solana keypair JSON.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58_MAP = {char: i for i, char in enumerate(BASE58_ALPHABET)}


def b58decode(value: str) -> bytes:
    if not value:
        raise ValueError("Base58 string is empty")

    number = 0
    for char in value:
        if char not in BASE58_MAP:
            raise ValueError(f"Invalid Base58 character: {char!r}")
        number = number * 58 + BASE58_MAP[char]

    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big")
    leading_zeros = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeros + decoded


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert Base58 private key string to Solana keypair JSON."
    )
    parser.add_argument("private_key_base58", help="Phantom private key in Base58 format")
    parser.add_argument(
        "-o",
        "--output",
        default="my-keypair.json",
        help="Output JSON file path (default: my-keypair.json)",
    )
    args = parser.parse_args()

    key_bytes = b58decode(args.private_key_base58.strip())
    if len(key_bytes) not in (32, 64):
        raise ValueError(
            f"Unexpected key length: {len(key_bytes)} bytes. Expected 32 or 64 bytes."
        )

    output_path = Path(args.output)
    output_path.write_text(json.dumps(list(key_bytes)), encoding="utf-8")
    print(f"Saved {len(key_bytes)} bytes to {output_path}")


if __name__ == "__main__":
    main()
