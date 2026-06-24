import os
import sys


# Bind to Render/Docker provided port when available.
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# Keep requests alive long enough for repo clone + analysis.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "360"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))

# Use a small threaded model to avoid total service stall on one long request.
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "gthread")
threads = int(os.getenv("GUNICORN_THREADS", "4"))

# Increase observability for crash diagnostics.
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "debug")
capture_output = True
accesslog = "-"
errorlog = "-"

# Recycle workers periodically to reduce leak-related instability.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "100"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "20"))

# Emit effective runtime settings at startup so deploy logs show actual config.
print(
    "[gunicorn-config] "
    f"bind={bind} "
    f"worker_class={worker_class} "
    f"workers={workers} "
    f"threads={threads} "
    f"timeout={timeout} "
    f"graceful_timeout={graceful_timeout} "
    f"loglevel={loglevel}",
    file=sys.stderr,
)
