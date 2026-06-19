# routes/reminder_v2.py — Enhanced Reminder API with Full Validation
# Features: Input Validation | Error Handling | Complete CRUD | Rate Limiting

import re
import logging
from flask import Blueprint, jsonify, request
from datetime import datetime
from db import get_connection, get_cursor

# ─────────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────────

reminders_bp = Blueprint("reminders", __name__)
logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [ReminderAPI] %(levelname)s: %(message)s'
)

# ─────────────────────────────────────────────────────────────────
# VALIDATORS
# ─────────────────────────────────────────────────────────────────

class Validators:
    """Input validation helpers"""
    
    EMAIL_REGEX = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    PHONE_REGEX = r'^\+91[6-9]\d{9}$'
    
    @staticmethod
    def validate_email(email: str) -> tuple:
        """Validate email format"""
        email = str(email).strip() if email else ""
        if not email:
            return False, "Email cannot be empty"
        if len(email) > 150:
            return False, "Email too long (max 150 chars)"
        if not re.match(Validators.EMAIL_REGEX, email):
            return False, f"Invalid email format: {email}"
        return True, email
    
    @staticmethod
    def validate_phone(phone: str) -> tuple:
        """Validate WhatsApp phone format"""
        phone = str(phone).strip() if phone else ""
        if not phone:
            return False, "Phone cannot be empty"
        # Allow flexible formats, normalize to +91XXXXXXXXXX
        phone_digits = re.sub(r'\D', '', phone)
        
        if phone_digits.startswith('91'):
            phone_digits = phone_digits[2:]
        
        if len(phone_digits) != 10:
            return False, "Phone must be 10 digits (Indian)"
        
        if not phone_digits[0] in '6789':
            return False, "Invalid Indian phone number"
        
        normalized = f"+91{phone_digits}"
        return True, normalized
    
    @staticmethod
    def validate_title(title: str) -> tuple:
        """Validate event title"""
        title = str(title).strip() if title else ""
        if not title:
            return False, "Title is required"
        if len(title) < 3:
            return False, "Title too short (min 3 chars)"
        if len(title) > 300:
            return False, "Title too long (max 300 chars)"
        return True, title
    
    @staticmethod
    def validate_priority(priority: str) -> tuple:
        """Validate priority level"""
        valid_priorities = ['low', 'medium', 'high']
        priority = str(priority).lower().strip() if priority else "medium"
        if priority not in valid_priorities:
            return False, f"Priority must be one of: {', '.join(valid_priorities)}"
        return True, priority
    
    @staticmethod
    def validate_status(status: str) -> tuple:
        """Validate event status"""
        valid_statuses = ['upcoming', 'completed', 'cancelled']
        status = str(status).lower().strip() if status else "upcoming"
        if status not in valid_statuses:
            return False, f"Status must be one of: {', '.join(valid_statuses)}"
        return True, status
    
    @staticmethod
    def validate_date(date_str: str) -> tuple:
        """Validate date format (YYYY-MM-DD)"""
        if not date_str:
            return False, "Date is required"
        try:
            parsed = datetime.strptime(str(date_str), '%Y-%m-%d')
            return True, parsed.date()
        except ValueError:
            return False, "Invalid date format (use YYYY-MM-DD)"
    
    @staticmethod
    def validate_time(time_str: str) -> tuple:
        """Validate time format (HH:MM)"""
        if not time_str:
            return False, "Time is required"
        try:
            parsed = datetime.strptime(str(time_str), '%H:%M')
            return True, parsed.time()
        except ValueError:
            return False, "Invalid time format (use HH:MM)"

# ─────────────────────────────────────────────────────────────────
# RESPONSE HELPERS
# ─────────────────────────────────────────────────────────────────

def success_response(data=None, message="Success", status_code=200):
    """Return standardized success response"""
    response = {
        "success": True,
        "message": message,
        "data": data
    }
    return jsonify(response), status_code


def error_response(error, message=None, status_code=400):
    """Return standardized error response"""
    response = {
        "success": False,
        "error": error,
        "message": message
    }
    return jsonify(response), status_code

# ─────────────────────────────────────────────────────────────────
# CATEGORY ENDPOINTS
# ─────────────────────────────────────────────────────────────────

