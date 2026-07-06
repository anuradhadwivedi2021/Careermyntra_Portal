from flask import Blueprint, jsonify, request
from db import get_connection, get_cursor
import monitor_service as svc
from routes.crypto_utils import encrypt_password, decrypt_password
from auth_utils import login_required

monitor_bp = Blueprint("monitor", __name__)


# ── Config (email + password) ────────────────────────────────
@monitor_bp.route("/monitor/config", methods=["GET"])
@login_required
def get_config():
    conn = get_connection(); cur = get_cursor(conn)
    # FIX: app_password SELECT mein shamil NAHI — frontend ko kabhi bhi
    #      plain/encrypted password nahi milni chahiye
    cur.execute("""
        SELECT id, alert_email, recipient_emails, interval_seconds
        FROM monitor_config LIMIT 1
    """)
    row = cur.fetchone()
    cur.close(); conn.close()
    if row:
        result = dict(row)
        result["has_password"] = True  # Frontend ko sirf ye batao ki password set hai ya nahi
        return jsonify(result)
    return jsonify({})


@monitor_bp.route("/monitor/config", methods=["POST"])
@login_required
def save_config():
    data       = request.json
    email      = data.get("alert_email", "").strip()
    password   = data.get("app_password", "").strip()
    recipients = data.get("recipient_emails", "").strip()
    interval   = int(data.get("interval_seconds", 120))

    # FIX: Save karne se pehle password encrypt karo
    encrypted_pw = encrypt_password(password) if password else None

    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT id, app_password FROM monitor_config LIMIT 1")
    exists = cur.fetchone()

    if exists:
        if encrypted_pw:
            # Naya password diya — update karo
            cur.execute("""
                UPDATE monitor_config
                SET alert_email=%s, app_password=%s,
                    recipient_emails=%s, interval_seconds=%s
                WHERE id=%s
            """, (email, encrypted_pw, recipients, interval, exists[0]))
        else:
            # Password field blank — purana encrypted password rehne do
            cur.execute("""
                UPDATE monitor_config
                SET alert_email=%s, recipient_emails=%s, interval_seconds=%s
                WHERE id=%s
            """, (email, recipients, interval, exists[0]))
    else:
        cur.execute("""
            INSERT INTO monitor_config
                (alert_email, app_password, recipient_emails, interval_seconds)
            VALUES (%s, %s, %s, %s)
        """, (email, encrypted_pw or "", recipients, interval))

    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True})


# ── URLs ─────────────────────────────────────────────────────
@monitor_bp.route("/monitor/urls", methods=["GET"])
@login_required
def get_urls():
    conn = get_connection(); cur = get_cursor(conn)
    cur.execute("SELECT * FROM monitor_urls ORDER BY id")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])


@monitor_bp.route("/monitor/urls", methods=["POST"])
@login_required
def add_url():
    data  = request.json
    url   = data.get("url", "").strip()
    label = data.get("label", url).strip()
    conn  = get_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO monitor_urls (url, label) VALUES (%s, %s)", (url, label))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True})


@monitor_bp.route("/monitor/urls/<int:uid>", methods=["DELETE"])
@login_required
def delete_url(uid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM monitor_alerts WHERE url_id = %s", (uid,))
    cur.execute("DELETE FROM monitor_snapshots WHERE url_id = %s", (uid,))
    cur.execute("DELETE FROM monitor_urls WHERE id = %s", (uid,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True})


@monitor_bp.route("/monitor/urls/<int:uid>/toggle", methods=["POST"])
@login_required
def toggle_url(uid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("UPDATE monitor_urls SET is_active = NOT is_active WHERE id = %s", (uid,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True})


# ── Start / Stop / Status ────────────────────────────────────
@monitor_bp.route("/monitor/start", methods=["POST"])
@login_required
def start():
    ok = svc.start_monitor()
    return jsonify({"success": ok, "message": "Monitor started" if ok else "Already running"})


@monitor_bp.route("/monitor/stop", methods=["POST"])
@login_required
def stop():
    svc.stop_monitor()
    return jsonify({"success": True, "message": "Monitor stopped"})


@monitor_bp.route("/monitor/status", methods=["GET"])
@login_required
def status():
    s = svc.get_status()
    return jsonify({
        "running":      s.get("running"),
        "last_checked": s.get("last_checked_at"),
        "last_alert":   s.get("last_alert"),
        "log":          s.get("log", [])
    })