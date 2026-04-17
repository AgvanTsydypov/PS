"""
SVG → PNG rasterization via a pooled headless Chromium (Playwright).

Why a dedicated worker thread:
  * Playwright's sync API refuses to start if an asyncio event loop exists
    in the current thread. FastAPI endpoints run on the main async thread,
    so we delegate all rasterization to a daemon thread that owns the
    browser for the lifetime of the process.
  * Starting Chromium costs ~500ms–1s; reusing one browser + page across
    mints keeps per-card rasterization at ~100–250 ms.
"""

from __future__ import annotations

import queue
import threading
from typing import Optional, Tuple

_DEFAULT_TIMEOUT_SECONDS = 60.0

# (svg_body, width, height, reply_queue)
_REQUEST_QUEUE: "queue.Queue[Optional[Tuple[str, int, int, queue.Queue]]]" = queue.Queue()
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()
_WORKER_READY = threading.Event()


def _wrap_svg_html(svg_body: str, width: int, height: int) -> str:
    """Wrap the raw SVG in a minimal HTML document sized to the target dims.

    Forcing width/height via CSS ensures the SVG fills the viewport exactly,
    regardless of whether the SVG itself declares width/height (ours does not —
    only viewBox).
    """
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"/>"
        "<style>"
        "html,body{margin:0;padding:0;background:transparent;}"
        f"svg{{display:block;width:{width}px;height:{height}px;}}"
        "</style></head><body>"
        f"{svg_body}"
        "</body></html>"
    )


def _worker_loop() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        # Drain the queue by replying with errors so callers don't hang.
        err = f"playwright not installed: {exc}"
        _WORKER_READY.set()
        while True:
            item = _REQUEST_QUEUE.get()
            if item is None:
                return
            _, _, _, reply_q = item
            reply_q.put(("error", err))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1024, "height": 1024},
            device_scale_factor=1.0,
        )
        page = context.new_page()
        _WORKER_READY.set()
        try:
            while True:
                item = _REQUEST_QUEUE.get()
                if item is None:
                    return
                svg_body, width, height, reply_q = item
                try:
                    page.set_viewport_size({"width": int(width), "height": int(height)})
                    html = _wrap_svg_html(svg_body, int(width), int(height))
                    page.set_content(html, wait_until="load")
                    # Ensure embedded @font-face (data-URI) has finished loading.
                    page.evaluate("() => document.fonts.ready")
                    png_bytes = page.screenshot(
                        type="png",
                        omit_background=False,
                        full_page=False,
                        clip={"x": 0, "y": 0, "width": int(width), "height": int(height)},
                    )
                    reply_q.put(("ok", png_bytes))
                except Exception as exc:
                    reply_q.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass


def _ensure_worker() -> None:
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        thread = threading.Thread(target=_worker_loop, name="svg-rasterizer", daemon=True)
        thread.start()
        _WORKER_STARTED = True
    # Wait for the worker to reach its request loop (browser booted) so the
    # first caller isn't racing with Chromium startup. Cold start can take
    # 20-40s on Windows the very first time Chromium is launched in a process.
    _WORKER_READY.wait(timeout=90.0)


def svg_to_png(
    svg_body: str,
    *,
    width: int,
    height: int,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> bytes:
    """Rasterize an SVG document to PNG bytes at the requested pixel size.

    Blocks until the dedicated worker thread returns the PNG. Safe to call
    from sync or async code (under async, invoke via ``asyncio.to_thread``
    if you need to avoid blocking the event loop).
    """
    if not svg_body:
        raise ValueError("svg_to_png: svg_body is empty")
    if width <= 0 or height <= 0:
        raise ValueError(f"svg_to_png: invalid size {width}x{height}")

    _ensure_worker()
    reply_q: "queue.Queue[Tuple[str, object]]" = queue.Queue(maxsize=1)
    _REQUEST_QUEUE.put((svg_body, int(width), int(height), reply_q))
    status, payload = reply_q.get(timeout=timeout_seconds)
    if status != "ok":
        raise RuntimeError(f"SVG rasterization failed: {payload}")
    assert isinstance(payload, (bytes, bytearray))
    return bytes(payload)