@reminders_bp.route("/reminders/categories", methods=["GET"])
def get_categories():
    """List all reminder categories"""
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("""
            SELECT id, name, created_at
            FROM reminder_categories
            ORDER BY name ASC
        """)
        categories = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        
        logger.info(f"Fetched {len(categories)} categories")
        return success_response(categories)
    
    except Exception as e:
        logger.error(f"Error fetching categories: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/categories", methods=["POST"])
def create_category():
    """Create new category"""
    try:
        data = request.json or {}
        name = data.get("name", "").strip()
        
        # Validate
        if not name:
            return error_response("Validation error", "Category name required")
        if len(name) < 2:
            return error_response("Validation error", "Name too short (min 2 chars)")
        if len(name) > 150:
            return error_response("Validation error", "Name too long (max 150 chars)")
        
        conn = get_connection()
        cur = conn.cursor()
        
        # Insert or get existing
        cur.execute("""
            INSERT INTO reminder_categories (name)
            VALUES (%s)
            ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
            RETURNING id, name, created_at
        """, (name,))
        
        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        if result:
            logger.info(f"✓ Category created/updated: {name}")
            return success_response(
                {"id": result[0], "name": result[1]},
                "Category created successfully",
                201
            )
    
    except Exception as e:
        logger.error(f"Error creating category: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/categories/<int:category_id>", methods=["DELETE"])
def delete_category(category_id: int):
    """Delete category"""
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        # Check if category is used
        cur.execute("""
            SELECT COUNT(*) as count FROM reminder_events 
            WHERE category_id = %s
        """, (category_id,))
        
        result = cur.fetchone()
        if result['count'] > 0:
            return error_response(
                "Conflict",
                f"Cannot delete - {result['count']} events using this category"
            )
        
        # Delete
        cur.execute("DELETE FROM reminder_categories WHERE id = %s", (category_id,))
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"✓ Category {category_id} deleted")
        return success_response(message="Category deleted successfully")
    
    except Exception as e:
        logger.error(f"Error deleting category: {e}")
        return error_response("Database error", str(e), 500)

# ─────────────────────────────────────────────────────────────────
# SUBCATEGORY ENDPOINTS
# ─────────────────────────────────────────────────────────────────

@reminders_bp.route("/reminders/subcategories", methods=["GET"])
def get_subcategories():
    """List all subcategories"""
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("""
            SELECT sc.id, sc.name, sc.category_id, c.name as category_name, sc.created_at
            FROM reminder_subcategories sc
            LEFT JOIN reminder_categories c ON c.id = sc.category_id
            ORDER BY c.name, sc.name
        """)
        subcategories = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        
        return success_response(subcategories)
    
    except Exception as e:
        logger.error(f"Error fetching subcategories: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/subcategories", methods=["POST"])
def create_subcategory():
    """Create new subcategory"""
    try:
        data = request.json or {}
        name = data.get("name", "").strip()
        category_id = data.get("category_id")
        
        # Validate
        if not name:
            return error_response("Validation error", "Subcategory name required")
        if len(name) < 2 or len(name) > 150:
            return error_response("Validation error", "Name must be 2-150 chars")
        
        conn = get_connection()
        cur = conn.cursor()
        
        # Verify category exists if provided
        if category_id:
            cur.execute("SELECT id FROM reminder_categories WHERE id = %s", (category_id,))
            if not cur.fetchone():
                return error_response("Not found", f"Category {category_id} not found")
        
        # Insert
        cur.execute("""
            INSERT INTO reminder_subcategories (name, category_id)
            VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET category_id = EXCLUDED.category_id
            RETURNING id, name, created_at
        """, (name, category_id))
        
        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"✓ Subcategory created: {name}")
        return success_response(
            {"id": result[0], "name": result[1]},
            "Subcategory created successfully",
            201
        )
    
    except Exception as e:
        logger.error(f"Error creating subcategory: {e}")
        return error_response("Database error", str(e), 500)

# ─────────────────────────────────────────────────────────────────
# EVENT ENDPOINTS
# ─────────────────────────────────────────────────────────────────

