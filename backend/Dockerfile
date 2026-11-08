FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN useradd --create-home --uid 10001 aegis \
    && mkdir -p /srv/aegis-data && chown aegis /srv/aegis-data
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
USER aegis

EXPOSE 8000
# Apply migrations, add default languages, then serve.
# Behind a reverse proxy (nginx/Caddy) add: --proxy-headers --forwarded-allow-ips=<proxy ip>
CMD ["sh", "-c", "alembic upgrade head && python -m app.cli seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
