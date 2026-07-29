FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_DATA_DIR=/data LIBRARY_DB_PATH=/data/library.db

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && python -m playwright install --with-deps chromium
COPY . /app
# Adapter/source configuration is user-owned runtime data.  Do not bake it
# into the image: otherwise a deleted source can be silently restored when a
# fresh or empty data volume is started.
RUN mkdir -p /data /home/ubuntu/Config/Jellyfin/data/data

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod 0755 /app/docker-entrypoint.sh

EXPOSE 8765
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python3", "web_app.py", "--host", "0.0.0.0", "--port", "8765"]
