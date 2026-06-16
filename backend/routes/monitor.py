# routes/monitor.py — Monitor API endpoints

from flask import Blueprint, jsonify, request
from db import get_connection, get_cursor
import monitor_service as svc

monitor_bp = Blueprint("monitor", __name__)

# ── Config (email + password) ────────────────────────────────
@monitor_bp.route("/monitor/config", methods=["GET"])
def get_config():
    conn = get_connection(); cur = get_cursor(conn)
    cur.execute("SELECT id, alert_email, interval_seconds FROM monitor_config LIMIT 1")
    row = cur.fetchone()
    cur.close(); conn.close()
    return jsonify(dict(row) if row else {})

@monitor_bp.route("/monitor/config", methods=["POST"])
def save_config():
    data = request.json
    email = data.get("alert_email", "").strip()
    password = data.get("app_password", "").strip()
    recipients = data.get("recipient_emails", "").strip()
    interval = int(data.get("interval_seconds", 120))
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM monitor_config LIMIT 1")
    exists = cur.fetchone()
    if exists:
        cur.execute("""UPDATE monitor_config SET alert_email=%s, app_password=%s,
                       recipient_emails=%s, interval_seconds=%s WHERE id=%s""",
                    (email, password, recipients, interval, exists[0]))
    else:
        cur.execute("""INSERT INTO monitor_config (alert_email, app_password, recipient_emails, interval_seconds)
                       VALUES (%s, %s, %s, %s)""", (email, password, recipients, interval))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True})

# ── URLs ─────────────────────────────────────────────────────
@monitor_bp.route("/monitor/urls", methods=["GET"])
def get_urls():
    conn = get_connection(); cur = get_cursor(conn)
    cur.execute("SELECT * FROM monitor_urls ORDER BY id")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])

@monitor_bp.route("/monitor/urls", methods=["POST"])
def add_url():
    data = request.json
    url = data.get("url", "").strip()
    label = data.get("label", url).strip()
    conn = get_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO monitor_urls (url, label) VALUES (%s, %s)", (url, label))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True})

@monitor_bp.route("/monitor/urls/<int:uid>", methods=["DELETE"])
def delete_url(uid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM monitor_urls WHERE id = %s", (uid,))
    cur.execute("DELETE FROM monitor_snapshots WHERE url_id = %s", (uid,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True})

@monitor_bp.route("/monitor/urls/<int:uid>/toggle", methods=["POST"])
def toggle_url(uid):
    conn = get_connection(); cur = conn.cursor()
    cur.execute("UPDATE monitor_urls SET is_active = NOT is_active WHERE id = %s", (uid,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"success": True})

# ── Start / Stop / Status ────────────────────────────────────
@monitor_bp.route("/monitor/start", methods=["POST"])
def start():
    ok = svc.start_monitor()
    return jsonify({"success": ok, "message": "Monitor started" if ok else "Already running"})

@monitor_bp.route("/monitor/stop", methods=["POST"])
def stop():
    svc.stop_monitor()
    return jsonify({"success": True, "message": "Monitor stopped"})

@monitor_bp.route("/monitor/status", methods=["GET"])
def status():
    return jsonify(svc.get_status())