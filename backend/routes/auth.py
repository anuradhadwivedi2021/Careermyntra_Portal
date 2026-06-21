# routes/auth.py — Email Login Only (Private Portal)
# UPDATED: Login ke baad ab JWT token bhi return hota hai

from flask import Blueprint, request, jsonify
import os
from dotenv import load_dotenv
from db import get_connection, get_cursor
import bcrypt
from auth_utils import generate_token

load_dotenv()

auth_bp = Blueprint('auth', __name__)

# ── Login (Email + Password) ───────────────────────────────
@auth_bp.route("/auth/login", methods=["POST"])
def login():
    data     = request.get_json()
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"success": False, "error": "Email aur password zaroori hai"}), 400

    conn = get_connection()
    cur  = get_cursor(conn)

    cur.execute("SELECT id, first_name, password_hash FROM users WHERE email = %s", (email,))
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        return jsonify({"success": False, "error": "Email registered nahi hai"}), 401

    if not bcrypt.checkpw(password.encode("utf-8"), user["password_hash"].encode("utf-8")):
        return jsonify({"success": False, "error": "Galat password"}), 401

    # ── NEW: Generate JWT token after successful login ──────
    token = generate_token(user["id"], email, user["first_name"])

    return jsonify({
        "success": True,
        "name":  user["first_name"],
        "email": email,
        "token": token   # frontend isko localStorage mein save karega
    })


# ── Verify token (optional helper endpoint for frontend) ────
@auth_bp.route("/auth/verify", methods=["GET"])
def verify():
    from auth_utils import decode_token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"valid": False}), 401

    token = auth_header.split(" ", 1)[1].strip()
    payload, error = decode_token(token)
    if error:
        return jsonify({"valid": False, "error": error}), 401

    return jsonify({"valid": True, "email": payload["email"], "first_name": payload["first_name"]})