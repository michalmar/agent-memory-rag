FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY agent_contracts /build/agent_contracts
COPY agents/customer-support-maf/src/customer-support-maf/requirements.txt /build/
RUN pip install --upgrade pip \
    && pip install /build/agent_contracts -r /build/requirements.txt \
    && rm -rf /build

WORKDIR /app
COPY agents/customer-support-maf/src/customer-support-maf /app/

EXPOSE 8088

CMD ["python", "main.py"]
