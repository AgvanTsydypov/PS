"""
Unified card asset pipeline: SVG -> PNG -> (R2 or Pinata).

Single source of truth for how PolyStars cards are turned into shippable image
bytes and where those bytes are published. Every caller (NFT mint payload,
admin showcase simulator, user-facing ``/api/cards/get``) goes through the
helpers in this module, so the on-chain / on-IPFS / on-R2 formats, filenames
and content types stay aligned.

Flow:
    render_payload -> (generate_card_svg, generate_card_back_svg)
                   -> svg_to_png (Playwright/Chromium)
                   -> upload_card_assets_to_r2     (showcase, .png in R2)
                   -> upload_card_assets_to_pinata (NFT mint, .png on IPFS)

Showcase (admin simulator, ``/api/cards/get``) writes to R2 so pages can serve
cheap public PNGs. NFT mint writes to Pinata because the minted metadata points
at IPFS. In both cases the on-disk/on-wire format is exactly the same PNG,
rasterized from the same SVG; only the destination differs.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, Tuple

import httpx

from scripts.cardgen.generate_card import generate_card_back_svg, generate_card_svg
from scripts.cardgen.rasterize import svg_to_png

try:
    import boto3
    from botocore.config import Config
except Exception:  # pragma: no cover - optional dependency on scripts-only hosts
    boto3 = None
    Config = None

logger = logging.getLogger(__name__)

# ── Rasterization size ──────────────────────────────────────────────────────
# Source SVG is 516x802 (viewBox). Default 1.5x yields 774x1203, a good balance
# of sharpness vs file size for marketplaces and wallet previews. Override with
# CARD_PNG_SCALE env var (e.g. 2.0 for 1032x1604).
try:
    CARD_PNG_SCALE = float(os.getenv("CARD_PNG_SCALE", "1.5"))
except ValueError:
    CARD_PNG_SCALE = 1.5
CARD_SVG_WIDTH = 516
CARD_SVG_HEIGHT = 802
CARD_PNG_WIDTH = int(round(CARD_SVG_WIDTH * CARD_PNG_SCALE))
CARD_PNG_HEIGHT = int(round(CARD_SVG_HEIGHT * CARD_PNG_SCALE))

CARD_PNG_MIME = "image/png"
CARD_PNG_CACHE_CONTROL = "public, max-age=31536000, immutable"

# ── Pinata (NFT mint destination) ───────────────────────────────────────────
PINATA_JWT_ENV_KEY = "PINATA_JWT"
PINATA_FILE_API_URL = "https://api.pinata.cloud/pinning/pinFileToIPFS"
PINATA_UNPIN_API_URL = "https://api.pinata.cloud/pinning/unpin"
PINATA_GATEWAY_PREFIX = "https://gateway.pinata.cloud/ipfs/"


# ─────────────────────────────────────────────────────────────────────────────
# SVG + PNG generation
# ─────────────────────────────────────────────────────────────────────────────

def render_card_svgs(render_payload: Dict[str, Any]) -> Tuple[str, str]:
    """Generate front+back SVG strings from a render payload."""
    front_svg = generate_card_svg(render_payload)
    back_svg = generate_card_back_svg(render_payload)
    return front_svg, back_svg


def rasterize_card_svgs(
    front_svg: str,
    back_svg: str,
    *,
    width: int = CARD_PNG_WIDTH,
    height: int = CARD_PNG_HEIGHT,
) -> Tuple[bytes, bytes]:
    """Rasterize front+back SVG -> PNG bytes via the shared headless browser pool.

    Front and back are rasterized in parallel: each ``svg_to_png`` call grabs
    a free worker from the shared Playwright pool, so on a pool with 2+
    workers this cuts per-card latency roughly in half vs the previous
    sequential implementation. On a 1-worker pool the two calls are simply
    serialized by the queue, so this fan-out never makes things slower.

    Width/height default to ``CARD_PNG_WIDTH``/``CARD_PNG_HEIGHT``. Callers can
    override for special cases (e.g. larger off-screen preview) but should
    generally rely on the shared constants so every card has the same pixel
    dimensions regardless of who rendered it.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_front = pool.submit(svg_to_png, front_svg, width=width, height=height)
        f_back = pool.submit(svg_to_png, back_svg, width=width, height=height)
        return f_front.result(), f_back.result()


def render_card_pngs(render_payload: Dict[str, Any]) -> Tuple[bytes, bytes]:
    """Convenience: render_card_svgs + rasterize_card_svgs in one call."""
    front_svg, back_svg = render_card_svgs(render_payload)
    return rasterize_card_svgs(front_svg, back_svg)