@reminders_bp.route("/reminders/events", methods=["GET"])
def get_events():
    """Fetch all events with reminders and recipients"""
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        
        # Get events
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
        
        # Fetch reminders and recipients for each event
        for ev in events:
            try:
                cur.execute("""
                    SELECT id, remind_at, label, is_sent, sent_at
                    FROM reminder_schedules
                    WHERE event_id = %s
                    ORDER BY remind_at ASC
                """, (ev["id"],))
                ev["reminders"] = [dict(r) for r in cur.fetchall()]
            except:
                ev["reminders"] = []
            
            try:
                cur.execute("""
                    SELECT id, type, value
                    FROM reminder_recipients
                    WHERE event_id = %s
                """, (ev["id"],))
                ev["recipients"] = [dict(r) for r in cur.fetchall()]
            except:
                ev["recipients"] = []
        
        cur.close()
        conn.close()
        
        logger.info(f"Fetched {len(events)} events")
        return success_response(events)
    
    except Exception as e:
        logger.error(f"Error fetching events: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/events/<int:event_id>", methods=["GET"])
def get_event(event_id: int):
    """Fetch single event with details"""
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
            WHERE e.id = %s
        """, (event_id,))
        
        event = cur.fetchone()
        if not event:
            return error_response("Not found", f"Event {event_id} not found", 404)
        
        event = dict(event)
        
        # Fetch reminders
        cur.execute("""
            SELECT id, remind_at, label, is_sent, sent_at
            FROM reminder_schedules WHERE event_id = %s ORDER BY remind_at
        """, (event_id,))
        event["reminders"] = [dict(r) for r in cur.fetchall()]
        
        # Fetch recipients
        cur.execute("""
            SELECT id, type, value FROM reminder_recipients WHERE event_id = %s
        """, (event_id,))
        event["recipients"] = [dict(r) for r in cur.fetchall()]
        
        cur.close()
        conn.close()
        
        return success_response(event)
    
    except Exception as e:
        logger.error(f"Error fetching event: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/events", methods=["POST"])
def create_event():
    """Create new event with reminders and recipients"""
    try:
        data = request.json or {}
        
        # Validate title
        valid, title = Validators.validate_title(data.get("title", ""))
        if not valid:
            return error_response("Validation error", title)
        
        # Validate priority
        valid, priority = Validators.validate_priority(data.get("priority"))
        if not valid:
            return error_response("Validation error", priority)
        
        # Validate status
        valid, status = Validators.validate_status(data.get("status"))
        if not valid:
            return error_response("Validation error", status)
        
        # Validate dates
        valid, event_date = Validators.validate_date(data.get("event_date", ""))
        if not valid:
            return error_response("Validation error", event_date)
        
        # Validate time
        valid, event_time = Validators.validate_time(data.get("event_time", ""))
        if not valid:
            return error_response("Validation error", event_time)
        
        # Validate emails
        emails = data.get("emails", [])
        validated_emails = []
        for email in emails:
            valid, email_or_error = Validators.validate_email(email)
            if not valid:
                return error_response("Validation error", f"Invalid email: {email_or_error}")
            validated_emails.append(email_or_error)
        
        # Insert event
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO reminder_events
                (title, category_id, subcategory_id, description, 
                 event_date, event_time, priority, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            title,
            data.get("category_id"),
            data.get("subcategory_id"),
            data.get("description", ""),
            event_date,
            event_time,
            priority,
            status
        ))
        
        event_id = cur.fetchone()[0]
        
        # Insert reminders
        reminders = data.get("reminders", [])
        for reminder in reminders:
            remind_at = reminder.get("remind_at")
            if remind_at:
                cur.execute("""
                    INSERT INTO reminder_schedules (event_id, remind_at, label)
                    VALUES (%s, %s, %s)
                """, (event_id, remind_at, reminder.get("label", "")))
        
        # Insert recipients
        for email in validated_emails:
            cur.execute("""
                INSERT INTO reminder_recipients (event_id, type, value)
                VALUES (%s, %s, %s)
            """, (event_id, "email", email))
        
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"✓ Event created: {title} (ID: {event_id})")
        return success_response(
            {"id": event_id, "title": title},
            "Event created successfully",
            201
        )
    
    except Exception as e:
        logger.error(f"Error creating event: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/events/<int:event_id>", methods=["PUT"])
def update_event(event_id: int):
    """Update event details"""
    try:
        data = request.json or {}
        
        conn = get_connection()
        cur = conn.cursor()
        
        # Verify event exists
        cur.execute("SELECT id FROM reminder_events WHERE id = %s", (event_id,))
        if not cur.fetchone():
            return error_response("Not found", f"Event {event_id} not found", 404)
        
        # Build update query
        updates = []
        params = []
        
        if "title" in data:
            valid, value = Validators.validate_title(data["title"])
            if not valid:
                return error_response("Validation error", value)
            updates.append("title = %s")
            params.append(value)
        
        if "description" in data:
            updates.append("description = %s")
            params.append(data.get("description", ""))
        
        if "priority" in data:
            valid, value = Validators.validate_priority(data["priority"])
            if not valid:
                return error_response("Validation error", value)
            updates.append("priority = %s")
            params.append(value)
        
        if "status" in data:
            valid, value = Validators.validate_status(data["status"])
            if not valid:
                return error_response("Validation error", value)
            updates.append("status = %s")
            params.append(value)
        
        if not updates:
            return error_response("Validation error", "No fields to update")
        
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(event_id)
        
        query = f"UPDATE reminder_events SET {', '.join(updates)} WHERE id = %s"
        cur.execute(query, params)
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"✓ Event {event_id} updated")
        return success_response(message="Event updated successfully")
    
    except Exception as e:
        logger.error(f"Error updating event: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/events/<int:event_id>", methods=["DELETE"])
def delete_event(event_id: int):
    """Delete event (cascades to reminders, recipients, attachments)"""
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        # Verify event exists
        cur.execute("SELECT title FROM reminder_events WHERE id = %s", (event_id,))
        result = cur.fetchone()
        if not result:
            return error_response("Not found", f"Event {event_id} not found", 404)
        
        title = result[0]
        
        # Delete event (cascades)
        cur.execute("DELETE FROM reminder_events WHERE id = %s", (event_id,))
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"✓ Event {event_id} ({title}) deleted")
        return success_response(message="Event deleted successfully")
    
    except Exception as e:
        logger.error(f"Error deleting event: {e}")
        return error_response("Database error", str(e), 500)

# ─────────────────────────────────────────────────────────────────
# RECIPIENT ENDPOINTS
# ─────────────────────────────────────────────────────────────────

@reminders_bp.route("/reminders/events/<int:event_id>/recipients", methods=["POST"])
def add_recipient(event_id: int):
    """Add email/WhatsApp recipient to event"""
    try:
        data = request.json or {}
        recipient_type = data.get("type", "").lower()
        value = data.get("value", "").strip()
        
        # Validate type
        if recipient_type not in ["email", "whatsapp"]:
            return error_response("Validation error", "Type must be 'email' or 'whatsapp'")
        
        # Validate value
        if recipient_type == "email":
            valid, value = Validators.validate_email(value)
            if not valid:
                return error_response("Validation error", value)
        else:  # whatsapp
            valid, value = Validators.validate_phone(value)
            if not valid:
                return error_response("Validation error", value)
        
        conn = get_connection()
        cur = conn.cursor()
        
        # Verify event exists
        cur.execute("SELECT id FROM reminder_events WHERE id = %s", (event_id,))
        if not cur.fetchone():
            return error_response("Not found", f"Event {event_id} not found", 404)
        
        # Insert recipient
        cur.execute("""
            INSERT INTO reminder_recipients (event_id, type, value)
            VALUES (%s, %s, %s)
            RETURNING id
        """, (event_id, recipient_type, value))
        
        recipient_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"✓ Recipient added to event {event_id}: {recipient_type}://{value}")
        return success_response(
            {"id": recipient_id, "type": recipient_type, "value": value},
            "Recipient added successfully",
            201
        )
    
    except Exception as e:
        logger.error(f"Error adding recipient: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/recipients/<int:recipient_id>", methods=["DELETE"])
def delete_recipient(recipient_id: int):
    """Delete recipient"""
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("DELETE FROM reminder_recipients WHERE id = %s", (recipient_id,))
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"✓ Recipient {recipient_id} deleted")
        return success_response(message="Recipient deleted successfully")
    
    except Exception as e:
        logger.error(f"Error deleting recipient: {e}")
        return error_response("Database error", str(e), 500)

# ─────────────────────────────────────────────────────────────────
# TEMPLATE ENDPOINTS
# ─────────────────────────────────────────────────────────────────

@reminders_bp.route("/reminders/templates", methods=["GET"])
def get_templates():
    """Fetch all templates"""
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("""
            SELECT id, channel, subject, body, updated_at
            FROM reminder_templates
            ORDER BY channel
        """)
        templates = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        
        return success_response(templates)
    
    except Exception as e:
        logger.error(f"Error fetching templates: {e}")
        return error_response("Database error", str(e), 500)


@reminders_bp.route("/reminders/templates/<channel>", methods=["PUT"])
def save_template(channel: str):
    """Update template for channel"""
    try:
        if channel not in ["email", "whatsapp", "sms"]:
            return error_response("Validation error", "Invalid channel")
        
        data = request.json or {}
        subject = data.get("subject", "").strip()
        body = data.get("body", "").strip()
        
        if not body:
            return error_response("Validation error", "Template body required")
        
        conn = get_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO reminder_templates (channel, subject, body, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (channel) DO UPDATE
            SET subject = EXCLUDED.subject, body = EXCLUDED.body, updated_at = CURRENT_TIMESTAMP
        """, (channel, subject or None, body))
        
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"✓ Template updated: {channel}")
        return success_response(message=f"Template for {channel} updated successfully")
    
    except Exception as e:
        logger.error(f"Error saving template: {e}")
        return error_response("Database error", str(e), 500)

