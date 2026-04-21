"""
SVG → PNG rasterization via a pool of headless Chromium workers (Playwright).

Architecture:
  * Each worker thread owns its own ``sync_playwright()`` instance, Browser
    and Page. Playwright's sync API refuses to start if an asyncio loop
    exists in the current thread, so we keep every browser off the FastAPI
    event loop and off each other.
  * All workers pull from a single shared request queue. ``svg_to_png``
    submits work and blocks on a per-request reply queue, so it is safe
    to call from any thread (sync or async-via-to_thread).
  * Pool size defaults to 4 (override with ``PLAYWRIGHT_WORKERS``). A
    pool of N renders up to N cards in parallel, which matters for batch
    simulators that used to be O(N) × per-card latency.

Cold start cost:
  * Launching a Chromium is ~500 ms – 3 s on warm disks (up to ~20-40 s the
    very first time Chromium has ever been spawned on the machine).
  * Call :func:`warmup` at process startup if you want the first request to
    skip that cost.
"""

from __future__ import annotations

import atexit
import logging
import os
import queue
import threading
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 60.0

# Default 2 workers: a balance between batch throughput and RAM (each
# Chromium costs ~200-300 MB resident). Bump via ``PLAYWRIGHT_WORKERS`` on
# hosts with more cores/RAM; drop to 1 for memory-constrained dev machines.
try:
    _DEFAULT_WORKER_COUNT = max(1, int(os.getenv("PLAYWRIGHT_WORKERS", "2")))
except ValueError:
    _DEFAULT_WORKER_COUNT = 2

# (svg_body, width, height, reply_queue); None is a shutdown sentinel.
_REQUEST_QUEUE: "queue.Queue[Optional[Tuple[str, int, int, queue.Queue]]]" = queue.Queue()

_POOL_LOCK = threading.Lock()
_POOL_STARTED = False
_ALL_WORKERS_READY = threading.Event()
_READY_COUNT_LOCK = threading.Lock()
_READY_COUNT = 0
_WORKER_COUNT = _DEFAULT_WORKER_COUNT
_WORKER_THREADS: List[threading.Thread] = []


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


def _mark_worker_ready() -> None:
    global _READY_COUNT
    with _READY_COUNT_LOCK:
        _READY_COUNT += 1
        if _READY_COUNT >= _WORKER_COUNT:
            _ALL_WORKERS_READY.set()


_BROWSER_CLOSED_MARKERS = (
    "target page, context or browser has been closed",
    "target closed",
    "browser has been closed",
    "browser closed",
    "connection closed",
    "browser context has been closed",
)


def _is_browser_closed_error(exc: BaseException) -> bool:
    """Return True if the exception indicates the Chromium page/context died.

    These errors leave the worker's ``page`` unusable: every subsequent call
    (``set_viewport_size``, ``set_content``, …) will re-raise the same
    ``TargetClosedError``. The worker must relaunch Chromium before it can
    process more work, otherwise it silently fails every request routed to
    it and the batch (e.g. the showcase simulator) stalls at zero progress.
    """
    name = type(exc).__name__
    if name in ("TargetClosedError", "BrowserClosedError"):
        return True
    msg = str(exc).lower()
    return any(marker in msg for marker in _BROWSER_CLOSED_MARKERS)


def _worker_loop(worker_index: int) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        # Drain the queue by replying with errors so callers don't hang.
        err = f"playwright not installed: {exc}"
        _mark_worker_ready()
        while True:
            item = _REQUEST_QUEUE.get()
            if item is None:
                return
            _, _, _, reply_q = item
            reply_q.put(("error", err))

    def _launch(p):
        # Extra args (e.g. --no-sandbox on Linux prod servers that run
        # Chromium without a user-namespace sandbox).
        extra_args = [
            a.strip()
            for a in os.getenv("PLAYWRIGHT_CHROMIUM_ARGS", "").split(",")
            if a.strip()
        ]
        if os.getenv("PLAYWRIGHT_NO_SANDBOX", "").strip().lower() in ("1", "true", "yes"):
            if "--no-sandbox" not in extra_args:
                extra_args.append("--no-sandbox")
        browser = p.chromium.launch(headless=True, args=extra_args or None)
        context = browser.new_context(
            viewport={"width": 1024, "height": 1024},
            device_scale_factor=1.0,
        )
        page = context.new_page()
        return browser, context, page

    def _close_quietly(browser, context) -> None:
        for closer in (context, browser):
            if closer is None:
                continue
            try:
                closer.close()
            except Exception:
                pass

    try:
        with sync_playwright() as p:
            browser = context = page = None
            try:
                browser, context, page = _launch(p)
                _mark_worker_ready()
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
                        err_msg = f"{type(exc).__name__}: {exc}"
                        reply_q.put(("error", err_msg))
                        # If the page/browser is dead, every subsequent request
                        # routed to this worker will fail with the same
                        # TargetClosedError. Relaunch Chromium in-place so the
                        # worker can keep serving. Common after uvicorn
                        # ``--reload`` tears down the previous process and the
                        # new Chromium instance exits right after startup.
                        if _is_browser_closed_error(exc):
                            logger.warning(
                                "rasterizer worker %d: browser closed mid-request (%s); relaunching",
                                worker_index,
                                err_msg,
                            )
                            _close_quietly(browser, context)
                            browser = context = page = None
                            try:
                                browser, context, page = _launch(p)
                            except Exception:
                                logger.exception(
                                    "rasterizer worker %d: failed to relaunch Chromium; exiting",
                                    worker_index,
                                )
                                return
            finally:
                _close_quietly(browser, context)
    except Exception:
        # Worker died mid-startup; mark ready anyway so the main caller doesn't
        # block forever waiting for a browser that will never come, and log.
        logger.exception("rasterizer worker %d crashed", worker_index)
        _mark_worker_ready()


