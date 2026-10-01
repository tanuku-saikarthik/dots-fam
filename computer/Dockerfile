# The computer each Dot gets: a persistent Chromium profile, a /data workspace, an optional shell.
# Build from the repository root:
#   docker build -f computer/Dockerfile -t dotsfam-computer:latest .
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

RUN pip install --no-cache-dir "playwright==1.63.0" "fastapi>=0.115" "uvicorn>=0.30" \
    && mkdir -p /data /app && chown pwuser:pwuser /data

COPY backend/dotsfam/computer_service.py /app/computer_service.py

USER pwuser
ENV DOTSFAM_HOST=0.0.0.0 \
    DOTSFAM_PORT=8800 \
    DOTSFAM_DATA=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8800
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8800), 2)"
CMD ["python", "/app/computer_service.py"]
