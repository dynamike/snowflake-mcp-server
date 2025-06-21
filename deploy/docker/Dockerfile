# Multi-stage Docker build for Snowflake MCP Server
FROM python:3.12-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster Python package management
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock ./
COPY snowflake_mcp_server/ ./snowflake_mcp_server/
COPY README.md CLAUDE.md ./

# Install dependencies and package
RUN uv venv && \
    uv pip install -e .

# Production image
FROM python:3.12-slim as production

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r snowflake-mcp && \
    useradd -r -g snowflake-mcp -d /app -s /bin/bash snowflake-mcp

# Set working directory
WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder --chown=snowflake-mcp:snowflake-mcp /app/.venv /app/.venv
COPY --from=builder --chown=snowflake-mcp:snowflake-mcp /app/snowflake_mcp_server /app/snowflake_mcp_server
COPY --from=builder --chown=snowflake-mcp:snowflake-mcp /app/pyproject.toml /app/

# Set up environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1

# Create logs directory
RUN mkdir -p /app/logs && chown snowflake-mcp:snowflake-mcp /app/logs

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Switch to non-root user
USER snowflake-mcp

# Expose ports
EXPOSE 8000 8001

# Default command (can be overridden)
CMD ["python", "-m", "snowflake_mcp_server.main", "--mode", "http", "--host", "0.0.0.0", "--port", "8000"]