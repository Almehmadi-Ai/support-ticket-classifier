import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./tickets.db")

MAX_CLASSIFICATION_ATTEMPTS = int(os.getenv("MAX_CLASSIFICATION_ATTEMPTS", "3"))
MAX_CONCURRENT_CLASSIFICATIONS = int(os.getenv("MAX_CONCURRENT_CLASSIFICATIONS", "3"))
RETRY_BACKOFF_SECONDS = float(os.getenv("RETRY_BACKOFF_SECONDS", "0.2"))

# Fallback poll so pending work is still picked up if an in-memory wake signal is
# ever missed. The wake event is the primary trigger; this is just a safety net.
WORKER_POLL_INTERVAL_SECONDS = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "5.0"))

DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100

MAX_STORED_ERROR_LENGTH = 500
