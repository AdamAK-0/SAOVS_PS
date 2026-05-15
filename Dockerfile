FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV SAOVS_SERVER_ROOT=/app
ENV SAOVS_DB=/data/runtime/saovs.sqlite3
ENV SAOVS_LOG_DIR=/data/runtime/logs
ENV SAOVS_CONTENT_ROOT=/data/content/files
ENV SAOVS_ASSET_BASE=https://assets-os.saovs.channel.or.jp/
ENV SAOVS_ASSET_HOSTS=assets-os.saovs.channel.or.jp
ENV SAOVS_ASSET_VER=30000
ENV SAOVS_MASTER_DATA_VER=202
ENV SAOVS_LOCALIZE_DATA_VER=161
ENV SAOVS_DEFAULT_USER_ID=183705490
ENV SAOVS_DEFAULT_USER_CODE=46841725594

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY README.md ./README.md
COPY docs ./docs
COPY content/README.md ./content/README.md

RUN mkdir -p /data/runtime/logs /data/content/files

EXPOSE 8000

CMD ["python", "-m", "saovs_private_server.compat_server", "--host", "0.0.0.0", "--port", "8000"]

