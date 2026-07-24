FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY agent_contracts /build/agent_contracts
COPY agents/directive-rag-maf/src/directive-rag-maf/requirements.txt /build/
RUN pip install --upgrade pip \
    && pip install /build/agent_contracts -r /build/requirements.txt \
    && rm -rf /build

WORKDIR /app
COPY agents/directive-rag-maf/src/directive-rag-maf /app/

EXPOSE 8088

CMD ["python", "main.py"]