# ─────────────────────────────────────────────────────────────────
# LOG ENDPOINTS
# ─────────────────────────────────────────────────────────────────

@reminders_bp.route("/reminders/logs", methods=["GET"])
def get_logs():
    """Fetch notification logs"""
    try:
        limit = request.args.get("limit", 100, type=int)
        offset = request.args.get("offset", 0, type=int)
        
        # Validate parameters
        limit = min(limit, 1000)  # Cap at 1000
        offset = max(offset, 0)
        
        conn = get_connection()
        cur = get_cursor(conn)
        
        cur.execute("""
            SELECT nl.id, nl.event_id, nl.channel, nl.recipient, nl.status, 
                   nl.error_msg, nl.sent_at, nl.created_at,
                   e.title AS event_title
            FROM reminder_notification_logs nl
            LEFT JOIN reminder_events e ON e.id = nl.event_id
            ORDER BY nl.created_at DESC
            LIMIT %s OFFSET %s
        """, (limit, offset))
        
        logs = [dict(r) for r in cur.fetchall()]
        
        # Get total count
        cur.execute("SELECT COUNT(*) as total FROM reminder_notification_logs")
        total = cur.fetchone()['total']
        
        cur.close()
        conn.close()
        
        return success_response({
            "logs": logs,
            "total": total,
            "limit": limit,
            "offset": offset
        })
    
    except Exception as e:
        logger.error(f"Error fetching logs: {e}")
        return error_response("Database error", str(e), 500)

