from pathlib import Path
import subprocess
import threading
from datetime import datetime

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles

from app import app
from paper_engine import open_positions

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DASHBOARD = BASE_DIR / "unified_dashboard.html"
CLASSIC_INDEX = ROOT_DIR / "index.html"
UPDATE_SCRIPT = BASE_DIR / "remote_update.cmd"
UPDATE_LAUNCHER = BASE_DIR / "remote_update.vbs"
UI_VERSION = "0.9.1"
_UPDATE = {"running": False, "requestedAt": None, "lastError": None}
_UPDATE_LOCK = threading.Lock()


def _remote_allowed(request: Request):
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1") or host.startswith("100.")


async def unified_mobile(request):
    return FileResponse(DASHBOARD, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


async def classic_daytrader(request):
    return FileResponse(CLASSIC_INDEX, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


async def root_styles(request):
    return FileResponse(ROOT_DIR / "styles.css", media_type="text/css", headers={"Cache-Control": "no-cache"})


async def root_manifest(request):
    return FileResponse(ROOT_DIR / "manifest.webmanifest", media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


async def root_sw(request):
    return FileResponse(ROOT_DIR / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


async def unified_root(request):
    return RedirectResponse(url="/classic", status_code=307)


async def update_status(request: Request):
    return {"ok": True, "uiVersion": UI_VERSION, **dict(_UPDATE)}


async def update_run(request: Request):
    if not _remote_allowed(request):
        raise HTTPException(status_code=403, detail="Update is allowed only from localhost or Tailscale.")
    if open_positions():
        raise HTTPException(status_code=409, detail="열린 Paper 포지션이 있어 업데이트를 차단했습니다.")
    if _UPDATE.get("running"):
        raise HTTPException(status_code=409, detail="업데이트가 이미 진행 중입니다.")
    if not UPDATE_SCRIPT.exists() or not UPDATE_LAUNCHER.exists():
        raise HTTPException(status_code=500, detail="원격 업데이트 실행 파일이 없습니다.")
    with _UPDATE_LOCK:
        _UPDATE.update({"running": True, "requestedAt": datetime.now().isoformat(), "lastError": None})
    try:
        # Use Windows Script Host as a truly detached hidden launcher. This avoids
        # fragile CREATE_* flag combinations and lets the updater kill/restart uvicorn safely.
        subprocess.Popen(
            ["wscript.exe", str(UPDATE_LAUNCHER)],
            cwd=str(ROOT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=False,
        )
        return {"ok": True, "accepted": True, "message": "업데이트를 시작했습니다. 서버 재시작 후 자동 재연결됩니다."}
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        _UPDATE.update({"running": False, "lastError": msg})
        raise HTTPException(status_code=500, detail=f"업데이트 실행 실패: {msg}")


app.router.routes.insert(0, Route("/api/system/update/run", update_run, methods=["POST"]))
app.router.routes.insert(0, Route("/api/system/update/status", update_status, methods=["GET"]))
app.router.routes.insert(0, Mount("/js", app=StaticFiles(directory=str(ROOT_DIR / "js")), name="classic-js"))
if (ROOT_DIR / "icons").exists():
    app.router.routes.insert(0, Mount("/icons", app=StaticFiles(directory=str(ROOT_DIR / "icons")), name="classic-icons"))
app.router.routes.insert(0, Route("/styles.css", root_styles, methods=["GET"]))
app.router.routes.insert(0, Route("/manifest.webmanifest", root_manifest, methods=["GET"]))
app.router.routes.insert(0, Route("/sw.js", root_sw, methods=["GET"]))
app.router.routes.insert(0, Route("/classic", classic_daytrader, methods=["GET"]))
app.router.routes.insert(0, Route("/classic/", classic_daytrader, methods=["GET"]))
app.router.routes.insert(0, Route("/dashboard", unified_mobile, methods=["GET"]))
app.router.routes.insert(0, Route("/mobile", unified_mobile, methods=["GET"]))
app.router.routes.insert(0, Route("/", unified_root, methods=["GET"]))
