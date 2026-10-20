"""One process, one port: the API under /api and the built frontend at /. This is the Docker entry point
(`uvicorn app.web:app`); `app.main:app` stays the plain API (tests, `npm run dev` with the Vite proxy)."""
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from app.config import settings
from app.main import app as api


class SPA(StaticFiles):
    """Static files with the single-page-app fallback: unknown paths get index.html, missing assets stay 404."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code == 404 and not path.startswith("assets/"):
                return await super().get_response("index.html", scope)
            raise


@asynccontextmanager
async def lifespan(_):
    async with api.router.lifespan_context(api):  # a mounted app's own lifespan is not run by Starlette: init the DB, recover runs
        yield


def build() -> Starlette:
    dist = Path(settings.static_dir) if settings.static_dir else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (dist / "index.html").exists():
        front = Mount("/", app=SPA(directory=dist, html=True), name="web")
    else:
        front = Route("/", lambda r: PlainTextResponse("The frontend is not built. Run `npm run build` in frontend/ (or use the Docker image).", status_code=503))
    return Starlette(routes=[Mount("/api", app=api), front], lifespan=lifespan)


app = build()
