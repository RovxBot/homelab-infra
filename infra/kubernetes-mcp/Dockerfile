FROM python:3.14.10-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b
LABEL org.opencontainers.image.source="https://github.com/RovxBot/homelab-infra"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FASTMCP_CHECK_FOR_UPDATES=off
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY src/ /app/
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python", "-B", "/app/server.py"]
