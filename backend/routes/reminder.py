import re
import logging
from flask import Blueprint, jsonify, request
from datetime import datetime
from db import get_connection, get_cursor
from auth_utils import login_required

reminders_bp = Blueprint("reminders", __name__)
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [ReminderAPI] %(levelname)s: %(message)s')

class Validators:
    EMAIL_REGEX = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

    @staticmethod
    def validate_email(email):
        email = str(email).strip() if email else ""
        if not email: return False, "Email cannot be empty"
        if not re.match(Validators.EMAIL_REGEX, email): return False, f"Invalid email: {email}"
        return True, email

    @staticmethod
    def validate_title(title):
        title = str(title).strip() if title else ""
        if not title: return False, "Title is required"
        if len(title) < 3: return False, "Title too short (min 3 chars)"
        return True, title

    @staticmethod
    def validate_priority(priority):
        priority = str(priority).lower().strip() if priority else "medium"
        if priority not in ['low', 'medium', 'high']: return False, "Priority must be low/medium/high"
        return True, priority

    @staticmethod
    def validate_status(status):
        status = str(status).lower().strip() if status else "upcoming"
        if status not in ['upcoming', 'completed', 'cancelled']: return False, "Invalid status"
        return True, status

    @staticmethod
    def validate_date(date_str):
        if not date_str: return False, "Date is required"
        try:
            parsed = datetime.strptime(str(date_str), '%Y-%m-%d')
            return True, parsed.date()
        except ValueError:
            return False, "Invalid date format (use YYYY-MM-DD)"

    @staticmethod
    def validate_time(time_str):
        if not time_str: return False, "Time is required"
        try:
            parsed = datetime.strptime(str(time_str), '%H:%M')
            return True, parsed.time()
        except ValueError:
            return False, "Invalid time format (use HH:MM)"


def success_response(data=None, message="Success", status_code=200):
    return jsonify({"success": True, "message": message, "data": data}), status_code

def error_response(error, message=None, status_code=400):
    return jsonify({"success": False, "error": error, "message": message}), status_code


# ── CATEGORIES ────────────────────────────────────────────────
@reminders_bp.route("/reminders/categories", methods=["GET"])
@login_required
def get_categories():
    try:
        conn = get_connection(); cur = get_cursor(conn)
        cur.execute("SELECT id, name, created_at FROM reminder_categories ORDER BY name ASC")
        # Return plain array so frontend can do: categories = await res.json()
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        logger.info(f"Fetched {len(rows)} categories")
        return jsonify(rows)
    except Exception as e:
        logger.error(f"Error fetching categories: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/categories", methods=["POST"])
@login_required
def create_category():
    try:
        data = request.json or {}
        name = data.get("name", "").strip()
        if not name: return error_response("Validation error", "Category name required")
        conn = get_connection(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO reminder_categories (name)
            VALUES (%s)
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id, name
        """, (name,))
        result = cur.fetchone()
        conn.commit(); cur.close(); conn.close()
        logger.info(f"Category created: {name}")
        return success_response({"id": result[0], "name": result[1]}, "Category created", 201)
    except Exception as e:
        logger.error(f"Error creating category: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/categories/<int:category_id>", methods=["DELETE"])
@login_required
def delete_category(category_id):
    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("DELETE FROM reminder_categories WHERE id = %s", (category_id,))
        conn.commit(); cur.close(); conn.close()
        return success_response(message="Category deleted")
    except Exception as e:
        return error_response("Database error", str(e), 500)


# ── SUBCATEGORIES ─────────────────────────────────────────────
@reminders_bp.route("/reminders/subcategories", methods=["GET"])
@login_required
def get_subcategories():
    try:
        conn = get_connection(); cur = get_cursor(conn)
        cur.execute("""
            SELECT sc.id, sc.name, sc.category_id, c.name as category_name, sc.created_at
            FROM reminder_subcategories sc
            LEFT JOIN reminder_categories c ON c.id = sc.category_id
            ORDER BY c.name, sc.name
        """)
        # Return plain array so frontend can do: subcategories = await res.json()
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify(rows)
    except Exception as e:
        logger.error(f"Error fetching subcategories: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/subcategories", methods=["POST"])
@login_required
def create_subcategory():
    try:
        data = request.json or {}
        name = data.get("name", "").strip()
        category_id = data.get("category_id")
        if not name: return error_response("Validation error", "Subcategory name required")
        conn = get_connection(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO reminder_subcategories (name, category_id)
            VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET category_id = EXCLUDED.category_id
            RETURNING id, name
        """, (name, category_id))
        result = cur.fetchone()
        conn.commit(); cur.close(); conn.close()
        logger.info(f"Subcategory created: {name}")
        return success_response({"id": result[0], "name": result[1]}, "Subcategory created", 201)
    except Exception as e:
        logger.error(f"Error creating subcategory: {e}")
        return error_response("Database error", str(e), 500)


