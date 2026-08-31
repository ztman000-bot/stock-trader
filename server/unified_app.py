from pathlib import Path

from fastapi.responses import FileResponse, RedirectResponse
from starlette.routing import Route

from app import app

BASE_DIR = Path(__file__).resolve().parent
DASHBOARD = BASE_DIR / "unified_dashboard.html"


async def unified_mobile(request):
    return FileResponse(DASHBOARD, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


async def unified_root(request):
    return RedirectResponse(url="/mobile", status_code=307)


# Put the unified UI routes before the legacy /mobile route while keeping the
# original FastAPI application's lifespan, NH bridge, collector and Paper loop.
app.router.routes.insert(0, Route("/dashboard", unified_mobile, methods=["GET"]))
app.router.routes.insert(0, Route("/mobile", unified_mobile, methods=["GET"]))
app.router.routes.insert(0, Route("/", unified_root, methods=["GET"]))
