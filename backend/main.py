# main.py — CareerMyntra Flask Backend
# Run: python main.py

import socket

# FIX: Render's outbound network doesn't support IPv6, but smtp.gmail.com
# sometimes resolves to an IPv6 address first, causing
# "[Errno 101] Network is unreachable" when sending reminder/monitor emails.
# Forcing IPv4-only DNS resolution for the whole process fixes this.
_original_getaddrinfo = socket.getaddrinfo
def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_only_getaddrinfo

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import uuid
import threading
import time

from routes.courses import courses_bp
from routes.upload import upload_bp
from routes.download import download_bp
from routes.auth import auth_bp
from routes.college_master import college_master_bp
from routes.streams import streams_bp
from routes.monitor import monitor_bp
from routes.reminder import reminders_bp

from routes.reminder_attachments import reminder_attachments_bp
from routes.college_predictor import college_predictor_bp
from routes.college_predictor_dynamic_filters import college_predictor_filters_bp
from routes.student_data import student_data_bp
from routes.student_delete import student_delete_bp

from db import init_db
from monitor_service import start_monitor
from reminder_scheduler import start_reminder_scheduler
from logger_setup import get_logger

logger = get_logger(__name__)

# ─── App Setup ───────────────────────────────────────────────
app = Flask(__name__)

CORS(app, origins=[

    
   "https://edtechmyntra.com",
    "https://www.edtechmyntra.com",   # ← ADD KARO

    "http://edtechmyntra.com",        # ← ADD KARO
    "http://www.edtechmyntra.com",    # ← ADD KARO
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://187.127.185.32",
    "http://187.127.185.32:5000",
])

# FIX: Rate limiting — prevents API abuse / spam
# main.py mein change karo:
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["2000 per day", "500 per hour"],  
    storage_uri="memory://"
)

# Stricter limit on login — prevents brute-force password guessing
limiter.exempt(upload_bp)
limiter.exempt(download_bp)
# NOTE: monitor_bp is NOT blanket-limited here anymore — monitor.html polls
# /monitor/status every 5s and /monitor/urls every 10s, so a shared "10 per
# hour" limit across the whole blueprint was exhausted within the first
# minute and made the Monitor page unusable (429 on every request).
# Sensitive monitor endpoints (password/config changes, start/stop) get
# their own tighter limits below, applied AFTER blueprint registration.

# ─── Config ──────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR  = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR  = os.path.join(BASE_DIR, "outputs")
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")

app.config["UPLOAD_DIR"]  = UPLOAD_DIR
app.config["OUTPUT_DIR"]  = OUTPUT_DIR
app.config["SCRIPTS_DIR"] = SCRIPTS_DIR
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

# ─── Make folders if not exist ───────────────────────────────
os.makedirs(UPLOAD_DIR,  exist_ok=True)
os.makedirs(OUTPUT_DIR,  exist_ok=True)
os.makedirs(SCRIPTS_DIR, exist_ok=True)

init_db()

def _delayed_start():
    time.sleep(5)  # let gunicorn fully boot first
    try:
        logger.info("[Startup] Calling start_monitor...")
        result = start_monitor()
        logger.info(f"[Startup] start_monitor returned: {result}")
    except Exception as e:
        logger.error(f"[Startup] start_monitor FAILED: {e}")
    try:
        start_reminder_scheduler()
        logger.info("[Startup] Reminder scheduler started")
    except Exception as e:
        logger.error(f"[Startup] Reminder scheduler FAILED: {e}")

threading.Thread(target=_delayed_start, daemon=True).start()

# ─── Register Blueprints ─────────────────────────────────────
app.register_blueprint(courses_bp,        url_prefix="/api")
app.register_blueprint(auth_bp,           url_prefix="/api")
app.register_blueprint(upload_bp,         url_prefix="/api")
app.register_blueprint(download_bp,       url_prefix="/api")
app.register_blueprint(college_master_bp, url_prefix="/api")
app.register_blueprint(streams_bp,        url_prefix="/api")
app.register_blueprint(monitor_bp,        url_prefix="/api")
app.register_blueprint(reminders_bp,          url_prefix="/api")
app.register_blueprint(reminder_attachments_bp, url_prefix="/api")
app.register_blueprint(college_predictor_bp,  url_prefix="/api")
app.register_blueprint(college_predictor_filters_bp, url_prefix="/api")
app.register_blueprint(student_data_bp, url_prefix="/api")
app.register_blueprint(student_delete_bp, url_prefix="/api")

# Targeted limits on monitor endpoints that change state (password/config,
# start/stop) — these are not polled repeatedly by the frontend like
# status/urls are, so a tight limit here doesn't break normal usage.
limiter.limit("10 per hour")(app.view_functions["monitor.save_config"])
limiter.limit("10 per hour")(app.view_functions["monitor.start"])
limiter.limit("10 per hour")(app.view_functions["monitor.stop"])

# ─── Health Check ────────────────────────────────────────────
@app.route("/api/health", methods=["GET"])
def health():
    from datetime import datetime
    from db import get_connection
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        db_status = "connected"
        http_code = 200
    except Exception as e:
        db_status = f"error: {str(e)}"
        http_code = 500

    return jsonify({
        "status": "ok" if db_status == "connected" else "unhealthy",
        "message": "CareerMyntra backend is running!",
        "database": db_status,
        "version": "1.1.0",
        "timestamp": datetime.now().isoformat()
    }), http_code

# ─── Run ─────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("  CareerMyntra Backend Starting...")
    logger.info("  URL: http://localhost:5000")
    logger.info("  Health: http://localhost:5000/api/health")
    logger.info("=" * 50)
    
    # Only for local testing
    app.run(debug=True, host="127.0.0.1", port=5000)