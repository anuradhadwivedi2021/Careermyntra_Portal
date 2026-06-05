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

from routes.auth import auth_bp        # ← ADD
from db import init_db  # ← SIRF YE LINE ADD KI

# ─── App Setup ───────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

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

init_db()  # ← SIRF YE LINE ADD KI

# ─── Register Blueprints ─────────────────────────────────────
app.register_blueprint(courses_bp,  url_prefix="/api")
app.register_blueprint(auth_bp, url_prefix="/api")   # ← ADD
app.register_blueprint(upload_bp,   url_prefix="/api")
app.register_blueprint(download_bp, url_prefix="/api")

# ─── Health Check ────────────────────────────────────────────
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "message": "CareerMyntra backend is running!",
        "version": "1.0.0"
    })

# ─── Run ─────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  CareerMyntra Backend Starting...")

    print("  URL: http://localhost:5000")
    print("  Health: http://localhost:5000/api/health")


    print("=" * 50)
    app.run(debug=False, host="0.0.0.0", port=5000)