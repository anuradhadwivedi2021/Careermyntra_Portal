# routes/reminder.py — Complete Reminder API (FIXED)

from flask import Blueprint, jsonify, request
from db import get_connection, get_cursor
import os

reminders_bp = Blueprint("reminders", __name__)

# ─── GET EVENTS ──────────────────────────────────────────────
@reminders_bp.route("/reminders/events", methods=["GET"])
def get_events():
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("""
            SELECT e.*, 
                   c.name AS category_name,
                   sc.name AS subcategory_name
            FROM reminder_events e
            LEFT JOIN reminder_categories c ON c.id = e.category_id
            LEFT JOIN reminder_subcategories sc ON sc.id = e.subcategory_id
            ORDER BY e.event_date ASC
        """)
        events = [dict(r) for r in cur.fetchall()]
        for ev in events:
            try:
                cur.execute("SELECT * FROM reminder_schedules WHERE event_id = %s ORDER BY remind_at", (ev["id"],))
                ev["reminders"] = [dict(r) for r in cur.fetchall()]
            except:
                ev["reminders"] = []
            try:
                cur.execute("SELECT * FROM reminder_recipients WHERE event_id = %s", (ev["id"],))
                ev["recipients"] = [dict(r) for r in cur.fetchall()]
            except:
                ev["recipients"] = []
        cur.close()
        conn.close()
        return jsonify(events)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── CREATE EVENT ────────────────────────────────────────────
@reminders_bp.route("/reminders/events", methods=["POST"])
def create_event():
    try:
        data = request.json or {}
        title = data.get("title", "").strip()
        if not title:
            return jsonify({"success": False, "error": "Title required"}), 400
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reminder_events
                (title, category_id, subcategory_id, description, event_date, event_time, priority, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            title,
            data.get("category_id"),
            data.get("subcategory_id"),
            data.get("description", ""),
            data.get("event_date"),
            data.get("event_time"),
            data.get("priority", "medium"),
            data.get("status", "upcoming")
        ))
        event_id = cur.fetchone()[0]
        for reminder in data.get("reminders", []):
            if reminder.get("remind_at"):
                cur.execute("""
                    INSERT INTO reminder_schedules (event_id, remind_at, label)
                    VALUES (%s, %s, %s)
                """, (event_id, reminder["remind_at"], reminder.get("label", "")))
        for email in data.get("emails", []):
            if email.strip():
                cur.execute("""
                    INSERT INTO reminder_recipients (event_id, type, value)
                    VALUES (%s, %s, %s)
                """, (event_id, "email", email.strip()))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "event_id": event_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── GET CATEGORIES ──────────────────────────────────────────
@reminders_bp.route("/reminders/categories", methods=["GET"])
def get_categories():
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("SELECT * FROM reminder_categories ORDER BY name")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── CREATE CATEGORY ─────────────────────────────────────────
@reminders_bp.route("/reminders/categories", methods=["POST"])
def create_category():
    try:
        data = request.json or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"success": False, "error": "Name required"}), 400
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reminder_categories (name)
            VALUES (%s)
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id, name
        """, (name,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "id": row[0], "name": row[1]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── GET SUBCATEGORIES ───────────────────────────────────────
@reminders_bp.route("/reminders/subcategories", methods=["GET"])
def get_subcategories():
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("SELECT * FROM reminder_subcategories ORDER BY name")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── CREATE SUBCATEGORY ──────────────────────────────────────
@reminders_bp.route("/reminders/subcategories", methods=["POST"])
def create_subcategory():
    try:
        data = request.json or {}
        name = data.get("name", "").strip()
        category_id = data.get("category_id")
        if not name:
            return jsonify({"success": False, "error": "Name required"}), 400
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reminder_subcategories (name, category_id)
            VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id, name
        """, (name, category_id))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "id": row[0], "name": row[1]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── GET TEMPLATES ───────────────────────────────────────────
@reminders_bp.route("/reminders/templates", methods=["GET"])
def get_templates():
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("SELECT * FROM reminder_templates ORDER BY channel")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── SAVE TEMPLATE ───────────────────────────────────────────
@reminders_bp.route("/reminders/templates/<channel>", methods=["PUT"])
def save_template(channel):
    try:
        if channel not in ("email", "whatsapp", "sms"):
            return jsonify({"success": False, "error": "Invalid channel"}), 400
        data = request.json or {}
        body = data.get("body", "").strip()
        if not body:
            return jsonify({"success": False, "error": "Body required"}), 400
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO reminder_templates (channel, subject, body, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (channel) DO UPDATE
            SET subject = EXCLUDED.subject, body = EXCLUDED.body, updated_at = CURRENT_TIMESTAMP
        """, (channel, data.get("subject", ""), body))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─── GET LOGS ────────────────────────────────────────────────
@reminders_bp.route("/reminders/logs", methods=["GET"])
def get_logs():
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("""
            SELECT nl.*, e.title AS event_title
            FROM reminder_notification_logs nl
            LEFT JOIN reminder_events e ON e.id = nl.event_id
            ORDER BY nl.created_at DESC
            LIMIT 100
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── STATUS ──────────────────────────────────────────────────
@reminders_bp.route("/reminders/status", methods=["GET"])
def status():
    return jsonify({"status": "ok", "service": "reminder"})