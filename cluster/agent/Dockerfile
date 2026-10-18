# Cluster agent. Build context: the research repo root (needs ic/ and cluster/agent/).
FROM python:3.12-slim
ARG KUBECTL_VERSION=v1.31.5
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/* \
 && ARCH=${TARGETARCH:-$(dpkg --print-architecture)} \
 && curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl \
 && echo "$(curl -fsSL https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
 && chmod +x /usr/local/bin/kubectl
WORKDIR /app
RUN pip install --no-cache-dir "pydantic>=2.0" "httpx>=0.27" "fastapi>=0.110" "uvicorn[standard]>=0.29" "python-dotenv>=1.0"
COPY ic ./ic
COPY cluster/agent/ic_cluster ./ic_cluster
ENV PYTHONUNBUFFERED=1
USER 65534
CMD ["uvicorn", "ic_cluster.agent:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
