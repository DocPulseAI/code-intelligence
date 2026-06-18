FROM python:3.11-slim

WORKDIR /app

# Install system dependencies required by git-based analysis and parser builds.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first to preserve layer caching.
COPY codeDetect/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt flask gunicorn

# Copy the application from the repo subdirectory into the runtime image.
COPY codeDetect/ .

# Ensure non-root nobody user owns the app directory
RUN chown -R nobody:nogroup /app

ENV PYTHONUNBUFFERED=1

EXPOSE 5000

# Run container as non-root
USER nobody

# Native python health check probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

CMD ["gunicorn", "-c", "gunicorn.conf.py", "api:app"]
