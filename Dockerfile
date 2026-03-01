# ──────────────────────────────────────────────────────────
# Uzhavan AI - Voice Backend Docker Image
# Multi-stage build for minimal image size
# ──────────────────────────────────────────────────────────

FROM python:3.11-slim AS builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Production image ─────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY . .

# Non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

# Start with uvicorn (production workers)
CMD ["python", "-m", "uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--ws", "websockets", \
     "--ws-ping-interval", "20", \
     "--ws-ping-timeout", "20", \
     "--timeout-keep-alive", "300", \
     "--log-level", "info"]
