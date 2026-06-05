# routes/auth.py — Google OAuth Login (Redirect Flow)

from flask import Blueprint, request, jsonify, redirect
import requests
import os
from dotenv import load_dotenv

load_dotenv()

auth_bp = Blueprint('auth', __name__)

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

# routes/auth.py mein ye 2 lines change karo:

REDIRECT_URI = os.getenv("REDIRECT_URI", "https://careermyntra-portal-6.onrender.com/api/auth/google/callback")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://careermyntra-portal-4.onrender.com")

@auth_bp.route("/auth/google/callback", methods=["GET"])
def google_callback():
    code = request.args.get("code")

    if not code:
        return jsonify({"error": "No code received"}), 400

    # Step 1: Exchange code for token
    token_res = requests.post("https://oauth2.googleapis.com/token", data={
        "code":          code,
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri":  REDIRECT_URI,
        "grant_type":    "authorization_code"
    })
    token_data = token_res.json()
    access_token = token_data.get("access_token")

    if not access_token:
        return jsonify({"error": "Token exchange failed", "details": token_data}), 400

    # Step 2: Get user info from Google
    user_res = requests.get("https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    user = user_res.json()

    # Step 3: Redirect to frontend with user info in query params
    name    = requests.utils.quote(user.get("name", ""))
    email   = requests.utils.quote(user.get("email", ""))
    picture = requests.utils.quote(user.get("picture", ""))

    return redirect(
        f"{FRONTEND_URL}/index.html?name={name}&email={email}&picture={picture}"
    )