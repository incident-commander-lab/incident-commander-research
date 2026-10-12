# Agent + fault injector. Build context is the research repo root (needs ic/).
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "pydantic>=2.0" "httpx>=0.27" "fastapi>=0.110" "uvicorn[standard]>=0.29" \
    "docker==7.1.0" "psycopg[binary]==3.2.3" "redis==5.2.1" "python-dotenv>=1.0"
COPY ic ./ic
COPY testbed/ops/testbed_ops ./testbed_ops
ENV PYTHONUNBUFFERED=1