# ─────────────────────────────────────────────────────────────────────────────
# Slug / key helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_slug(slug: str) -> str:
    """Filesystem/URL-safe slug; mirrors historical behaviour."""
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(slug or "").strip()) or "card"


def generated_card_r2_key(slug: str, side: str) -> str:
    """Canonical R2 object key for a showcase card image.

    Keys always end in ``.png`` — the showcase pipeline writes rasterized PNGs,
    not SVGs. Prefixed with ``R2_PREFIX`` (e.g. ``dev``, ``prod``) when set so
    multiple environments can share a bucket.
    """
    prefix = str(os.getenv("R2_PREFIX", "dev")).strip().strip("/")
    safe_side = "front" if side == "front" else "back"
    key = f"cards-images/{_safe_slug(slug)}/{safe_side}.png"
    return f"{prefix}/{key}" if prefix else key


def extract_r2_key_from_public_url(public_base_url: str, url: Optional[str]) -> Optional[str]:
    """Invert ``{public_base_url}/{key}`` to recover the R2 object key."""
    if not url:
        return None
    base = (public_base_url or "").rstrip("/")
    value = str(url).strip()
    if not base or not value.startswith(base + "/"):
        return None
    return value[len(base) + 1:]


# ─────────────────────────────────────────────────────────────────────────────
# R2 (showcase destination)
# ─────────────────────────────────────────────────────────────────────────────

_R2_CLIENT: Any = None


def _r2_required_env() -> Dict[str, str]:
    endpoint = str(os.getenv("R2_ENDPOINT", "")).strip()
    bucket = str(os.getenv("R2_BUCKET", "")).strip()
    access_key = str(os.getenv("R2_ACCESS_KEY_ID", "")).strip()
    secret_key = str(os.getenv("R2_SECRET_ACCESS_KEY", "")).strip()
    public_base_url = str(os.getenv("R2_PUBLIC_BASE_URL", "")).strip().rstrip("/")
    missing = [
        name
        for name, value in (
            ("R2_ENDPOINT", endpoint),
            ("R2_BUCKET", bucket),
            ("R2_ACCESS_KEY_ID", access_key),
            ("R2_SECRET_ACCESS_KEY", secret_key),
            ("R2_PUBLIC_BASE_URL", public_base_url),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"R2 env vars missing: {', '.join(missing)}")
    return {
        "endpoint": endpoint,
        "bucket": bucket,
        "access_key": access_key,
        "secret_key": secret_key,
        "public_base_url": public_base_url,
    }


def r2_public_base_url() -> str:
    """Return the configured R2 public base URL (no trailing slash)."""
    return _r2_required_env()["public_base_url"]


def _get_r2_client() -> Any:
    global _R2_CLIENT
    if boto3 is None or Config is None:
        raise ValueError("R2 upload dependencies are missing. Install boto3 and botocore.")
    if _R2_CLIENT is not None:
        return _R2_CLIENT
    cfg = _r2_required_env()
    _R2_CLIENT = boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )
    return _R2_CLIENT


def upload_card_assets_to_r2(
    slug: str,
    front_png: bytes,
    back_png: bytes,
) -> Tuple[str, str, str, str]:
    """Upload front/back PNGs under the canonical ``cards-images/<slug>/<side>.png`` keys.

    Returns ``(front_url, back_url, front_key, back_key)``. Uploads run in
    parallel; both must succeed or the caller's ``try/except`` should fall
    back and call :func:`delete_r2_object_by_key` on whichever key(s) landed.
    """
    cfg = _r2_required_env()
    front_key = generated_card_r2_key(slug, "front")
    back_key = generated_card_r2_key(slug, "back")
    client = _get_r2_client()

    put_kwargs: Dict[str, Any] = {
        "Bucket": cfg["bucket"],
        "ContentType": CARD_PNG_MIME,
        "CacheControl": CARD_PNG_CACHE_CONTROL,
    }

    def _put(key: str, body: bytes) -> None:
        client.put_object(Key=key, Body=body, **put_kwargs)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_front = pool.submit(_put, front_key, front_png)
        f_back = pool.submit(_put, back_key, back_png)
        f_front.result()
        f_back.result()

    return (
        f"{cfg['public_base_url']}/{front_key}",
        f"{cfg['public_base_url']}/{back_key}",
        front_key,
        back_key,
    )


