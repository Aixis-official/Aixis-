FROM python:3.12-slim

WORKDIR /app

# System deps for WeasyPrint
RUN apt-get update && apt-get install -y \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Copy all source first (needed for hatch build)
COPY . .

RUN pip install --no-cache-dir .

# NOTE: Use PostgreSQL in production (Railway addon).
# SQLite data is LOST on every deploy because containers are ephemeral.
RUN mkdir -p /data

ENV PYTHONPATH=/app/src

EXPOSE 8000
CMD uvicorn aixis_web.app:app --host 0.0.0.0 --port ${PORT:-8000}
