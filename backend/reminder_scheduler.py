# reminder_scheduler.py — Auto-send due reminders every minute

import os
import smtplib
import threading
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from db import get_connection, get_cursor


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


def send_email(to_email: str, subject: str, body: str) -> tuple:
    try:
        sender   = os.getenv("MONITOR_EMAIL", "")
        password = os.getenv("MONITOR_EMAIL_PASSWORD", "")
        if not sender or not password:
            return False, "Email credentials not configured"
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


def fire_due_reminders():
    now = datetime.now()

    # ── Step 1: fetch due schedules — fresh connection ──
    due = []
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("""
            SELECT rs.id AS schedule_id, rs.event_id, rs.label,
                   e.title, e.event_date, e.event_time, e.description,
                   c.name AS category_name
            FROM   reminder_schedules rs
            JOIN   reminder_events e ON e.id = rs.event_id
            LEFT JOIN reminder_categories c ON c.id = e.category_id
            WHERE  rs.is_sent = FALSE
              AND  rs.remind_at <= %s
        """, (now,))
        due = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[ReminderScheduler] ERROR fetching schedules: {e}")
        try: conn.rollback(); conn.close()
        except: pass
        return

    if not due:
        return

    # ── Step 2: load email template — fresh connection ──
    email_tpl = None
    try:
        conn = get_connection()
        cur  = get_cursor(conn)
        cur.execute("SELECT subject, body FROM reminder_templates WHERE channel = 'email' LIMIT 1")
        row = cur.fetchone()
        if row:
            email_tpl = dict(row)
            # Decode URL-encoded content if present
            if "%0A" in email_tpl.get("body", ""):
                email_tpl["body"] = urllib.parse.unquote(email_tpl["body"])
            if email_tpl.get("subject") and "%0A" in email_tpl["subject"]:
                email_tpl["subject"] = urllib.parse.unquote(email_tpl["subject"])
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[ReminderScheduler] ERROR fetching template: {e}")
        try: conn.rollback(); conn.close()
        except: pass

    # ── Step 3: process each due schedule ──
    for sched in due:
        schedule_id = sched["schedule_id"]
        event_id    = sched["event_id"]
        label       = sched.get("label", "")

        # fetch recipients — fresh connection
        recipients = []
        try:
            conn = get_connection()
            cur  = get_cursor(conn)
            cur.execute("SELECT type, value FROM reminder_recipients WHERE event_id = %s", (event_id,))
            recipients = [dict(r) for r in cur.fetchall()]
            cur.close()
            conn.close()
        except Exception as e:
            print(f"[ReminderScheduler] ERROR fetching recipients: {e}")
            try: conn.rollback(); conn.close()
            except: pass
            continue

        # send to each recipient
        for recip in recipients:
            rtype = recip["type"]
            rval  = recip["value"]
            status = "pending"
            err    = ""

            if rtype == "email" and email_tpl:
                subj = fill_template(email_tpl.get("subject", ""), sched, label)
                body = fill_template(email_tpl.get("body", ""),    sched, label)
                ok, msg = send_email(rval, subj, body)
                status = "sent" if ok else "failed"
                err    = "" if ok else msg

                # log to email_logs — fresh connection
                try:
                    conn = get_connection()
                    cur  = conn.cursor()
                    cur.execute("""
                        INSERT INTO reminder_email_logs
                            (to_email, subject, body, smtp_response, status)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (rval, subj, body, msg, status))
                    conn.commit()
                    cur.close()
                    conn.close()
                except Exception as e:
                    try: conn.rollback(); conn.close()
                    except: pass

            elif rtype == "whatsapp":
                status = "pending"
                err    = "Twilio not configured"

            # log to notification_logs — fresh connection
            try:
                conn = get_connection()
                cur  = conn.cursor()
                cur.execute("""
                    INSERT INTO reminder_notification_logs
                        (event_id, channel, recipient, status, error_msg, sent_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    event_id, rtype, rval, status,
                    err or None,
                    now if status == "sent" else None
                ))
                conn.commit()
                cur.close()
                conn.close()
            except Exception as e:
                try: conn.rollback(); conn.close()
                except: pass

        # mark schedule as sent — fresh connection
        try:
            conn = get_connection()
            cur  = conn.cursor()
            cur.execute("""
                UPDATE reminder_schedules
                SET is_sent = TRUE, sent_at = %s
                WHERE id = %s
            """, (now, schedule_id))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            print(f"[ReminderScheduler] ERROR marking sent: {e}")
            try: conn.rollback(); conn.close()
            except: pass


# ── START ──
_scheduler = None
_lock = threading.Lock()

def start_reminder_scheduler():
    global _scheduler
    with _lock:
        if _scheduler and _scheduler.running:
            return
        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(
            fire_due_reminders,
            trigger=IntervalTrigger(minutes=1),
            id="reminder_fire",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=60
        )
        _scheduler.start()
        print("[ReminderScheduler] Started — checking every 60 seconds")