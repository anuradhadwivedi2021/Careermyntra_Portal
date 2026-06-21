# auth_utils.py — JWT Token Helper + Login Required Decorator
# Place this file in: backend/auth_utils.py

import os
import jwt
from functools import wraps
from datetime import datetime, timedelta, timezone
from flask import request, jsonify
from dotenv import load_dotenv

load_dotenv()

# ── Secret key for signing tokens ────────────────────────────
# IMPORTANT: Set JWT_SECRET in .env (a long random string).
# If missing, raise an error rather than using an insecure default.
def _get_secret():
    secret = os.getenv("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError(
            "JWT_SECRET .env mein set nahi hai! "
            "Run: python3 -c \"import secrets; print(secrets.token_hex(32))\" "
            "aur output ko JWT_SECRET=... .env mein daalo."
        )
    return secret

TOKEN_EXPIRY_HOURS = 24 * 7   # 7 days

# ── Generate a token after successful login ──────────────────
def generate_token(user_id, email, first_name):
    payload = {
        "user_id": user_id,
        "email": email,
        "first_name": first_name,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc)
    }
    return jwt.encode(payload, _get_secret(), algorithm="HS256")

# ── Decode / verify a token ───────────────────────────────────
def decode_token(token):
    try:
        payload = jwt.decode(token, _get_secret(), algorithms=["HS256"])
        return payload, None
    except jwt.ExpiredSignatureError:
        return None, "Token expired. Please log in again."
    except jwt.InvalidTokenError:
        return None, "Invalid token."

# ── Decorator to protect routes ───────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized. Please log in."}), 401

        token = auth_header.split(" ", 1)[1].strip()
        payload, error = decode_token(token)
        if error:
            return jsonify({"error": error}), 401

        # Make user info available to the route via flask.g if needed
        request.user = payload
        return f(*args, **kwargs)
    return decorated