# reminder_scheduler_v2.py — Production-Grade Email Automation
# Features: Retry Logic | Email Validation | Comprehensive Logging | Rate Limiting

import os
import re
import smtplib
import threading
import urllib.parse
import logging
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Tuple, Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from db import get_connection, get_cursor

# ─────────────────────────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [ReminderScheduler] %(levelname)s: %(message)s',
    handlers=[
       logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [ReminderScheduler] %(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler()  # Only console, no file
    ]
),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────

class Config:
    """Centralized configuration"""
    MAX_RETRIES = 3
    RETRY_DELAY_MINUTES = 5  # First retry after 5 min
    EXPONENTIAL_BASE = 2  # Each retry: delay * 2
    BATCH_SIZE = 50  # Process in batches to avoid memory overload
    RATE_LIMIT_PER_SECOND = 5  # Don't send more than 5 emails/second
    EMAIL_TIMEOUT = 10  # SMTP timeout in seconds
    VALID_EMAIL_REGEX = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    @staticmethod
    def get_smtp_config() -> Tuple[str, str, str, int]:
        """Get SMTP configuration from environment"""
        sender = os.getenv("MONITOR_EMAIL")
        password = os.getenv("MONITOR_EMAIL_PASSWORD")
        
        if not sender or not password:
            raise ValueError(
                "Missing SMTP credentials. Set MONITOR_EMAIL and "
                "MONITOR_EMAIL_PASSWORD environment variables."
            )
        
        host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        port = int(os.getenv("SMTP_PORT", "465"))
        return sender, password, host, port

# ─────────────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def validate_email(email: str) -> bool:
    """Validate email format"""
    if not email or not isinstance(email, str):
        return False
    return bool(re.match(Config.VALID_EMAIL_REGEX, email.strip()))


def fill_template(template: str, event: dict, duration_label: str = "") -> str:
    """
    Replace template placeholders with actual values
    
    Placeholders supported:
    - {{EventTitle}} → event title
    - {{Category}} → category name
    - {{EventDate}} → event date
    - {{EventTime}} → event time
    - {{EventDescription}} → event description
    - {{ReminderDuration}} → custom duration label
    """
    if not template:
        return ""
    
    replacements = {
        "{{EventTitle}}": str(event.get("title", "")),
        "{{Category}}": str(event.get("category_name", "")),
        "{{EventDate}}": str(event.get("event_date", "")),
        "{{EventTime}}": str(event.get("event_time", "") or "N/A"),
        "{{EventDescription}}": str(event.get("description", "")),
        "{{ReminderDuration}}": str(duration_label or ""),
    }
    
    result = template
    for key, val in replacements.items():
        result = result.replace(key, val or "")
    
    return result


def calculate_next_retry_time(retry_count: int) -> datetime:
    """
    Calculate next retry time using exponential backoff
    Retry 1: 5 min
    Retry 2: 10 min
    Retry 3: 20 min
    """
    delay_minutes = Config.RETRY_DELAY_MINUTES * (Config.EXPONENTIAL_BASE ** retry_count)
    return datetime.now() + timedelta(minutes=delay_minutes)


# ─────────────────────────────────────────────────────────────────
# EMAIL SENDING
# ─────────────────────────────────────────────────────────────────

