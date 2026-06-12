# main.py — CareerMyntra Flask Backend
# Run: python main.py

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import os
import uuid
import threading
import time

from routes.courses import courses_bp
from routes.upload import upload_bp
from routes.download import download_bp
from routes.auth import auth_bp
from routes.college_master import college_master_bp  # ← NEW
from routes.streams import streams_bp   

from db import init_db

# ─── App Setup ───────────────────────────────────────────────
app = Flask(__name__)

CORS(app, origins=[
    "https://careermyntra-portal-4.onrender.com",
     "https://careermyntra-portal-6.onrender.com",
    "http://localhost:5500",
    "http://127.0.0.1:5500"
])

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

# ─── Register Blueprints ─────────────────────────────────────
app.register_blueprint(courses_bp,        url_prefix="/api")
app.register_blueprint(auth_bp,           url_prefix="/api")
app.register_blueprint(upload_bp,         url_prefix="/api")
app.register_blueprint(download_bp,       url_prefix="/api")
app.register_blueprint(college_master_bp, url_prefix="/api")  # ← NEW
app.register_blueprint(streams_bp,        url_prefix="/api")  # ← STREAMS

# ─── Health Check ────────────────────────────────────────────
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "message": "CareerMyntra backend is running!",
        "version": "1.1.0"
    })
# ─── Run ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  CareerMyntra Backend Starting...")
    print("  URL: http://localhost:5000")
    print("  Health: http://localhost:5000/api/health")
    print("=" * 50)
    app.run(debug=False, host="0.0.0.0", port=5000)