# Backend image: one image for the API, the migrate init container, CronJobs and (later) runners.
FROM docker.io/library/python:3.13-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-compile -r requirements.txt

COPY app.py ./
COPY config ./config
COPY scripts ./scripts
COPY src ./src

# Non-root, fixed UID so Kubernetes can enforce runAsNonRoot and file ownership is predictable.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin lab
USER 10001
EXPOSE 8000

# --no-proxy-headers: the API resolves client addresses itself (api.trusted_proxies).
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