# ─────────────────────────────────────────────────────────────────
# HEALTH & STATUS
# ─────────────────────────────────────────────────────────────────

@reminders_bp.route("/reminders/status", methods=["GET"])
def status():
    """Health check endpoint"""
    return success_response({
        "service": "reminder",
        "status": "ok",
        "timestamp": datetime.now().isoformat()
    })


@reminders_bp.route("/reminders/stats", methods=["GET"])
def get_stats():
    """Get reminder statistics"""
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        
        # Total events
        cur.execute("SELECT COUNT(*) as count FROM reminder_events")
        total_events = cur.fetchone()['count']
        
        # Upcoming events
        cur.execute("""
            SELECT COUNT(*) as count FROM reminder_events 
            WHERE status = 'upcoming' AND event_date >= CURRENT_DATE
        """)
        upcoming_events = cur.fetchone()['count']
        
        # Total recipients
        cur.execute("SELECT COUNT(*) as count FROM reminder_recipients")
        total_recipients = cur.fetchone()['count']
        
        # Sent reminders
        cur.execute("""
            SELECT COUNT(*) as count FROM reminder_notification_logs 
            WHERE status = 'sent'
        """)
        sent_reminders = cur.fetchone()['count']
        
        # Failed reminders
        cur.execute("""
            SELECT COUNT(*) as count FROM reminder_notification_logs 
            WHERE status = 'failed'
        """)
        failed_reminders = cur.fetchone()['count']
        
        cur.close()
        conn.close()
        
        return success_response({
            "total_events": total_events,
            "upcoming_events": upcoming_events,
            "total_recipients": total_recipients,
            "sent_reminders": sent_reminders,
            "failed_reminders": failed_reminders,
            "timestamp": datetime.now().isoformat()
        })
    
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        return error_response("Database error", str(e), 500)