def delete_r2_object_by_key(key: Optional[str]) -> None:
    """Best-effort delete of a single R2 object; never raises."""
    if not key:
        return
    try:
        cfg = _r2_required_env()
        _get_r2_client().delete_object(Bucket=cfg["bucket"], Key=key)
    except Exception:
        logger.warning("Could not delete generated card asset from R2 key=%s", key, exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Pinata (NFT mint destination)
# ─────────────────────────────────────────────────────────────────────────────

def _pinata_jwt() -> str:
    jwt = str(os.getenv(PINATA_JWT_ENV_KEY, "")).strip()
    if not jwt:
        raise ValueError(f"{PINATA_JWT_ENV_KEY} is required")
    return jwt


def _pin_bytes_to_pinata(
    filename: str, body: bytes, content_type: str, *, ext: str = "bin",
) -> str:
    """Pin one byte blob to Pinata and mirror the same bytes to R2.

    The R2 mirror lives under ``ipfs-backup/<cid>.<ext>`` and is hard-required
    (failure aborts the call). The whole point of the mirror is disaster
    recovery if Pinata ever drops these pins — silently skipping it on a bad
    day would leave us thinking we're protected when we're not.

    The Pinata pin is configured with ``cidVersion: 1`` (CIDv1, ``bafy…``)
    for subdomain-gateway compatibility and ``wrapWithDirectory: false`` so
    the CID is a pure function of ``body``. That same byte-equivalence is
    what lets the R2 mirror restore an identical CID at any standards-
    compliant IPFS service.
    """
    jwt = _pinata_jwt()
    response = httpx.post(
        PINATA_FILE_API_URL,
        headers={"Authorization": f"Bearer {jwt}"},
        data={
            "pinataMetadata": json.dumps({"name": filename}),
            # ``wrapWithDirectory: false`` is also Pinata's current default,
            # but we pin it explicitly so a future default change can't
            # silently turn our file CIDs into directory CIDs (which would
            # depend on filename and break the R2 mirror).
            "pinataOptions": json.dumps(
                {"cidVersion": 1, "wrapWithDirectory": False}
            ),
        },
        files={"file": (filename, body, content_type)},
        timeout=40.0,
    )
    response.raise_for_status()
    payload = response.json()
    ipfs_hash = payload.get("IpfsHash")
    if not ipfs_hash:
        raise RuntimeError(f"Pinata file upload missing IpfsHash: {payload}")

    try:
        backup_ipfs_bytes_to_r2(ipfs_hash, body, content_type, ext)
    except Exception as exc:
        # Roll back the Pinata pin so the caller is back to a clean state —
        # otherwise a half-mirror leaks a pin that no longer matches our
        # disaster-recovery contract.
        _unpin_pinata_cid_best_effort(ipfs_hash)
        raise RuntimeError(
            f"R2 mirror of pinned IPFS content failed (cid={ipfs_hash}): {exc}"
        ) from exc

    return f"{PINATA_GATEWAY_PREFIX}{ipfs_hash}"


def upload_card_assets_to_pinata(
    slug: str,
    front_png: bytes,
    back_png: bytes,
) -> Tuple[str, str]:
    """Pin front/back PNGs to Pinata in parallel. Returns ``(front_url, back_url)``."""
    safe = _safe_slug(slug)
    front_name = f"{safe}-front.png"
    back_name = f"{safe}-back.png"
    with ThreadPoolExecutor(max_workers=2) as pool:
        front_f = pool.submit(
            _pin_bytes_to_pinata, front_name, front_png, CARD_PNG_MIME, ext="png",
        )
        back_f = pool.submit(
            _pin_bytes_to_pinata, back_name, back_png, CARD_PNG_MIME, ext="png",
        )
        return front_f.result(), back_f.result()


def _pinata_cid_from_url(url: Any) -> str:
    """Extract the bare CID from a card-asset URL in either supported form:
    ``ipfs://<CID>`` (current) or ``https://<gateway>/ipfs/<CID>`` (legacy).
    Returns ``""`` for anything that doesn't look like pinned IPFS content.
    Any trailing ``/path`` is dropped — card PNGs are pinned as bare CIDs.
    """
    s = str(url or "").strip()
    if not s:
        return ""
    if s.startswith("ipfs://"):
        rest = s[len("ipfs://"):]
        if rest.lower().startswith("ipfs/"):
            rest = rest[len("ipfs/"):]
        rest = rest.lstrip("/")
    else:
        marker = "/ipfs/"
        idx = s.find(marker)
        if idx == -1:
            return ""
        rest = s[idx + len(marker):].lstrip("/")
    return rest.split("/", 1)[0].split("?", 1)[0]


def unpin_pinata_urls(urls) -> None:
    """Best-effort unpin a list of Pinata card-asset URLs (``ipfs://<CID>`` or
    legacy gateway URLs) and remove the matching R2 mirror copies.
    Silently ignores all errors."""
    jwt = os.environ.get(PINATA_JWT_ENV_KEY, "").strip()
    for url in urls or ():
        cid = _pinata_cid_from_url(url)
        if not cid:
            continue
        if jwt:
            try:
                httpx.delete(
                    f"{PINATA_UNPIN_API_URL}/{cid}",
                    headers={"Authorization": f"Bearer {jwt}"},
                    timeout=10.0,
                )
            except Exception:
                pass
        # All callers of this helper deal with PNG card uploads. JSON metadata
        # mirror cleanup lives next to its own unpin in evm_service.
        delete_ipfs_backup_for_cid(cid, "png")


def _unpin_pinata_cid_best_effort(cid: str) -> None:
    """Internal helper used by the upload path to roll back a Pinata pin
    when the follow-on R2 mirror write fails. Silent on failure — the caller
    is already raising and the pin will eventually fall out of scope."""
    if not cid:
        return
    jwt = os.environ.get(PINATA_JWT_ENV_KEY, "").strip()
    if not jwt:
        return
    try:
        httpx.delete(
            f"{PINATA_UNPIN_API_URL}/{cid}",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=10.0,
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# R2 disaster-recovery mirror for pinned IPFS content
# ─────────────────────────────────────────────────────────────────────────────
# Mirrors every byte we pin to Pinata into R2 under ``ipfs-backup/<cid>.<ext>``.
# If Pinata ever drops these pins, re-uploading the mirror bytes to any
# standards-compliant IPFS service produces the same CID — so the on-chain
# ``tokenURI = ipfs://<cid>`` keeps resolving without contract changes.
#
# Hard-required by design: failed mirror writes abort the pin (and roll back
# the Pinata pin via ``_unpin_pinata_cid_best_effort``). The whole point of
# this layer is to be the safety net when Pinata fails — silently degrading
# to "no mirror" defeats the purpose.

IPFS_BACKUP_R2_KEY_PREFIX = "ipfs-backup"


def _ipfs_backup_r2_key(cid: str, ext: str) -> str:
    """Canonical R2 object key for an IPFS-mirror copy.

    Lives outside the showcase ``cards-images/`` namespace so disaster-recovery
    tooling can list every backup with one prefix scan without colliding with
    the (sometimes pruned) showcase objects.
    """
    safe_cid = "".join(ch for ch in str(cid or "").strip() if ch.isalnum())
    if not safe_cid:
        raise ValueError("ipfs backup key requires a non-empty CID")
    safe_ext = (ext or "bin").strip().lstrip(".").lower() or "bin"
    prefix = str(os.getenv("R2_PREFIX", "dev")).strip().strip("/")
    key = f"{IPFS_BACKUP_R2_KEY_PREFIX}/{safe_cid}.{safe_ext}"
    return f"{prefix}/{key}" if prefix else key


def ipfs_backup_public_url(cid: str, ext: str = "png") -> str:
    """Public R2 URL of the disaster-recovery mirror for a pinned IPFS CID.

    Every byte pinned to Pinata is also mirrored to R2 under
    ``{R2_PREFIX}/ipfs-backup/<cid>.<ext>`` (see :func:`backup_ipfs_bytes_to_r2`),
    so card images can be served from R2 — which is CDN-fronted and has no
    per-IP rate limit — instead of hot-linking ``gateway.pinata.cloud`` (which
    429s under load). Raises ``ValueError`` if R2 env is not configured so the
    caller can fall back to the public Pinata gateway.
    """
    return f"{r2_public_base_url()}/{_ipfs_backup_r2_key(cid, ext)}"


def backup_ipfs_bytes_to_r2(
    cid: str, body: bytes, content_type: str, ext: str,
) -> str:
    """Mirror pinned IPFS bytes to R2 keyed by CID. Returns the R2 object key.
    Raises on any failure — the caller MUST treat a failed mirror as a failed
    pin (and undo the Pinata pin), otherwise we silently lose DR coverage."""
    cfg = _r2_required_env()
    key = _ipfs_backup_r2_key(cid, ext)
    client = _get_r2_client()
    client.put_object(
        Bucket=cfg["bucket"],
        Key=key,
        Body=body,
        ContentType=content_type or "application/octet-stream",
        CacheControl=CARD_PNG_CACHE_CONTROL,
    )
    return key


def delete_ipfs_backup_for_cid(cid: str, ext: str) -> None:
    """Best-effort delete of an IPFS-mirror copy. Used in cleanup paths that
    mirror the Pinata unpin (e.g. mint failure before broadcast). Never raises
    — a leftover mirror object is a tiny storage leak, not a correctness bug.
    """
    if not cid:
        return
    try:
        cfg = _r2_required_env()
        key = _ipfs_backup_r2_key(cid, ext)
        _get_r2_client().delete_object(Bucket=cfg["bucket"], Key=key)
    except Exception:
        logger.warning(
            "Could not delete ipfs mirror for cid=%s ext=%s",
            cid, ext, exc_info=True,
        )
