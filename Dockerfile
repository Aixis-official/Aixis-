FROM python:3.12-slim

WORKDIR /app

# System deps for Playwright + WeasyPrint
RUN apt-get update && apt-get install -y \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 \
    libffi-dev shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Install Playwright browsers (for audit engine)
RUN playwright install chromium --with-deps || true

COPY . .

ENV PYTHONPATH=/app/src

EXPOSE 8000
CMD uvicorn aixis_web.app:app --host 0.0.0.0 --port ${PORT:-8000}
