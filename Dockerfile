FROM python:3.9-slim

WORKDIR /app

# Install system dependencies required by git-based analysis and parser builds.
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first to preserve layer caching.
COPY codeDetect/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt flask gunicorn

# Copy the application from the repo subdirectory into the runtime image.
COPY codeDetect/ .

ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["gunicorn", "-c", "gunicorn.conf.py", "api:app"]
