FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

ARG UV_VERSION=0.8.15

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir "uv==${UV_VERSION}"
