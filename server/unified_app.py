from pathlib import Path
import os
import sqlite3
import subprocess
import threading
import time
from datetime import datetime

from fastapi import Request
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

from app import app, run_backfill
from collector import DB_PATH, collector

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DASHBOARD = BASE_DIR / "unified_dashboard.html"
CLASSIC_INDEX = ROOT_DIR / "index.html"
UPDATE_SCRIPT = BASE_DIR / "remote_update.cmd"
UPDATE_LAUNCHER = BASE_DIR / "remote_update.vbs"
UI_VERSION = "0.10.0"
_UPDATE = {"running": False, "requestedAt": None, "lastError": None}
_UPDATE_LOCK = threading.Lock()
_WARMUP = {"running": False, "done": False, "lastError": None}


def _remote_allowed(request: Request):
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1") or host.startswith("100.")


def _has_open_positions():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=2)
        try:
            row = conn.execute("SELECT 1 FROM paper_trades WHERE status='OPEN' LIMIT 1").fetchone()
            return bool(row), None
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower(): return False, None
        return None, f"Paper DB 확인 실패: {exc}"
    except Exception as exc:
        return None, f"Paper DB 확인 실패: {type(exc).__name__}: {exc}"


def _launch_update_after_response():
    time.sleep(2.0)
    try:
        subprocess.Popen(["wscript.exe", str(UPDATE_LAUNCHER)], cwd=str(ROOT_DIR), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=False)
    except Exception as exc:
        with _UPDATE_LOCK: _UPDATE.update({"running": False, "lastError": f"{type(exc).__name__}: {exc}"})


def _warm_candidate_universe():
    # start_stock_trader_background.cmd uses AUTO_BACKFILL=false for fast UI boot.
    # Warm all 20 candidates here in two API-safe batches so Top10 is usable immediately.
    time.sleep(3.0)
    _WARMUP.update({"running": True, "done": False, "lastError": None})
    try:
        codes = list(collector.watchlist)
        for i in range(0, len(codes), 10):
            run_backfill(codes[i:i + 10])
            time.sleep(0.8)
        _WARMUP["done"] = True
    except Exception as exc:
        _WARMUP["lastError"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        _WARMUP["running"] = False


async def unified_mobile(request): return FileResponse(DASHBOARD, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})
async def classic_daytrader(request): return FileResponse(CLASSIC_INDEX, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})
async def root_styles(request): return FileResponse(ROOT_DIR / "styles.css", media_type="text/css", headers={"Cache-Control": "no-cache"})
async def root_manifest(request): return FileResponse(ROOT_DIR / "manifest.webmanifest", media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})
async def root_sw(request): return FileResponse(ROOT_DIR / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})
async def unified_root(request): return RedirectResponse(url="/classic", status_code=307)
async def update_status(request: Request): return JSONResponse({"ok": True, "uiVersion": UI_VERSION, "warmup": dict(_WARMUP), **dict(_UPDATE)})


async def update_run(request: Request):
    if not _remote_allowed(request): return JSONResponse({"ok": False, "detail": "업데이트는 localhost 또는 Tailscale 접속에서만 허용됩니다."}, status_code=403)
    has_open, db_error = _has_open_positions()
    if db_error: return JSONResponse({"ok": False, "detail": db_error + " · 안전을 위해 업데이트를 보류합니다."}, status_code=409)
    if has_open: return JSONResponse({"ok": False, "detail": "열린 Paper 포지션이 있어 업데이트를 차단했습니다."}, status_code=409)
    if _UPDATE.get("running"): return JSONResponse({"ok": False, "detail": "업데이트가 이미 진행 중입니다."}, status_code=409)
    if not UPDATE_SCRIPT.exists() or not UPDATE_LAUNCHER.exists(): return JSONResponse({"ok": False, "detail": "원격 업데이트 파일이 없습니다. 노트북에서 통합 업데이트를 한 번 실행하세요."}, status_code=409)
    try:
        with _UPDATE_LOCK: _UPDATE.update({"running": True, "requestedAt": datetime.now().isoformat(), "lastError": None})
        threading.Thread(target=_launch_update_after_response, daemon=True, name="remote-update-launcher").start()
    except Exception as exc:
        msg=f"{type(exc).__name__}: {exc}"
        with _UPDATE_LOCK: _UPDATE.update({"running": False, "lastError": msg})
        return JSONResponse({"ok": False, "detail": "업데이트 예약 실패: " + msg}, status_code=409)
    return JSONResponse({"ok": True,"accepted": True,"uiVersion": UI_VERSION,"message": "업데이트 요청 접수 완료. 약 2초 후 서버 재시작을 시작합니다."})


app.router.routes.insert(0, Route("/api/system/update/run", update_run, methods=["POST"]))
app.router.routes.insert(0, Route("/api/system/update/status", update_status, methods=["GET"]))
app.router.routes.insert(0, Mount("/js", app=StaticFiles(directory=str(ROOT_DIR / "js")), name="classic-js"))
if (ROOT_DIR / "icons").exists(): app.router.routes.insert(0, Mount("/icons", app=StaticFiles(directory=str(ROOT_DIR / "icons")), name="classic-icons"))
app.router.routes.insert(0, Route("/styles.css", root_styles, methods=["GET"]))
app.router.routes.insert(0, Route("/manifest.webmanifest", root_manifest, methods=["GET"]))
app.router.routes.insert(0, Route("/sw.js", root_sw, methods=["GET"]))
app.router.routes.insert(0, Route("/classic", classic_daytrader, methods=["GET"]))
app.router.routes.insert(0, Route("/classic/", classic_daytrader, methods=["GET"]))
app.router.routes.insert(0, Route("/dashboard", unified_mobile, methods=["GET"]))
app.router.routes.insert(0, Route("/mobile", unified_mobile, methods=["GET"]))
app.router.routes.insert(0, Route("/", unified_root, methods=["GET"]))

if os.getenv("AUTO_BACKFILL", "true").lower() == "false":
    threading.Thread(target=_warm_candidate_universe, daemon=True, name="candidate-universe-warmup").start()
