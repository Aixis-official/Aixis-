FROM postgres:18-bookworm AS pgclient

FROM python:3.12-slim

WORKDIR /app

# Copy pg_dump / pg_restore v18 binaries from official PostgreSQL image
COPY --from=pgclient /usr/lib/postgresql/18/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=pgclient /usr/lib/postgresql/18/bin/pg_restore /usr/local/bin/pg_restore

# Copy LDAP/SASL libs from postgres image (not available in python:3.12-slim)
COPY --from=pgclient /usr/lib/*/libldap-2*.so* /usr/lib/
COPY --from=pgclient /usr/lib/*/liblber-2*.so* /usr/lib/
COPY --from=pgclient /usr/lib/*/libsasl2.so* /usr/lib/

# System deps: WeasyPrint + pg_dump runtime libraries (libpq, Kerberos)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev shared-mime-info \
    libpq5 libkrb5-3 libgssapi-krb5-2 \
    && rm -rf /var/lib/apt/lists/* && ldconfig

# Copy all source first (needed for hatch build)
COPY . .

# Force install compatible Jinja2 first, then install the rest
RUN pip install --no-cache-dir --force-reinstall "jinja2>=3.1,<3.2" && pip install --no-cache-dir .

# Create data directories and non-root user
RUN mkdir -p /data/backups /data/output /data/screenshots /data/uploads \
    && useradd -r -m -u 1000 appuser \
    && chown -R appuser:appuser /data /app

ENV PYTHONPATH=/app/src

EXPOSE 8000

# Run as non-root user for security hardening
USER appuser
CMD uvicorn aixis_web.app:app --host 0.0.0.0 --port ${PORT:-8000}
