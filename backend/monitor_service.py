# monitor_service.py — Background monitoring engine
# Implements scraping + email logic (same as sir's original website_monitor.py)
# Controlled via start_monitor() / stop_monitor() / get_status()

import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import json
import threading
import time
from datetime import datetime
from db import get_connection, get_cursor

# ── Global state ────────────────────────────────────────────
_thread = None
_stop_event = threading.Event()
_status = {
    "running": False,
    "last_checked_at": None,
    "last_result": None,   # "updates_found" | "no_updates" | "error" | None
    "sites_checked": 0,
    "updates_found": 0,
}


# ── get_website_content() — SAME LOGIC AS SIR'S CODE ──────────
def get_website_content(url):
    """Extract headline+link pairs from anchor tags (text length > 30 chars)"""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        headlines = []
        seen = set()

        for link in soup.find_all("a", href=True):
            text = link.get_text(strip=True)
            href = link["href"]

            if len(text) < 30:
                continue
            if href.startswith("/"):
                href = url.rstrip("/") + href
            elif not href.startswith("http"):
                continue

            if text not in seen:
                seen.add(text)
                headlines.append({"title": text, "link": href})

        return headlines[:15]

    except Exception as e:
        print(f"\n[Monitor] Error reading {url} : {e}")
        return None


# ── send_combined_email() — SAME HTML TEMPLATE AS SIR'S CODE ──
def send_combined_email(all_updates, config):
    """Send one combined email for all sites that had updates.
    config = {alert_email, app_password, recipient_emails}"""
    sender_email = config.get("alert_email")
    app_password = config.get("app_password")
    recipients   = config.get("recipient_emails") or sender_email

    if not sender_email or not app_password:
        print("[Monitor] Email credentials not configured — skipping email")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Website Update Alert"
        msg["From"] = sender_email
        msg["To"] = recipients

        current_time = datetime.now().strftime("%d-%m-%Y %I:%M:%S %p")

        all_sections_html = ""

        for update in all_updates:
            website_url = update["url"]
            headlines = update["headlines"]

            headlines_html = ""
            for index, item in enumerate(headlines, start=1):
                headlines_html += f"""
                <div style="
                    border: 1px solid #dddddd;
                    border-radius: 8px;
                    padding: 15px;
                    margin-bottom: 12px;
                    background-color: #fafafa;
                ">
                    <div style="
                        font-size: 15px;
                        font-weight: bold;
                        color: #222222;
                        margin-bottom: 10px;
                        line-height: 1.5;
                    ">
                        {index}. {item['title']}
                    </div>
                    <a href="{item['link']}" target="_blank" style="
                        color: #1a73e8;
                        text-decoration: none;
                        font-size: 14px;
                        font-weight: bold;
                    ">
                        Read Full Article →
                    </a>
                    <div style="
                        margin-top: 10px;
                        font-size: 12px;
                        color: #888888;
                        border-top: 1px solid #eeeeee;
                        padding-top: 8px;
                    ">
                         {website_url} &nbsp;|&nbsp;  {current_time}
                    </div>
                </div>
                """

            all_sections_html += f"""
            <div style="margin-bottom: 40px;">
                <h3 style="color: #1a73e8; border-bottom: 2px solid #1a73e8; padding-bottom: 6px;">
                    <a href="{website_url}" target="_blank" style="color: #1a73e8; text-decoration: none;">
                        {website_url}
                    </a>
                </h3>
                {headlines_html}
            </div>
            """

        html_body = f"""
        <html>
        <body style="
            font-family: Arial, sans-serif;
            background-color: #f4f4f4;
            padding: 20px;
        ">
            <div style="
                max-width: 750px;
                background-color: white;
                margin: auto;
                border-radius: 10px;
                padding: 30px;
                border: 1px solid #dddddd;
            ">
                <h2 style="color: #1a73e8; margin-bottom: 10px;">
                    Website Update Notification
                </h2>

                <p>Dear User,</p>
                <p>New updates have been detected on the following monitored websites.</p>

                <table style="width: 100%; border-collapse: collapse; margin-bottom: 30px;">
                    <tr>
                        <td style="border: 1px solid #dddddd; padding: 10px; font-weight: bold; width: 150px;">
                            Detected At
                        </td>
                        <td style="border: 1px solid #dddddd; padding: 10px;">
                            {current_time}
                        </td>
                    </tr>
                    <tr>
                        <td style="border: 1px solid #dddddd; padding: 10px; font-weight: bold;">
                            Sites Updated
                        </td>
                        <td style="border: 1px solid #dddddd; padding: 10px;">
                            {len(all_updates)} website(s)
                        </td>
                    </tr>
                </table>

                {all_sections_html}

                <hr style="margin-top: 30px;">
                <p style="text-align: center; color: gray; font-size: 12px;">
                    Automated Website Monitoring System
                </p>
            </div>
        </body>
        </html>
        """

        part = MIMEText(html_body, "html", "utf-8")
        msg.attach(part)

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, app_password)
        server.sendmail(sender_email, [r.strip() for r in recipients.split(",")], msg.as_string())
        server.quit()

        print("\n[Monitor] Email Sent Successfully!")
        return True

    except Exception as e:
        print("\n[Monitor] Email Error:", e)
        return False


