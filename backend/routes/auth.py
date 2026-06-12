# routes/auth.py — Google OAuth + Email OTP 2FA

from flask import Blueprint, request, jsonify, redirect
import requests
import os
import random
import string
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

auth_bp = Blueprint('auth', __name__)

GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI         = os.getenv("REDIRECT_URI", "https://careermyntra-portal-6.onrender.com/api/auth/google/callback")
FRONTEND_URL         = os.getenv("FRONTEND_URL", "https://careermyntra-portal-4.onrender.com")

# SMTP config — .env mein add karo
SMTP_EMAIL    = os.getenv("SMTP_EMAIL", "")       # e.g. careermyntra@gmail.com
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")    # Gmail App Password
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))

# In-memory OTP store: { email: { otp, expires, name, picture } }
# Production mein Redis ya DB use karo
_otp_store = {}

# ── OTP generate karo ──────────────────────────────────────
def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

# ── Email bhejo ────────────────────────────────────────────
def send_otp_email(to_email, name, otp):
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print(f"[OTP] SMTP not configured — OTP for {to_email}: {otp}")
        return True  # Dev mode mein skip karo, console mein print hoga

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "🔐 CareerMyntra — Your Login OTP"
        msg["From"]    = f"CareerMyntra <{SMTP_EMAIL}>"
        msg["To"]      = to_email

        html = f"""
        <div style="font-family:'Segoe UI',sans-serif;max-width:480px;margin:0 auto;background:#f0f4ff;padding:32px;border-radius:16px;">
          <div style="background:#1565c0;border-radius:12px;padding:24px;text-align:center;margin-bottom:24px;">
            <h2 style="color:#fff;margin:0;font-size:22px;">🔐 Login Verification</h2>
          </div>
          <div style="background:#fff;border-radius:12px;padding:28px;text-align:center;">
            <p style="color:#374151;font-size:15px;margin-bottom:8px;">Hi <strong>{name}</strong>,</p>
            <p style="color:#374151;font-size:14px;margin-bottom:24px;">Your one-time password for CareerMyntra login:</p>
            <div style="background:#f0f4ff;border:2px dashed #1565c0;border-radius:12px;padding:20px;margin-bottom:24px;">
              <span style="font-size:36px;font-weight:800;color:#1565c0;letter-spacing:10px;">{otp}</span>
            </div>
            <p style="color:#9ca3af;font-size:13px;">⏱️ Valid for <strong>5 minutes</strong> only.</p>
            <p style="color:#9ca3af;font-size:12px;margin-top:8px;">Agar aapne login nahi kiya toh is email ko ignore karo.</p>
          </div>
          <p style="text-align:center;color:#9ca3af;font-size:12px;margin-top:16px;">© CareerMyntra · DTE Maharashtra Portal</p>
        </div>
        """
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, to_email, msg.as_string())

        print(f"[OTP] Email sent to {to_email}")
        return True
    except Exception as e:
        print(f"[OTP ERROR] Email send failed: {e}")
        return False

# ── Google OAuth Callback ──────────────────────────────────
@auth_bp.route("/auth/google/callback", methods=["GET"])
def google_callback():
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "No code received"}), 400

    # Token exchange
    token_res = requests.post("https://oauth2.googleapis.com/token", data={
        "code":          code,
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri":  REDIRECT_URI,
        "grant_type":    "authorization_code"
    })
    token_data  = token_res.json()
    access_token = token_data.get("access_token")
    if not access_token:
        return jsonify({"error": "Token exchange failed", "details": token_data}), 400

    # User info Google se
    user_res = requests.get("https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    user    = user_res.json()
    email   = user.get("email", "")
    name    = user.get("name", "User")
    picture = user.get("picture", "")

    # OTP generate aur store karo
    otp     = generate_otp()
    expires = time.time() + 300  # 5 min

    _otp_store[email] = {
        "otp":     otp,
        "expires": expires,
        "name":    name,
        "picture": picture
    }

    # Email bhejo
    send_otp_email(email, name, otp)

    # OTP page pe redirect karo (email query param mein)
    safe_email = requests.utils.quote(email)
    safe_name  = requests.utils.quote(name)
    return redirect(f"{FRONTEND_URL}/otp.html?email={safe_email}&name={safe_name}")

# ── OTP Verify Endpoint ────────────────────────────────────
@auth_bp.route("/auth/verify-otp", methods=["POST"])
def verify_otp():
    data  = request.get_json()
    email = data.get("email", "").strip()
    otp   = data.get("otp", "").strip()

    if not email or not otp:
        return jsonify({"success": False, "error": "Email aur OTP dono chahiye"}), 400

    record = _otp_store.get(email)
    if not record:
        return jsonify({"success": False, "error": "OTP nahi mila ya expire ho gaya"}), 400

    if time.time() > record["expires"]:
        del _otp_store[email]
        return jsonify({"success": False, "error": "OTP expire ho gaya. Dobara login karo."}), 400

    if record["otp"] != otp:
        return jsonify({"success": False, "error": "Galat OTP hai!"}), 400

    # OTP sahi — delete karo aur user data bhejo
    name    = record["name"]
    picture = record["picture"]
    del _otp_store[email]

    return jsonify({
        "success": True,
        "name":    name,
        "email":   email,
        "picture": picture
    })

# ── OTP Resend ─────────────────────────────────────────────
@auth_bp.route("/auth/resend-otp", methods=["POST"])
def resend_otp():
    data  = request.get_json()
    email = data.get("email", "").strip()

    record = _otp_store.get(email)
    if not record:
        return jsonify({"success": False, "error": "Pehle Google se login karo"}), 400

    # Naya OTP generate karo
    new_otp = generate_otp()
    _otp_store[email]["otp"]     = new_otp
    _otp_store[email]["expires"] = time.time() + 300

    send_otp_email(email, record["name"], new_otp)
    return jsonify({"success": True, "message": "Naya OTP bheja gaya!"})