# ── EVENTS ────────────────────────────────────────────────────
@reminders_bp.route("/reminders/events", methods=["GET"])
@login_required
def get_events():
    try:
        conn = get_connection(); cur = get_cursor(conn)
        cur.execute("""
            SELECT e.*, c.name AS category_name, sc.name AS subcategory_name
            FROM reminder_events e
            LEFT JOIN reminder_categories c ON c.id = e.category_id
            LEFT JOIN reminder_subcategories sc ON sc.id = e.subcategory_id
            ORDER BY e.event_date ASC
        """)

        events = []
        for r in cur.fetchall():
            ev = dict(r)
            # Fix: convert time/date objects to strings for JSON serialization
            if ev.get("event_time") is not None:
                ev["event_time"] = str(ev["event_time"])
            if ev.get("event_date") is not None:
                ev["event_date"] = str(ev["event_date"])
            if ev.get("start_dt") is not None:
                ev["start_dt"] = ev["start_dt"].isoformat()
            if ev.get("end_dt") is not None:
                ev["end_dt"] = ev["end_dt"].isoformat()
            if ev.get("created_at") is not None:
                ev["created_at"] = ev["created_at"].isoformat()
            if ev.get("updated_at") is not None:
                ev["updated_at"] = ev["updated_at"].isoformat()
            events.append(ev)

        for ev in events:
            try:
                cur.execute("SELECT id, remind_at, label, is_sent, sent_at FROM reminder_schedules WHERE event_id = %s ORDER BY remind_at ASC", (ev["id"],))
                ev["reminders"] = [dict(r) for r in cur.fetchall()]
            except: ev["reminders"] = []
            try:
                cur.execute("SELECT id, type, value FROM reminder_recipients WHERE event_id = %s", (ev["id"],))
                ev["recipients"] = [dict(r) for r in cur.fetchall()]
            except: ev["recipients"] = []

        cur.close(); conn.close()
        logger.info(f"Fetched {len(events)} events")
        return success_response(events)
    except Exception as e:
        logger.error(f"Error fetching events: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/events", methods=["POST"])
