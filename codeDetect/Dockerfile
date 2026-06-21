FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY codeDetect/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt flask gunicorn

# Copy application files
COPY codeDetect/ .

# Create non-root user and adjust ownership
RUN useradd -m appuser && chown -R appuser:appuser /app

# Environment variables
ENV PYTHONUNBUFFERED=1

# Expose port for Flask API
EXPOSE 5000

# Run container as non-root
USER appuser

# Native python health check probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

# Run HTTP API service.
CMD ["gunicorn", "-c", "gunicorn.conf.py", "api:app"]
