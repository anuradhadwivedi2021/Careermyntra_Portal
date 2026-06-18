# routes/reminders.py — Exam & Admissions Reminder Manager API
# Register in main.py:
#   from routes.reminders import reminders_bp
#   app.register_blueprint(reminders_bp, url_prefix="/api")

from flask import Blueprint, jsonify, request, current_app
from db import get_connection, get_cursor
import os
import uuid
import smtplib
import threading
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

reminders_bp = Blueprint("reminders", __name__)

ALLOWED_ATTACH = {".pdf", ".jpg", ".jpeg", ".png", ".xlsx", ".docx", ".txt"}

# ─────────────────────────────────────────────────────────────
# HELPER — replace {{variables}} in template
# ─────────────────────────────────────────────────────────────
def fill_template(template: str, event: dict, duration_label: str = "") -> str:
    replacements = {
        "{{EventTitle}}":       event.get("title", ""),
        "{{Category}}":         event.get("category_name", ""),
        "{{EventDate}}":        str(event.get("event_date", "")),
        "{{EventTime}}":        str(event.get("event_time", "") or ""),
        "{{EventDescription}}": event.get("description", "") or "",
        "{{ReminderDuration}}": duration_label,
    }
    result = template
    for key, val in replacements.items():
        result = result.replace(key, val)
    return result


# ─────────────────────────────────────────────────────────────
# HELPER — send email via Gmail SMTP
# ─────────────────────────────────────────────────────────────
def send_email(to_email: str, subject: str, body: str) -> tuple[bool, str]:
    try:
        sender   = os.getenv("MONITOR_EMAIL", "")
        password = os.getenv("MONITOR_EMAIL_PASSWORD", "")
        if not sender or not password:
            return False, "Email credentials not configured in .env"

        msg = MIMEMultipart()
        msg["From"]    = sender
        msg["To"]      = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, to_email, msg.as_string())
        return True, "sent"
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# CATEGORIES
# ─────────────────────────────────────────────────────────────
@reminders_bp.route("/reminders/categories", methods=["GET"])
def get_categories():
    conn = get_connection(); cur = get_cursor(conn)
    cur.execute("SELECT * FROM reminder_categories ORDER BY name")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])


@reminders_bp.route("/reminders/categories", methods=["POST"])
def add_category():
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name required"}), 400
    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO reminder_categories (name) VALUES (%s) ON CONFLICT (name) DO NOTHING RETURNING id",
            (name,)
        )
        row = cur.fetchone()
        conn.commit(); cur.close(); conn.close()
        return jsonify({"success": True, "id": row[0] if row else None})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# SUB CATEGORIES
# ─────────────────────────────────────────────────────────────
@reminders_bp.route("/reminders/subcategories", methods=["GET"])
def get_subcategories():
    conn = get_connection(); cur = get_cursor(conn)
    cur.execute("SELECT * FROM reminder_subcategories ORDER BY name")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])


@reminders_bp.route("/reminders/subcategories", methods=["POST"])
def add_subcategory():
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"success": False, "error": "Name required"}), 400
    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO reminder_subcategories (name, category_id) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING RETURNING id",
            (name, data.get("category_id"))
        )
        row = cur.fetchone()
        conn.commit(); cur.close(); conn.close()
        return jsonify({"success": True, "id": row[0] if row else None})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# EVENTS — CRUD
# ─────────────────────────────────────────────────────────────
@reminders_bp.route("/reminders/events", methods=["GET"])
def get_events():
    conn = get_connection(); cur = get_cursor(conn)
    cur.execute("""
        SELECT e.*,
               c.name  AS category_name,
               sc.name AS subcategory_name
        FROM reminder_events e
        LEFT JOIN reminder_categories    c  ON c.id  = e.category_id
        LEFT JOIN reminder_subcategories sc ON sc.id = e.subcategory_id
        ORDER BY e.event_date ASC
    """)
    events = [dict(r) for r in cur.fetchall()]

    for ev in events:
        cur.execute("SELECT * FROM reminder_schedules WHERE event_id = %s ORDER BY remind_at", (ev["id"],))
        ev["reminders"] = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM reminder_recipients WHERE event_id = %s", (ev["id"],))
        ev["recipients"] = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT * FROM reminder_event_attachments WHERE event_id = %s", (ev["id"],))
        ev["attachments"] = [dict(r) for r in cur.fetchall()]

    cur.close(); conn.close()
    return jsonify(events)