@login_required
def create_event():
    try:
        data = request.json or {}

        valid, title = Validators.validate_title(data.get("title", ""))
        if not valid: return error_response("Validation error", title)

        valid, priority = Validators.validate_priority(data.get("priority"))
        if not valid: return error_response("Validation error", priority)

        valid, status = Validators.validate_status(data.get("status"))
        if not valid: return error_response("Validation error", status)

        valid, event_date = Validators.validate_date(data.get("event_date", ""))
        if not valid: return error_response("Validation error", event_date)

        valid, event_time = Validators.validate_time(data.get("event_time", ""))
        if not valid: return error_response("Validation error", event_time)

        emails = data.get("emails", [])
        validated_emails = []
        for email in emails:
            valid, val = Validators.validate_email(email)
            if not valid: return error_response("Validation error", f"Invalid email: {val}")
            validated_emails.append(val)

        phones = data.get("phones", [])
        validated_phones = [str(p).strip() for p in phones if str(p).strip()]

        conn = get_connection(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO reminder_events (title, category_id, subcategory_id, description, event_date, event_time, priority, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """, (title, data.get("category_id"), data.get("subcategory_id"), data.get("description", ""), event_date, event_time, priority, status))
        event_id = cur.fetchone()[0]

        for reminder in data.get("reminders", []):
            if reminder.get("remind_at"):
                cur.execute("INSERT INTO reminder_schedules (event_id, remind_at, label) VALUES (%s, %s, %s)",
                    (event_id, reminder["remind_at"], reminder.get("label", "")))

        for email in validated_emails:
            cur.execute("INSERT INTO reminder_recipients (event_id, type, value) VALUES (%s, %s, %s)",
                (event_id, "email", email))

        for phone in validated_phones:
            cur.execute("INSERT INTO reminder_recipients (event_id, type, value) VALUES (%s, %s, %s)",
                (event_id, "whatsapp", phone))

        conn.commit(); cur.close(); conn.close()
        logger.info(f"Event created: {title} (ID: {event_id})")
        return success_response({"id": event_id, "title": title}, "Event created successfully", 201)
    except Exception as e:
        logger.error(f"Error creating event: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/events/<int:event_id>", methods=["PUT"])
@login_required
def update_event(event_id):
    try:
        data = request.json or {}
        conn = get_connection(); cur = conn.cursor()
        updates = []; params = []

        if "title" in data:
            valid, value = Validators.validate_title(data["title"])
            if not valid: return error_response("Validation error", value)
            updates.append("title = %s"); params.append(value)

        if "status" in data:
            valid, value = Validators.validate_status(data["status"])
            if not valid: return error_response("Validation error", value)
            updates.append("status = %s"); params.append(value)

        if "description" in data:
            updates.append("description = %s"); params.append(data["description"])

        if "category_id" in data:
            updates.append("category_id = %s"); params.append(data["category_id"])

        if "subcategory_id" in data:
            updates.append("subcategory_id = %s"); params.append(data["subcategory_id"])

        if "event_date" in data:
            updates.append("event_date = %s"); params.append(data["event_date"])

        if "event_time" in data:
            updates.append("event_time = %s"); params.append(data["event_time"])

        if "priority" in data:
            updates.append("priority = %s"); params.append(data["priority"])

        if not updates: return error_response("Validation error", "No fields to update")
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(event_id)
        cur.execute(f"UPDATE reminder_events SET {', '.join(updates)} WHERE id = %s", params)

        # Reminder schedules: replace only the ones not yet sent, so edits
        # don't erase the history of reminders that already fired.
        if "reminders" in data:
            cur.execute("DELETE FROM reminder_schedules WHERE event_id = %s AND is_sent = FALSE", (event_id,))
            for reminder in data["reminders"]:
                if reminder.get("remind_at"):
                    cur.execute("INSERT INTO reminder_schedules (event_id, remind_at, label) VALUES (%s, %s, %s)",
                        (event_id, reminder["remind_at"], reminder.get("label", "")))

        conn.commit(); cur.close(); conn.close()
        return success_response(message="Event updated")
    except Exception as e:
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/events/<int:event_id>", methods=["DELETE"])
@login_required
def delete_event(event_id):
    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("DELETE FROM reminder_events WHERE id = %s", (event_id,))
        conn.commit(); cur.close(); conn.close()
        return success_response(message="Event deleted")
    except Exception as e:
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/events/<int:event_id>/recipients", methods=["POST"])
@login_required
def add_recipient(event_id):
    try:
        data = request.json or {}
        rtype = data.get("type", "").lower()
        value = data.get("value", "").strip()
        if rtype not in ["email", "whatsapp"]: return error_response("Validation error", "Type must be email or whatsapp")
        if rtype == "email":
            valid, value = Validators.validate_email(value)
            if not valid: return error_response("Validation error", value)
        conn = get_connection(); cur = conn.cursor()
        cur.execute("INSERT INTO reminder_recipients (event_id, type, value) VALUES (%s, %s, %s) RETURNING id", (event_id, rtype, value))
        rid = cur.fetchone()[0]
        conn.commit(); cur.close(); conn.close()
        return success_response({"id": rid, "type": rtype, "value": value}, "Recipient added", 201)
    except Exception as e:
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/recipients/<int:recipient_id>", methods=["DELETE"])
@login_required
def delete_recipient(recipient_id):
    try:
        conn = get_connection(); cur = conn.cursor()
        cur.execute("DELETE FROM reminder_recipients WHERE id = %s", (recipient_id,))
        conn.commit(); cur.close(); conn.close()
        return success_response(message="Recipient deleted")
    except Exception as e:
        return error_response("Database error", str(e), 500)


# ── TEMPLATES ─────────────────────────────────────────────────
@reminders_bp.route("/reminders/templates", methods=["GET"])
@login_required
def get_templates():
    try:
        conn = get_connection(); cur = get_cursor(conn)
        cur.execute("SELECT id, channel, subject, body, updated_at FROM reminder_templates ORDER BY channel")
        rows = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify(rows)
    except Exception as e:
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/templates/<channel>", methods=["PUT"])
@login_required
def save_template(channel):
    try:
        if channel not in ["email", "whatsapp", "sms"]:
            return error_response("Validation error", "Invalid channel")
        data = request.json or {}
        body = data.get("body", "").strip()
        if not body: return error_response("Validation error", "Template body required")
        conn = get_connection(); cur = conn.cursor()
        cur.execute("""
            INSERT INTO reminder_templates (channel, subject, body, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (channel) DO UPDATE SET subject=EXCLUDED.subject, body=EXCLUDED.body, updated_at=CURRENT_TIMESTAMP
        """, (channel, data.get("subject", ""), body))
        conn.commit(); cur.close(); conn.close()
        return success_response(message=f"Template updated")
    except Exception as e:
        return error_response("Database error", str(e), 500)


# ── LOGS ──────────────────────────────────────────────────────
@reminders_bp.route("/reminders/logs", methods=["GET"])
@login_required
def get_logs():
    try:
        conn = get_connection(); cur = get_cursor(conn)
        cur.execute("""
            SELECT nl.id, nl.event_id, nl.channel, nl.recipient, nl.status,
                   nl.error_msg, nl.sent_at, nl.created_at, e.title AS event_title
            FROM reminder_notification_logs nl
            LEFT JOIN reminder_events e ON e.id = nl.event_id
            ORDER BY nl.created_at DESC LIMIT 100
        """)
        logs = [dict(r) for r in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify(logs)
    except Exception as e:
        return error_response("Database error", str(e), 500)


# ── STATUS & STATS ────────────────────────────────────────────
@reminders_bp.route("/reminders/status", methods=["GET"])
@login_required
def status():
    return success_response({"service": "reminder", "status": "ok", "timestamp": datetime.now().isoformat()})


@reminders_bp.route("/reminders/stats", methods=["GET"])
@login_required
def get_stats():
    try:
        conn = get_connection(); cur = get_cursor(conn)
        cur.execute("SELECT COUNT(*) as c FROM reminder_events WHERE status='upcoming' AND event_date >= CURRENT_DATE")
        upcoming = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) as c FROM reminder_events WHERE event_date = CURRENT_DATE")
        today = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) as c FROM reminder_notification_logs WHERE status='sent' AND DATE(created_at)=CURRENT_DATE")
        sent_today = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) as c FROM reminder_events WHERE status='upcoming' AND event_date < CURRENT_DATE")
        missed = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) as c FROM reminder_notification_logs WHERE status='failed' AND DATE(created_at)=CURRENT_DATE")
        failed_today = cur.fetchone()['c']
        cur.close(); conn.close()
        return jsonify({"upcoming": upcoming, "today": today, "sent_today": sent_today, "missed": missed, "failed_today": failed_today})
    except Exception as e:
        return error_response("Database error", str(e), 500)