class EmailSender:
    """Handles email sending with proper error handling"""
    
    def __init__(self):
        self.sender, self.password, self.host, self.port = Config.get_smtp_config()
        self.last_send_time = datetime.now()
    
    def _rate_limit(self):
        """Enforce rate limiting (max 5 emails/second)"""
        elapsed = (datetime.now() - self.last_send_time).total_seconds()
        min_delay = 1.0 / Config.RATE_LIMIT_PER_SECOND
        if elapsed < min_delay:
            import time
            time.sleep(min_delay - elapsed)
        self.last_send_time = datetime.now()
    
    def send(self, to_email: str, subject: str, body: str) -> Tuple[bool, str]:
        """
        Send email with comprehensive error handling
        
        Returns: (success: bool, message: str)
        """
        # Validate recipient
        if not validate_email(to_email):
            return False, f"Invalid email format: {to_email}"
        
        # Validate content
        if not subject or not subject.strip():
            return False, "Subject cannot be empty"
        if not body or not body.strip():
            return False, "Body cannot be empty"
        
        try:
            self._rate_limit()
            
            # Build message
            msg = MIMEMultipart("alternative")
            msg["From"] = self.sender
            msg["To"] = to_email
            msg["Subject"] = subject
            
            # Add plain text version
            text_part = MIMEText(body, "plain", "utf-8")
            msg.attach(text_part)
            
            # Add HTML version (optional)
            html_body = f"<pre style='font-family: Arial, sans-serif;'>{body.replace(chr(10), '<br>')}</pre>"
            html_part = MIMEText(html_body, "html", "utf-8")
            msg.attach(html_part)
            
            # Send via SMTP
            with smtplib.SMTP_SSL(
                self.host, 
                self.port, 
                timeout=Config.EMAIL_TIMEOUT
            ) as server:
                server.login(self.sender, self.password)
                server.sendmail(self.sender, to_email, msg.as_string())
            
            logger.info(f"✓ Email sent to {to_email} | Subject: {subject[:50]}")
            return True, "sent"
            
        except smtplib.SMTPAuthenticationError:
            msg = "SMTP authentication failed - check credentials"
            logger.error(msg)
            return False, msg
        
        except smtplib.SMTPServerDisconnected:
            msg = "SMTP server disconnected - network issue"
            logger.error(msg)
            return False, msg
        
        except smtplib.SMTPException as e:
            msg = f"SMTP error: {str(e)}"
            logger.error(msg)
            return False, msg
        
        except TimeoutError:
            msg = "SMTP connection timeout"
            logger.error(msg)
            return False, msg
        
        except Exception as e:
            msg = f"Unexpected error: {str(e)}"
            logger.error(msg)
            return False, msg

# ─────────────────────────────────────────────────────────────────
# RETRY MANAGEMENT
# ─────────────────────────────────────────────────────────────────

def get_retry_count(schedule_id: int) -> int:
    """Get current retry count for a schedule"""
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("""
            SELECT COUNT(*) as retry_count
            FROM reminder_notification_logs
            WHERE schedule_id = %s AND status IN ('failed', 'retry')
        """, (schedule_id,))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return result['retry_count'] if result else 0
    except Exception as e:
        logger.error(f"Error getting retry count: {e}")
        return 0


def should_retry(schedule_id: int) -> bool:
    """Check if schedule should be retried"""
    retry_count = get_retry_count(schedule_id)
    return retry_count < Config.MAX_RETRIES


def update_for_retry(schedule_id: int, retry_count: int):
    """Mark schedule for retry with exponential backoff"""
    try:
        next_retry = calculate_next_retry_time(retry_count)
        conn = get_connection()
        cur = conn.cursor()
        
        # Create new schedule for retry with incremented remind_at
        cur.execute("""
            SELECT event_id, label FROM reminder_schedules WHERE id = %s
        """, (schedule_id,))
        row = cur.fetchone()
        
        if row:
            event_id = row[0]
            label = row[1]
            
            cur.execute("""
                INSERT INTO reminder_schedules 
                    (event_id, remind_at, label, is_sent)
                VALUES (%s, %s, %s, FALSE)
            """, (event_id, next_retry, f"{label} (Retry {retry_count + 1})"))
            
            conn.commit()
            logger.info(f"✓ Retry scheduled for schedule {schedule_id} at {next_retry}")
        
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error updating for retry: {e}")

# ─────────────────────────────────────────────────────────────────
# DATABASE OPERATIONS
# ─────────────────────────────────────────────────────────────────

