# routes/auth.py — Email Login Only (Private Portal)

from flask import Blueprint, request, jsonify
import os
from dotenv import load_dotenv
from db import get_connection, get_cursor
import bcrypt
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

    return jsonify({
        "success": True,
        "name":  user["first_name"],
        "email": email
    })