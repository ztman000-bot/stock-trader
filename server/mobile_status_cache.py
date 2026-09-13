"""Read-only stale-while-revalidate cache for the Android dashboard status.

This module is presentation/transport only. It never calls an order endpoint and
never mutates Paper/Control state. The underlying /api/mobile/status payload is
still built by server/app.py; this layer only reuses the last successful payload
so opening the native dashboard does not synchronously repeat a heavy scan.
"""
from __future__ import annotations

import os
import threading
import time

import anyio
from fastapi.responses import JSONResponse

TTL_SEC = max(2.0, float(os.getenv('MOBILE_STATUS_SERVER_CACHE_TTL_SEC', '8')))
WARMUP_DELAY_SEC = max(0.5, float(os.getenv('MOBILE_STATUS_SERVER_CACHE_WARMUP_SEC', '3')))
_ENABLED = (
    os.getenv('TEMP_PHONE_SERVER', '').lower() == 'true'
    or os.getenv('MOBILE_STATUS_SERVER_CACHE_ENABLED', 'false').lower() == 'true'
)

_LOCK = threading.RLock()
_STATE = {
    'payload': None,
    'builtAt': 0.0,
    'building': False,
    'lastError': None,
}


def _build_payload():
    # Lazy import avoids changing app.py import order or Control semantics.
    import app as bridge
    return bridge._mobile_payload()


def _store(payload):
    with _LOCK:
        _STATE['payload'] = payload
        _STATE['builtAt'] = time.monotonic()
        _STATE['lastError'] = None


def _refresh_worker():
    try:
        _store(_build_payload())
    except Exception as exc:
        with _LOCK:
            _STATE['lastError'] = f'{type(exc).__name__}: {exc}'[:500]
    finally:
        with _LOCK:
            _STATE['building'] = False


def request_refresh():
    if not _ENABLED:
        return False
    with _LOCK:
        if _STATE['building']:
            return False
        _STATE['building'] = True
    threading.Thread(target=_refresh_worker, name='mobile-status-cache-refresh', daemon=True).start()
    return True


def _view(payload, built_at, building):
    age = max(0.0, time.monotonic() - float(built_at or 0))
    out = dict(payload or {})
    out['mobileCache'] = {
        'serverCache': True,
        'ageSec': round(age, 3),
        'stale': age > TTL_SEC,
        'refreshing': bool(building),
        'ttlSec': TTL_SEC,
        'controlMutation': False,
        'realOrderEnabled': False,
    }
    return out


def _snapshot():
    with _LOCK:
        return _STATE['payload'], float(_STATE['builtAt'] or 0), bool(_STATE['building'])


def _delayed_warmup():
    time.sleep(WARMUP_DELAY_SEC)
    request_refresh()


def install(fastapi_app):
    """Install one Android-only cache middleware on an existing FastAPI app."""
    if not _ENABLED or getattr(fastapi_app.state, 'mobile_status_cache_installed', False):
        return False
    fastapi_app.state.mobile_status_cache_installed = True

    @fastapi_app.middleware('http')
    async def mobile_status_cache_middleware(request, call_next):
        path = request.url.path
        is_status = request.method == 'GET' and path == '/api/mobile/status'
        if is_status:
            payload, built_at, building = _snapshot()
            if payload is not None:
                age = max(0.0, time.monotonic() - built_at)
                if age > TTL_SEC:
                    request_refresh()
                    _, _, building = _snapshot()
                # Return immediately even when stale; the metadata keeps the UI
                # visibly in SYNCING state until the next fresh poll arrives.
                return JSONResponse(_view(payload, built_at, building))

            # First boot only: wait for one build in a worker thread rather than
            # blocking the event loop. If it fails, fall back to the original route.
            try:
                fresh = await anyio.to_thread.run_sync(_build_payload)
                _store(fresh)
                payload, built_at, building = _snapshot()
                return JSONResponse(_view(payload, built_at, building))
            except Exception:
                return await call_next(request)

        response = await call_next(request)
        if request.method == 'GET' and path in ('/', '/classic', '/dashboard'):
            payload, built_at, _ = _snapshot()
            if payload is None or time.monotonic() - built_at > TTL_SEC:
                request_refresh()
        return response

    threading.Thread(target=_delayed_warmup, name='mobile-status-cache-warmup', daemon=True).start()
    return True