def fetch_due_schedules(batch_size: int = Config.BATCH_SIZE) -> List[Dict]:
    """
    Fetch schedules that are due for sending
    Process in batches to avoid memory issues with large datasets
    """
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        
        now = datetime.now()
        cur.execute("""
            SELECT rs.id AS schedule_id, rs.event_id, rs.label, rs.created_at,
                   e.title, e.event_date, e.event_time, e.description,
                   c.name AS category_name
            FROM reminder_schedules rs
            JOIN reminder_events e ON e.id = rs.event_id
            LEFT JOIN reminder_categories c ON c.id = e.category_id
            WHERE rs.is_sent = FALSE
              AND rs.remind_at <= %s
            ORDER BY rs.remind_at ASC
            LIMIT %s
        """, (now, batch_size))
        
        schedules = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        
        logger.info(f"Found {len(schedules)} due schedules")
        return schedules
    
    except Exception as e:
        logger.error(f"Error fetching schedules: {e}")
        return []


def fetch_email_template() -> Optional[Dict]:
    """Fetch email template from database"""
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        
        cur.execute("""
            SELECT id, subject, body, updated_at
            FROM reminder_templates
            WHERE channel = 'email'
            LIMIT 1
        """)
        
        row = cur.fetchone()
        cur.close()
        conn.close()
        
        if row:
            template = dict(row)
            # Decode URL-encoded content if present
            for key in ['subject', 'body']:
                if template.get(key) and '%0A' in template[key]:
                    template[key] = urllib.parse.unquote(template[key])
            
            logger.info(f"✓ Loaded email template (updated: {template['updated_at']})")
            return template
        
        logger.warning("No email template found")
        return None
    
    except Exception as e:
        logger.error(f"Error fetching template: {e}")
        return None


def fetch_recipients(event_id: int) -> List[Dict]:
    """Fetch email recipients for an event"""
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        
        cur.execute("""
            SELECT id, type, value
            FROM reminder_recipients
            WHERE event_id = %s AND type = 'email'
        """, (event_id,))
        
        recipients = [dict(r) for r in cur.fetchall()]
        cur.close()
        conn.close()
        
        return recipients
    
    except Exception as e:
        logger.error(f"Error fetching recipients: {e}")
        return []


