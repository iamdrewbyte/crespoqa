# CrespoQA web app. The AI model runs in its own container (see docker-compose.yml).
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

# LibreOffice computes the contents page numbers; the metric-compatible fonts make its page breaks match Word's.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libreoffice-writer poppler-utils fontconfig \
        fonts-liberation fonts-crosextra-carlito ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY deploy/fonts.conf /etc/fonts/local.conf
RUN fc-cache -f

RUN useradd --system --uid 10001 --create-home --home-dir /home/crespoqa crespoqa \
 && mkdir -p /data && chown crespoqa:crespoqa /data
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY crespoqa ./crespoqa

ENV CRESPOQA_HOST=0.0.0.0 \
    CRESPOQA_PORT=8000 \
    CRESPOQA_DB=/data/crespoqa.db \
    CRESPOQA_TEMP_DIR=/data/tmp \
    CRESPOQA_LOG_DIR=/data/logs

USER crespoqa
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"
CMD ["python", "-m", "crespoqa.api.app"]
