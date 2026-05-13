# Tiny FastAPI sidecar for the projects.json builder UI.
#
# Receives a POST /apply with a projects.json body, writes it atomically to
# /conf/projects.json (a volume shared with mordred), then runs
# `docker restart $MORDRED_CONTAINER_NAME` via the mounted docker socket.

FROM python:3.12-slim

# docker CLI for restarting the mordred container from inside this one.
RUN apt-get update \
 && apt-get install -y --no-install-recommends docker-cli ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi==0.115.13 "uvicorn[standard]==0.34.3"

WORKDIR /app
COPY main.py .

EXPOSE 8000

ENV CONF_DIR=/conf \
    MORDRED_CONTAINER_NAME=open-pulse-mordred

ENTRYPOINT ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
