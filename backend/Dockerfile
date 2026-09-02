# syntax=docker/dockerfile:1.7
#
# CrimeLink API image.  One image serves the API, the Celery worker and beat —
# only the entrypoint differs, which keeps the air-gapped image inventory small.

FROM python:3.11.9-slim-bookworm AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential=12.9 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /install
COPY pyproject.toml ./
COPY README.md ./
# The package lives in ./app; ship it so a wheel build sees the real layout.
COPY app ./app

# Dependencies only: this layer is cached until pyproject.toml changes.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip==24.2 \
 && /opt/venv/bin/pip install .

FROM python:3.11.9-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    CRIMELINK_PROFILE=production \
    CRIMELINK_ENVIRONMENT=production

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl=7.88.1-10+deb12u12 libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 crimelink

COPY --from=build /opt/venv /opt/venv

WORKDIR /srv/crimelink
COPY --chown=crimelink:crimelink app ./app
COPY --chown=crimelink:crimelink scripts ./scripts
COPY --chown=crimelink:crimelink alembic ./alembic
COPY --chown=crimelink:crimelink alembic.ini ./alembic.ini
COPY --chown=crimelink:crimelink pyproject.toml ./pyproject.toml

USER crimelink
ENV PYTHONPATH=/srv/crimelink
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live')"

# Two workers per container is deliberate: the API is I/O bound against
# Postgres/Neo4j, and a district deployment runs a single API container.
CMD ["uvicorn", "app.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
