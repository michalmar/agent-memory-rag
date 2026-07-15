FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY agent_contracts /build/agent_contracts
COPY backend /build/backend
RUN pip install --upgrade pip \
    && pip install /build/agent_contracts /build/backend \
    && rm -rf /build

WORKDIR /app

EXPOSE 8000

CMD ["uvicorn", "agent_memory_backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
