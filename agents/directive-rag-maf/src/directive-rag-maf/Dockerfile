FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY agent_contracts /build/agent_contracts
COPY directive_contracts /build/directive_contracts
COPY maf_hosting /build/maf_hosting
COPY agents/directive-rag-maf/src/directive-rag-maf/requirements.txt /build/
RUN pip install --upgrade pip \
    && pip install /build/directive_contracts /build/agent_contracts /build/maf_hosting -r /build/requirements.txt \
    && rm -rf /build

WORKDIR /app
COPY agents/directive-rag-maf/src/directive-rag-maf /app/

EXPOSE 8088

CMD ["python", "main.py"]