# ── Config Loader ──────────────────────────────────────────────
def _load_config():
    conn = get_connection()
    cur = get_cursor(conn)
    cur.execute("SELECT alert_email, app_password, recipient_emails, interval_seconds FROM monitor_config LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return {"alert_email": None, "app_password": None, "recipient_emails": None, "interval_seconds": 120}
    return dict(row)


# ── check_notifications() — SAME LOGIC, DB INSTEAD OF .txt FILES ──
def check_notifications():
    print("\n========================================")
    print("Checking Websites...")
    print("========================================\n")

    config = _load_config()

    conn = get_connection()
    cur = get_cursor(conn)

    cur.execute("SELECT id, url, label FROM monitor_urls WHERE is_active = TRUE")
    sites = cur.fetchall()

    all_updates = []
    sites_checked = 0

    for site in sites:
        url_id = site["id"]
        url    = site["url"]
        label  = site["label"] or url

        print(f"Checking: {url}")
        sites_checked += 1

        current_content = get_website_content(url)
        if current_content is None:
            continue

        current_content_str = json.dumps(current_content, sort_keys=True)

        # Get last saved snapshot for this URL (one row per URL in new schema)
        cur.execute(
            "SELECT content FROM monitor_snapshots WHERE url_id = %s",
            (url_id,)
        )
        last = cur.fetchone()

        if last is None:
            # First time — just save snapshot, no email
            cur.execute(
                "INSERT INTO monitor_snapshots (url_id, content, updated_at) VALUES (%s, %s, CURRENT_TIMESTAMP)",
                (url_id, current_content_str)
            )
            print("Initial Website Data Saved.\n")
            continue

        old_content_str = last["content"]

        if current_content_str != old_content_str:
            print("New Website Update Found!")

            all_updates.append({
                "url": url,
                "headlines": current_content
            })

            cur.execute(
                """INSERT INTO monitor_snapshots (url_id, content, updated_at)
                   VALUES (%s, %s, CURRENT_TIMESTAMP)
                   ON CONFLICT (url_id) DO UPDATE SET content = EXCLUDED.content, updated_at = CURRENT_TIMESTAMP""",
                (url_id, current_content_str)
            )

            cur.execute("""
                INSERT INTO monitor_alerts (url_id, url, label, new_headlines)
                VALUES (%s, %s, %s, %s)
            """, (url_id, url, label, json.dumps(current_content)))
        else:
            print("No New Updates Found.\n")

    conn.commit()

    result = "no_updates"
    if all_updates:
        print(f"\n{len(all_updates)} site(s) updated — sending combined email...")
        email_sent = send_combined_email(all_updates, config)
        if email_sent:
            cur.execute("UPDATE monitor_alerts SET email_sent = TRUE WHERE email_sent = FALSE")
            conn.commit()
        result = "updates_found"
    else:
        print("\nNo updates across any site. No email sent.")

    cur.close()
    conn.close()

    _status["last_checked_at"] = datetime.now().isoformat()
    _status["last_result"] = result
    _status["sites_checked"] = sites_checked
    _status["updates_found"] = len(all_updates)

    return all_updates


# ── Background Loop ────────────────────────────────────────────
def _run_loop():
    _status["running"] = True
    while not _stop_event.is_set():
        try:
            check_notifications()
        except Exception as e:
            print("[Monitor] Loop error:", e)
            _status["last_result"] = "error"

        config = _load_config()
        interval = config.get("interval_seconds") or 120

        # Sleep in small chunks so stop_monitor() can interrupt quickly
        waited = 0
        while waited < interval and not _stop_event.is_set():
            time.sleep(1)
            waited += 1

    _status["running"] = False


# ── Public Controls ────────────────────────────────────────────
def start_monitor():
    """Start the background monitor loop. Returns False if already running."""
    global _thread
    if _status["running"]:
        return False
    _stop_event.clear()
    _thread = threading.Thread(target=_run_loop, daemon=True)
    _thread.start()
    return True


def stop_monitor():
    """Signal the background loop to stop."""
    _stop_event.set()
    _status["running"] = False


def get_status():
    return dict(_status)