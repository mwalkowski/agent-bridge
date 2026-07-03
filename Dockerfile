FROM python:3.11-slim

LABEL org.opencontainers.image.title="Agent Bridge"
LABEL org.opencontainers.image.description="Runtime-agnostic, auditable message bus for coordinating heterogeneous LLM agents."
LABEL org.opencontainers.image.source="https://github.com/mwalkowski/agent-bridge"
LABEL org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app

# Install the package (no third-party dependencies for the core).
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir .

# Run as a non-root user and keep the append-only log on a volume.
RUN useradd --create-home --uid 10001 bridge \
    && mkdir -p /data \
    && chown bridge:bridge /data
USER bridge
VOLUME ["/data"]

EXPOSE 8765

# The HTTP API is unauthenticated by design. Publish it only on a trusted
# network (see README, "Safety model"). Inside the container it binds 0.0.0.0;
# restrict exposure with the host port mapping.
ENTRYPOINT ["agent-bridge-server"]
CMD ["--host", "0.0.0.0", "--port", "8765", "--root", "/data"]
