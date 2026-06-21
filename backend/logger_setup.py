# logger_setup.py — Central Logging Configuration
# Place this file in: backend/logger_setup.py
#
# Usage in any file:
#   from logger_setup import get_logger
#   logger = get_logger(__name__)
#   logger.info("Something happened")
#   logger.error(f"Something broke: {e}")

import logging
import logging.handlers
import os

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "app.log")

_configured = False

def _configure_root_logger():
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Rotating file handler: max 10MB per file, keep last 5 files
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Also print to console (Render captures this in its Logs tab)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)


def get_logger(name):
    _configure_root_logger()
    return logging.getLogger(name)