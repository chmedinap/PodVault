FROM python:3.11-slim

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir . \
    && mkdir -p /app/data

# Paths under /app/data are meant to be volume-mounted (see docker-compose.yml)
# so the database, downloads, and feeds.txt survive container restarts/rebuilds.
ENV PODVAULT_DB_PATH=/app/data/podvault_data.db \
    PODVAULT_DOWNLOADS_FOLDER=/app/data/downloads \
    PODVAULT_FEEDS_FILE=/app/data/feeds.txt

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

CMD ["podvault", "serve", "--host", "0.0.0.0", "--port", "8000"]