@reminders_bp.route("/reminders/events", methods=["POST"])
def create_event():
    data = request.json or {}

    title = data.get("title", "").strip()
    if not title:
        return jsonify({"success": False, "error": "Title required"}), 400
    if not data.get("event_date"):
        return jsonify({"success": False, "error": "Event date required"}), 400
    if not data.get("category_id"):
        return jsonify({"success": False, "error": "Category required"}), 400

    try:
        conn = get_connection(); cur = conn.cursor()

        # ── Insert event ──
        cur.execute("""
            INSERT INTO reminder_events
                (title, category_id, subcategory_id, description,
                 event_date, event_time, start_dt, end_dt, priority, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            title,
            data.get("category_id"),
            data.get("subcategory_id") or None,
            data.get("description", ""),
            data["event_date"],
            data.get("event_time") or None,
            data.get("start_dt") or None,
            data.get("end_dt") or None,
            data.get("priority", "medium"),
            data.get("status", "upcoming"),
        ))
        event_id = cur.fetchone()[0]

        # ── Insert reminder schedules ──
        reminders = data.get("reminders", [])  # list of {"label": "7d", "remind_at": "2025-06-10T08:00"}
        for r in reminders:
            if r.get("remind_at"):
                cur.execute("""
                    INSERT INTO reminder_schedules (event_id, remind_at, label)
                    VALUES (%s, %s, %s)
                """, (event_id, r["remind_at"], r.get("label", "")))

        # ── Insert recipients ──
        for email in data.get("emails", []):
            if email.strip():
                cur.execute(
                    "INSERT INTO reminder_recipients (event_id, type, value) VALUES (%s, 'email', %s)",
                    (event_id, email.strip())
                )
        for phone in data.get("phones", []):
            if phone.strip():
                cur.execute(
                    "INSERT INTO reminder_recipients (event_id, type, value) VALUES (%s, 'whatsapp', %s)",
                    (event_id, phone.strip())
                )

        conn.commit(); cur.close(); conn.close()
        return jsonify({"success": True, "event_id": event_id})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@reminders_bp.route("/reminders/events/<int:event_id>", methods=["GET"])
def get_event(event_id):
    conn = get_connection(); cur = get_cursor(conn)
    cur.execute("""
        SELECT e.*, c.name AS category_name, sc.name AS subcategory_name
        FROM reminder_events e
        LEFT JOIN reminder_categories    c  ON c.id  = e.category_id
        LEFT JOIN reminder_subcategories sc ON sc.id = e.subcategory_id
        WHERE e.id = %s
    """, (event_id,))
    ev = cur.fetchone()
    if not ev:
        cur.close(); conn.close()
        return jsonify({"error": "Event not found"}), 404

    ev = dict(ev)
    cur.execute("SELECT * FROM reminder_schedules  WHERE event_id = %s ORDER BY remind_at", (event_id,))
    ev["reminders"] = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM reminder_recipients WHERE event_id = %s", (event_id,))
    ev["recipients"] = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM reminder_event_attachments WHERE event_id = %s", (event_id,))
    ev["attachments"] = [dict(r) for r in cur.fetchall()]

    cur.close(); conn.close()
    return jsonify(ev)


@reminders_bp.route("/reminders/events/<int:event_id>", methods=["PUT"])
def update_event(event_id):
    data = request.json or {}
    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("""
            UPDATE reminder_events SET
                title          = %s,
                category_id    = %s,
                subcategory_id = %s,
                description    = %s,
                event_date     = %s,
                event_time     = %s,
                start_dt       = %s,
                end_dt         = %s,
                priority       = %s,
                status         = %s,
                updated_at     = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (
            data.get("title"),
            data.get("category_id"),
            data.get("subcategory_id") or None,
            data.get("description", ""),
            data.get("event_date"),
            data.get("event_time") or None,
            data.get("start_dt") or None,
            data.get("end_dt") or None,
            data.get("priority", "medium"),
            data.get("status", "upcoming"),
            event_id,
        ))
        conn.commit(); cur.close(); conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@reminders_bp.route("/reminders/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("DELETE FROM reminder_events WHERE id = %s", (event_id,))
        conn.commit(); cur.close(); conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# ATTACHMENTS — upload file for an event
# ─────────────────────────────────────────────────────────────
@reminders_bp.route("/reminders/events/<int:event_id>/attachments", methods=["POST"])
def upload_attachment(event_id):
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"success": False, "error": "Empty filename"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_ATTACH:
        return jsonify({"success": False, "error": f"File type {ext} not allowed"}), 400

    upload_dir = os.path.join(current_app.config.get("UPLOAD_DIR", "uploads"), "reminders")
    os.makedirs(upload_dir, exist_ok=True)

    unique_name = f"{uuid.uuid4()}{ext}"
    save_path   = os.path.join(upload_dir, unique_name)
    file.save(save_path)

    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO reminder_event_attachments (event_id, filename, filepath, filesize)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (event_id, file.filename, save_path, os.path.getsize(save_path)))
        attach_id = cur.fetchone()[0]
        conn.commit(); cur.close(); conn.close()
        return jsonify({"success": True, "attachment_id": attach_id, "filename": file.filename})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@reminders_bp.route("/reminders/attachments/<int:attach_id>", methods=["DELETE"])
