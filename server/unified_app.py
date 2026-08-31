from pathlib import Path
import os
import subprocess
import threading
from datetime import datetime

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.routing import Route

from app import app
from paper_engine import open_positions

BASE_DIR = Path(__file__).resolve().parent
DASHBOARD = BASE_DIR / "unified_dashboard.html"
UPDATE_SCRIPT = BASE_DIR / "remote_update.cmd"
UI_VERSION = "0.8.1"
_UPDATE = {"running": False, "requestedAt": None, "lastError": None}
_UPDATE_LOCK = threading.Lock()


def _remote_allowed(request: Request):
    host = (request.client.host if request.client else "") or ""
    # Localhost plus Tailscale CGNAT range only. Normal LAN/public clients cannot trigger updates.
    return host in ("127.0.0.1", "::1") or host.startswith("100.")


async def unified_mobile(request):
    return FileResponse(DASHBOARD, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


async def unified_root(request):
    return RedirectResponse(url="/mobile", status_code=307)


async def update_status(request: Request):
    return {"ok": True, "uiVersion": UI_VERSION, **dict(_UPDATE)}


async def update_run(request: Request):
    if not _remote_allowed(request):
        raise HTTPException(status_code=403, detail="Update is allowed only from localhost or Tailscale.")
    if open_positions():
        raise HTTPException(status_code=409, detail="열린 Paper 포지션이 있어 업데이트를 차단했습니다.")
    if _UPDATE.get("running"):
        raise HTTPException(status_code=409, detail="업데이트가 이미 진행 중입니다.")
    if not UPDATE_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="remote_update.cmd가 없습니다.")
    with _UPDATE_LOCK:
        _UPDATE.update({"running": True, "requestedAt": datetime.now().isoformat(), "lastError": None})
    try:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(["cmd.exe", "/c", str(UPDATE_SCRIPT)], cwd=str(BASE_DIR.parent), creationflags=flags, close_fds=True)
        return {"ok": True, "accepted": True, "message": "업데이트를 시작했습니다. 서버가 재시작되면 화면이 자동 재연결됩니다."}
    except Exception as exc:
        _UPDATE.update({"running": False, "lastError": f"{type(exc).__name__}: {exc}"})
        raise HTTPException(status_code=500, detail="업데이트 프로세스를 시작하지 못했습니다.")


# Unified UI and management routes are inserted before legacy routes while the
# original app keeps ownership of NH data, collector, Paper loop and lifespan.
app.router.routes.insert(0, Route("/api/system/update/run", update_run, methods=["POST"]))
app.router.routes.insert(0, Route("/api/system/update/status", update_status, methods=["GET"]))
app.router.routes.insert(0, Route("/dashboard", unified_mobile, methods=["GET"]))
app.router.routes.insert(0, Route("/mobile", unified_mobile, methods=["GET"]))
app.router.routes.insert(0, Route("/", unified_root, methods=["GET"]))
