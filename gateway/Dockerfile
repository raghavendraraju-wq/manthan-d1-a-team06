# Mock payment gateway. Build from the repo root: docker build -f gateway/Dockerfile .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GATEWAY_DB=/data/gateway.db
WORKDIR /srv

COPY gateway/requirements.txt gateway/requirements.txt
RUN pip install --no-cache-dir -r gateway/requirements.txt

COPY gateway/ gateway/
RUN useradd --system --uid 10001 gateway && mkdir /data && chown gateway /data
USER gateway
VOLUME /data
EXPOSE 8081

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8081/healthz', timeout=2)"

CMD ["uvicorn", "gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8081"]