def delete_attachment(attach_id):
    try:
        conn = get_connection(); cur = get_cursor(conn)
        cur.execute("SELECT filepath FROM reminder_event_attachments WHERE id = %s", (attach_id,))
        row = cur.fetchone()
        if row and os.path.exists(row["filepath"]):
            os.remove(row["filepath"])
        cur2 = conn.cursor()
        cur2.execute("DELETE FROM reminder_event_attachments WHERE id = %s", (attach_id,))
        conn.commit(); cur.close(); cur2.close(); conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# TEMPLATES — get & save email/whatsapp templates
# ─────────────────────────────────────────────────────────────
@reminders_bp.route("/reminders/templates", methods=["GET"])
def get_templates():
    conn = get_connection(); cur = get_cursor(conn)
    cur.execute("SELECT * FROM reminder_templates ORDER BY channel")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])


@reminders_bp.route("/reminders/templates/<channel>", methods=["PUT"])
def save_template(channel):
    if channel not in ("email", "whatsapp", "sms"):
        return jsonify({"success": False, "error": "Invalid channel"}), 400
    data = request.json or {}
    body = data.get("body", "").strip()
    if not body:
        return jsonify({"success": False, "error": "Body required"}), 400
    
    # ── Decode URL-encoded content if needed ──
    import urllib.parse
    if "%0A" in body or "%0D" in body:
        body = urllib.parse.unquote(body)
    
    # ── Normalize line endings ──
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    
    subject = data.get("subject", "")
    if subject and ("%0A" in subject or "%0D" in subject):
        import urllib.parse
        subject = urllib.parse.unquote(subject)
    subject = subject.replace("\r\n", "\n").replace("\r", "\n")
    
    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO reminder_templates (channel, subject, body, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (channel) DO UPDATE
            SET subject = EXCLUDED.subject, body = EXCLUDED.body, updated_at = CURRENT_TIMESTAMP
        """, (channel, subject, body))
        conn.commit(); cur.close(); conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# NOTIFICATION LOGS
# ─────────────────────────────────────────────────────────────
@reminders_bp.route("/reminders/logs", methods=["GET"])
def get_logs():
    conn = get_connection(); cur = get_cursor(conn)
    cur.execute("""
        SELECT nl.*, e.title AS event_title
        FROM reminder_notification_logs nl
        LEFT JOIN reminder_events e ON e.id = nl.event_id
        ORDER BY nl.created_at DESC
        LIMIT 100
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([dict(r) for r in rows])


# ─────────────────────────────────────────────────────────────
# DASHBOARD STATS
# ─────────────────────────────────────────────────────────────
@reminders_bp.route("/reminders/stats", methods=["GET"])
def get_stats():
    conn = get_connection(); cur = get_cursor(conn)
    today = datetime.now().date()

    cur.execute("SELECT COUNT(*) FROM reminder_events WHERE status = 'upcoming' AND event_date >= %s", (today,))
    upcoming = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM reminder_events WHERE event_date = %s", (today,))
    today_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM reminder_events WHERE status = 'upcoming' AND event_date < %s", (today,))
    missed = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM reminder_notification_logs WHERE status = 'sent' AND DATE(sent_at) = %s", (today,))
    sent_today = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM reminder_notification_logs WHERE status = 'failed' AND DATE(created_at) = %s", (today,))
    failed_today = cur.fetchone()[0]

    cur.close(); conn.close()
    return jsonify({
        "upcoming":    upcoming,
        "today":       today_count,
        "missed":      missed,
        "sent_today":  sent_today,
        "failed_today": failed_today,
    })


# ─────────────────────────────────────────────────────────────
# SEND REMINDER MANUALLY (test / trigger)
# ─────────────────────────────────────────────────────────────
@reminders_bp.route("/reminders/send/<int:event_id>", methods=["POST"])
def send_reminder_now(event_id):
    """
    Manually trigger reminder for an event (for testing).
    Body: { "channel": "email" }  — or omit for all channels
    """
    data     = request.json or {}
    channel  = data.get("channel")   # optional filter

    conn = get_connection(); cur = get_cursor(conn)

    # Fetch event
    cur.execute("""
        SELECT e.*, c.name AS category_name
        FROM reminder_events e
        LEFT JOIN reminder_categories c ON c.id = e.category_id
        WHERE e.id = %s
    """, (event_id,))
    ev = cur.fetchone()
    if not ev:
        cur.close(); conn.close()
        return jsonify({"success": False, "error": "Event not found"}), 404
    ev = dict(ev)

    # Fetch recipients
    cur.execute("SELECT * FROM reminder_recipients WHERE event_id = %s", (event_id,))
    recipients = [dict(r) for r in cur.fetchall()]

    # Fetch template
    cur.execute("SELECT * FROM reminder_templates WHERE channel = 'email' LIMIT 1")
    email_tpl = cur.fetchone()
    if email_tpl:
        email_tpl = dict(email_tpl)
        # Decode URL-encoded content if present
        if "%0A" in email_tpl.get("body", ""):
            email_tpl["body"] = urllib.parse.unquote(email_tpl["body"])
        if email_tpl.get("subject") and "%0A" in email_tpl["subject"]:
            email_tpl["subject"] = urllib.parse.unquote(email_tpl["subject"])
    
    cur.execute("SELECT * FROM reminder_templates WHERE channel = 'whatsapp' LIMIT 1")
    wa_tpl    = cur.fetchone()

    cur.close(); conn.close()

    results = []

    def log_notif(event_id, ch, recipient, status, error=""):
        try:
            c = get_connection(); cu = c.cursor()
            cu.execute("""
                INSERT INTO reminder_notification_logs
                    (event_id, channel, recipient, status, error_msg, sent_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (event_id, ch, recipient, status, error or None,
                  datetime.now() if status == "sent" else None))
            c.commit(); cu.close(); c.close()
        except Exception:
            pass

    for r in recipients:
        rtype = r["type"]
        rval  = r["value"]

        if channel and rtype != channel:
            continue

        if rtype == "email" and email_tpl:
            subj = fill_template(email_tpl["subject"] or "", ev)
            body = fill_template(email_tpl["body"],    ev)
            ok, msg = send_email(rval, subj, body)
            status = "sent" if ok else "failed"
            log_notif(event_id, "email", rval, status, "" if ok else msg)

            # email_logs
            try:
                c = get_connection(); cu = c.cursor()
                cu.execute("""
                    INSERT INTO reminder_email_logs (to_email, subject, body, smtp_response, status)
                    VALUES (%s, %s, %s, %s, %s)
                """, (rval, subj, body, msg, status))
                c.commit(); cu.close(); c.close()
            except Exception:
                pass

            results.append({"channel": "email", "to": rval, "status": status})

        elif rtype == "whatsapp":
            # WhatsApp via Twilio — integrate karo jab Twilio credentials ho
            # For now, log as pending
            log_notif(event_id, "whatsapp", rval, "pending", "Twilio not configured")
            results.append({"channel": "whatsapp", "to": rval, "status": "pending", "note": "Configure Twilio in .env"})

    return jsonify({"success": True, "results": results})