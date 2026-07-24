FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY directive_contracts /build/directive_contracts
COPY setup/directive_ingest /build/directive_ingest
RUN pip install --upgrade pip \
    && pip install /build/directive_contracts /build/directive_ingest \
    && rm -rf /build

WORKDIR /app
COPY setup/directives /app/fixtures

RUN groupadd --gid 10001 ingestion \
    && useradd --uid 10001 --gid ingestion --no-create-home ingestion
USER 10001:10001

CMD ["directive-ingest", "run-daily"]