def _ensure_pool() -> None:
    global _POOL_STARTED
    with _POOL_LOCK:
        if _POOL_STARTED:
            return
        for i in range(_WORKER_COUNT):
            thread = threading.Thread(
                target=_worker_loop,
                args=(i,),
                name=f"svg-rasterizer-{i}",
                daemon=True,
            )
            thread.start()
            _WORKER_THREADS.append(thread)
        _POOL_STARTED = True
        atexit.register(shutdown)
    # Wait for the pool to reach steady state (all browsers booted) so the
    # first caller isn't racing with Chromium startup. Cold start can take
    # 20-40s on Windows the very first time Chromium is ever launched.
    _ALL_WORKERS_READY.wait(timeout=90.0)


def warmup() -> None:
    """Spawn the worker pool eagerly, so the first real request is instant.

    Safe to call multiple times; subsequent calls are no-ops. Typical usage
    is from a FastAPI ``startup`` event on processes that will rasterize
    cards (admin_backend, user_web_backend).
    """
    _ensure_pool()


def shutdown(timeout_seconds: float = 5.0) -> None:
    """Stop every worker and close its Chromium cleanly.

    CRITICAL on dev servers running under ``uvicorn --reload``: worker
    threads are daemons, so a plain process exit would skip their
    ``browser.close()`` and leave zombie Chromium subprocesses behind.
    On Windows a dozen reloads can pile up 50+ orphaned ``chrome-headless-shell``
    processes, consume several GB of RAM, and wedge the machine until it
    swaps. We send one sentinel per worker and join briefly so each worker
    exits its ``sync_playwright()`` context and shuts down its browser.

    Registered via ``atexit`` the first time the pool starts, and callable
    directly from a FastAPI ``@app.on_event("shutdown")`` hook for belt-
    and-suspenders cleanup. Idempotent.
    """
    global _POOL_STARTED
    with _POOL_LOCK:
        if not _POOL_STARTED:
            return
        threads = list(_WORKER_THREADS)
        _WORKER_THREADS.clear()
        _POOL_STARTED = False
    for _ in threads:
        _REQUEST_QUEUE.put(None)
    for thread in threads:
        thread.join(timeout=timeout_seconds)


_MAX_RENDER_ATTEMPTS = max(1, int(os.getenv("PLAYWRIGHT_MAX_ATTEMPTS", "3")))


def _is_retryable_render_error(message: object) -> bool:
    """Transient failures worth another trip through the pool.

    Mostly ``TargetClosedError`` from a worker whose Chromium has just died.
    That worker relaunches itself in-place, so a fresh submission almost
    always lands on a healthy worker (or the same one after it recovered).
    """
    text = str(message).lower()
    if "targetclosederror" in text:
        return True
    return any(marker in text for marker in _BROWSER_CLOSED_MARKERS)


def svg_to_png(
    svg_body: str,
    *,
    width: int,
    height: int,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> bytes:
    """Rasterize an SVG document to PNG bytes at the requested pixel size.

    Blocks until a worker in the pool returns the PNG. Safe to call from
    multiple threads concurrently: each call gets its own reply queue and
    pulls a free worker from the shared pool. Up to ``PLAYWRIGHT_WORKERS``
    rasterizations run in parallel.

    On transient browser-closed errors (common right after ``uvicorn --reload``
    leaves a worker with a dead Chromium) the call is re-submitted up to
    ``PLAYWRIGHT_MAX_ATTEMPTS`` times; the offending worker relaunches its
    browser in parallel, so the retry usually succeeds immediately.
    """
    if not svg_body:
        raise ValueError("svg_to_png: svg_body is empty")
    if width <= 0 or height <= 0:
        raise ValueError(f"svg_to_png: invalid size {width}x{height}")

    _ensure_pool()
    last_error: object = "unknown"
    for attempt in range(1, _MAX_RENDER_ATTEMPTS + 1):
        reply_q: "queue.Queue[Tuple[str, object]]" = queue.Queue(maxsize=1)
        _REQUEST_QUEUE.put((svg_body, int(width), int(height), reply_q))
        try:
            status, payload = reply_q.get(timeout=timeout_seconds)
        except queue.Empty:
            raise RuntimeError(
                f"SVG rasterization timed out after {timeout_seconds:.0f}s — "
                "Playwright worker is dead or overloaded. "
                "Check server logs for Chromium launch errors. "
                "On Linux set PLAYWRIGHT_NO_SANDBOX=1 if running without user-namespace sandbox."
            )
        if status == "ok":
            assert isinstance(payload, (bytes, bytearray))
            return bytes(payload)
        last_error = payload
        if attempt >= _MAX_RENDER_ATTEMPTS or not _is_retryable_render_error(payload):
            break
        logger.warning(
            "svg_to_png attempt %d/%d hit a dead worker (%s); retrying",
            attempt,
            _MAX_RENDER_ATTEMPTS,
            payload,
        )
    raise RuntimeError(f"SVG rasterization failed: {last_error}")
