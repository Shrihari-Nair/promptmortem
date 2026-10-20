from contextlib import asynccontextmanager

import secrets as _secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api import analysis, exports, metrics, optimize, plans, projects, runs, scenarios
from app.config import settings
from app.core.db import engine, init_db
from app.core.secrets import configured_keys
from app.pipeline.runner import recover_interrupted


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    recover_interrupted(engine)  # runs left 'running' by a crash/restart become 'paused' (explicit resume)
    yield


app = FastAPI(title="Prompt Change Validator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
app.include_router(projects.router)
app.include_router(analysis.router)
app.include_router(scenarios.router)
app.include_router(metrics.router)
app.include_router(plans.router)
app.include_router(runs.router)
app.include_router(exports.router)
app.include_router(optimize.router)


@app.middleware("http")
async def require_token(request: Request, call_next):
    """Optional single-user protection: when PV_API_TOKEN is set every call (except /health and CORS preflights) needs it as
    `Authorization: Bearer <token>`, `X-API-Token`, or `?token=` (EventSource and download links cannot set headers)."""
    want = settings.api_token
    path = request.url.path
    root = request.scope.get("root_path", "")  # mounted under /api by app.web
    if root and path.startswith(root):
        path = path[len(root):]
    if want and request.method != "OPTIONS" and path != "/health":
        h = request.headers.get("authorization", "")
        got = (h[7:] if h.lower().startswith("bearer ") else "") or request.headers.get("x-api-token", "") or request.query_params.get("token", "")
        if not _secrets.compare_digest(got.encode(), want.encode()):
            return JSONResponse({"detail": "missing or invalid API token"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)


@app.get("/health")
def health():
    """Setup check: which provider keys are configured (booleans only), the default models, whether a token is required."""
    return {"ok": True, "auth_required": bool(settings.api_token), "keys": configured_keys(),
            "models": {"target": settings.target_model, "judge": settings.judge_model}, "embedding_model": settings.embedding_model}