def log_send_attempt(
    schedule_id: int,
    event_id: int,
    to_email: str,
    subject: str,
    body: str,
    status: str,
    error_msg: Optional[str] = None
):
    """Log email send attempt to database"""
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        now = datetime.now() if status == "sent" else None
        
        # Log to notification_logs (summary)
        cur.execute("""
            INSERT INTO reminder_notification_logs
                (event_id, channel, recipient, status, error_msg, sent_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (event_id, "email", to_email, status, error_msg, now))
        
        # Log to email_logs (detailed)
        if status == "sent":
            cur.execute("""
                INSERT INTO reminder_email_logs
                    (to_email, subject, body, smtp_response, status)
                VALUES (%s, %s, %s, %s, %s)
            """, (to_email, subject, body, "Success", status))
        elif status == "failed":
            cur.execute("""
                INSERT INTO reminder_email_logs
                    (to_email, subject, body, smtp_response, status)
                VALUES (%s, %s, %s, %s, %s)
            """, (to_email, subject, body, error_msg or "Unknown error", status))
        
        conn.commit()
        cur.close()
        conn.close()
    
    except Exception as e:
        logger.error(f"Error logging send attempt: {e}")


def mark_schedule_sent(schedule_id: int):
    """Mark schedule as sent in database"""
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        now = datetime.now()
        cur.execute("""
            UPDATE reminder_schedules
            SET is_sent = TRUE, sent_at = %s
            WHERE id = %s
        """, (now, schedule_id))
        
        conn.commit()
        cur.close()
        conn.close()
        
        logger.info(f"✓ Marked schedule {schedule_id} as sent")
    
    except Exception as e:
        logger.error(f"Error marking schedule sent: {e}")

# ─────────────────────────────────────────────────────────────────
# MAIN SCHEDULER FUNCTION
# ─────────────────────────────────────────────────────────────────

def fire_due_reminders():
    """
    Main scheduler function - runs every minute
    Finds due reminders and sends emails with retry logic
    """
    try:
        logger.info("━" * 60)
        logger.info("🔔 Starting reminder batch process...")
        
        # Step 1: Initialize email sender
        try:
            sender = EmailSender()
        except ValueError as e:
            logger.error(f"Cannot initialize email sender: {e}")
            return
        
        # Step 2: Fetch due schedules
        schedules = fetch_due_schedules()
        if not schedules:
            logger.info("No due reminders at this moment")
            return
        
        # Step 3: Load email template
        email_template = fetch_email_template()
        if not email_template:
            logger.warning("Skipping - no email template found")
            return
        
        # Step 4: Process each schedule
        sent_count = 0
        failed_count = 0
        retry_count = 0
        
        for schedule in schedules:
            schedule_id = schedule["schedule_id"]
            event_id = schedule["event_id"]
            
            try:
                # Fetch recipients for this event
                recipients = fetch_recipients(event_id)
                
                if not recipients:
                    logger.warning(f"No email recipients for event {event_id}")
                    mark_schedule_sent(schedule_id)
                    continue
                
                # Send to each recipient
                for recipient in recipients:
                    to_email = recipient["value"].strip()
                    
                    # Validate email
                    if not validate_email(to_email):
                        logger.warning(f"Skipping invalid email: {to_email}")
                        log_send_attempt(
                            schedule_id, event_id, to_email,
                            "", "", "failed",
                            f"Invalid email format: {to_email}"
                        )
                        failed_count += 1
                        continue
                    
                    # Fill template
                    subject = fill_template(email_template["subject"], schedule, schedule["label"])
                    body = fill_template(email_template["body"], schedule, schedule["label"])
                    
                    # Send email
                    success, message = sender.send(to_email, subject, body)
                    
                    if success:
                        log_send_attempt(
                            schedule_id, event_id, to_email,
                            subject, body, "sent"
                        )
                        sent_count += 1
                    else:
                        # Handle failure with retry logic
                        retry_count_current = get_retry_count(schedule_id)
                        
                        if should_retry(schedule_id):
                            log_send_attempt(
                                schedule_id, event_id, to_email,
                                subject, body, "retry", message
                            )
                            update_for_retry(schedule_id, retry_count_current)
                            retry_count += 1
                        else:
                            log_send_attempt(
                                schedule_id, event_id, to_email,
                                subject, body, "failed",
                                f"{message} (Max retries exceeded)"
                            )
                            failed_count += 1
                
                # Mark schedule as sent (even if some recipients failed)
                mark_schedule_sent(schedule_id)
            
            except Exception as e:
                logger.error(f"Error processing schedule {schedule_id}: {e}")
                failed_count += 1
        
        # Step 5: Log summary
        logger.info("━" * 60)
        logger.info(f"📊 Batch Summary:")
        logger.info(f"  ✓ Sent: {sent_count}")
        logger.info(f"  ⟲ Retries: {retry_count}")
        logger.info(f"  ✗ Failed: {failed_count}")
        logger.info(f"  Total: {sent_count + retry_count + failed_count}")
        logger.info("━" * 60)
    
    except Exception as e:
        logger.error(f"Critical error in fire_due_reminders: {e}")

# ─────────────────────────────────────────────────────────────────
# SCHEDULER STARTUP
# ─────────────────────────────────────────────────────────────────

_scheduler = None
_lock = threading.Lock()

def start_reminder_scheduler():
    """Start the background reminder scheduler"""
    global _scheduler
    
    with _lock:
        if _scheduler and _scheduler.running:
            logger.info("Scheduler already running")
            return
        
        try:
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
            logger.info("✓ ReminderScheduler started - checking every 60 seconds")
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")

def stop_reminder_scheduler():
    """Stop the background reminder scheduler"""
    global _scheduler
    
    with _lock:
        if _scheduler and _scheduler.running:
            _scheduler.shutdown(wait=False)
            logger.info("✓ ReminderScheduler stopped")

# ─────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────────

def get_scheduler_status() -> Dict:
    """Get current scheduler status"""
    return {
        "running": _scheduler is not None and _scheduler.running,
        "jobs": len(_scheduler.get_jobs()) if _scheduler else 0,
        "timestamp": datetime.now().isoformat()
    }