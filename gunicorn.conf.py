import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = int(os.getenv("WEB_WORKERS", "2"))
threads = int(os.getenv("WEB_THREADS", "4"))
worker_class = "gthread"
timeout = 30
graceful_timeout = 30
keepalive = 5
max_requests = 2000
max_requests_jitter = 200
# Application metrics log route patterns rather than sensitive recovery/receipt URLs.
accesslog = None
errorlog = "-"
capture_output = True
