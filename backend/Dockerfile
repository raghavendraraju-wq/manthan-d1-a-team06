# API. Build from the repo root: docker build -f backend/Dockerfile .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UPLOAD_DIR=/data/uploads
WORKDIR /srv

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY returnright/ returnright/
RUN useradd --system --uid 10001 api && mkdir -p /data/uploads && chown -R api /data
USER api
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=120s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=2)"

# Migrate, seed (skips itself when data exists), then serve.
CMD ["sh", "-c", "python -m backend.migrate && python -m backend.seed && exec uvicorn backend.main:create_app --factory --host 0.0.0.0 --port 8000"]
