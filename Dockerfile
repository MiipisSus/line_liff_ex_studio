FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# collectstatic 只需要能載入 settings，這裡給假的必要變數；實際值在執行時由環境變數 / Fly secrets 提供
RUN DJANGO_SECRET_KEY=build-only LIFF_ID=build-only LINE_CHANNEL_ID=build-only \
    python manage.py collectstatic --noinput

RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app

EXPOSE 8000

# 256MB 機器：1 個 worker + 多執行緒，避免多 worker 把記憶體吃滿
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "30", "--access-logfile", "-"]
