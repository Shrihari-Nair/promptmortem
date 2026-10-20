# Prompt Mortem: backend + frontend in ONE image, ONE port.
#   docker compose up --build        then open http://localhost:8000
# The Gemini (or other provider) key is NEVER baked into the image: it is read at run time from your git-ignored .env.

# ---- 1. build the frontend ---------------------------------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2. the app: Python API that also serves the built frontend ---------------------------------------------------
FROM python:3.12-slim AS app
# EMBEDDINGS=1 adds sentence-transformers (better scenario clustering, about 2 GB more because of PyTorch). Default: lightweight fallback.
ARG EMBEDDINGS=0
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HOME=/home/app \
    PV_DB_URL=sqlite:////data/workspace.db \
    PV_STATIC_DIR=/app/frontend/dist \
    HF_HOME=/data/hf
# (embeddings: the app uses sentence-transformers when it is installed (EMBEDDINGS=1) and falls back to a lightweight method otherwise)

RUN pip install --no-cache-dir uv && useradd --create-home --uid 1000 app && mkdir /data && chown app:app /data

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project $( [ "$EMBEDDINGS" = "1" ] && echo "--extra embeddings" )
COPY backend/app ./app
COPY examples /app/examples
COPY scripts /app/scripts
COPY --from=web /web/dist /app/frontend/dist

USER app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=4 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"
CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000"]
