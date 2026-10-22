FROM python:3.12-slim
RUN pip install --no-cache-dir "httpx==0.28.1"
COPY replay.py /app/replay.py
USER 65534
CMD ["python", "-u", "/app/replay.py"]
