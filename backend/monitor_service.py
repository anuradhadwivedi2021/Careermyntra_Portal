# monitor_service.py — Background website monitoring engine

import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import threading
import time
import os
from datetime import datetime
from db import get_connection, get_cursor

CHECK_INTERVAL = 600

# ── In-memory state ─────────────────────────────────────────
_monitor_thread = None
_stop_event = threading.Event()
_status = {"running": False, "last_checked": None, "last_alert": None, "log": []}

def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    _status["log"].append(entry)
    _status["log"] = _status["log"][-50:]  # keep last 50 lines
    print(entry, flush=True)

# ── DB helpers ──────────────────────────────────────────────
def get_monitor_config():
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("SELECT * FROM monitor_config LIMIT 1")
        row = cur.fetchone()
        cur.close(); conn.close()
        return dict(row) if row else None
    except Exception as e:
        _log(f"❌ Config DB error: {e}")
        return None

def get_monitor_urls():
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("SELECT * FROM monitor_urls WHERE is_active = TRUE ORDER BY id")
        rows = cur.fetchall()
        cur.close(); conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        _log(f"❌ URLs DB error: {e}")
        return []

def get_saved_content(url_id):
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        cur.execute("SELECT content FROM monitor_snapshots WHERE url_id = %s", (url_id,))
        row = cur.fetchone()
        cur.close(); conn.close()
        return row["content"] if row else None
    except Exception as e:
        _log(f"❌ Snapshot DB error: {e}")
        return None

def save_content(url_id, content):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO monitor_snapshots (url_id, content)
            VALUES (%s, %s)
            ON CONFLICT (url_id) DO UPDATE SET content = EXCLUDED.content, updated_at = NOW()
        """, (url_id, content))
        conn.commit(); cur.close(); conn.close()
    except Exception as e:
        _log(f"DB save error: {e}")

# ── Scraper ─────────────────────────────────────────────────
def get_website_content(url):
    try:
        headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        headlines = []
        seen = set()
        for link in soup.find_all("a", href=True):
            text = link.get_text(strip=True)
            href = link["href"]
            if len(text) < 30: continue
            if href.startswith("/"): href = url.rstrip("/") + href
            elif not href.startswith("http"): continue
            if text not in seen:
                seen.add(text)
                headlines.append({"title": text, "link": href})
        return headlines[:15]
    except Exception as e:
        _log(f"Error reading {url}: {e}")
        return None

# ── Email ────────────────────────────────────────────────────
def send_alert_email(config, all_updates):
    try:
        email = config["alert_email"]
        app_password = config["app_password"]
        current_time = datetime.now().strftime("%d-%m-%Y %I:%M:%S %p")
        all_sections_html = ""

        for update in all_updates:
            headlines_html = ""
            for i, item in enumerate(update["headlines"], 1):
                headlines_html += f"""
                <div style="border:1px solid #ddd;border-radius:8px;padding:15px;margin-bottom:12px;background:#fafafa;">
                    <div style="font-size:15px;font-weight:bold;color:#222;margin-bottom:10px;">{i}. {item['title']}</div>
                    <a href="{item['link']}" style="color:#1a73e8;font-size:14px;font-weight:bold;">Read Full Article →</a>
                    <div style="margin-top:10px;font-size:12px;color:#888;border-top:1px solid #eee;padding-top:8px;">
                        {update['url']} | {current_time}
                    </div>
                </div>"""
            all_sections_html += f"""
            <div style="margin-bottom:40px;">
                <h3 style="color:#1a73e8;border-bottom:2px solid #1a73e8;padding-bottom:6px;">
                    <a href="{update['url']}" style="color:#1a73e8;text-decoration:none;">{update['url']}</a>
                </h3>
                {headlines_html}
            </div>"""

        html_body = f"""<html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
        <div style="max-width:750px;background:white;margin:auto;border-radius:10px;padding:30px;border:1px solid #ddd;">
            <h2 style="color:#1a73e8;">🔔 CareerMyntra — Website Update Alert</h2>
            <p>New updates detected on monitored websites.</p>
            <table style="width:100%;border-collapse:collapse;margin-bottom:30px;">
                <tr><td style="border:1px solid #ddd;padding:10px;font-weight:bold;">Detected At</td>
                    <td style="border:1px solid #ddd;padding:10px;">{current_time}</td></tr>
                <tr><td style="border:1px solid #ddd;padding:10px;font-weight:bold;">Sites Updated</td>
                    <td style="border:1px solid #ddd;padding:10px;">{len(all_updates)} website(s)</td></tr>
            </table>
            {all_sections_html}
            <hr><p style="text-align:center;color:gray;font-size:12px;">CareerMyntra Automated Monitor</p>
        </div></body></html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "🔔 CareerMyntra Website Update Alert"
        msg["From"] = email
        msg["To"] = email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(email, app_password)
        server.send_message(msg)
        server.quit()
        _log("✅ Alert email sent!")
        _status["last_alert"] = current_time
    except Exception as e:
        _log(f"❌ Email error: {e}")

# ── Main loop ────────────────────────────────────────────────
def _monitor_loop():
    _log("Monitor started 🟢")
    while not _stop_event.is_set():
        config = get_monitor_config()
        _log(f"Config fetched: {config is not None}")
        if not config:
            _log("No config found. Waiting...")
            time.sleep(CHECK_INTERVAL)
            continue

        urls = get_monitor_urls()
        all_updates = []
        _status["last_checked"] = datetime.now().strftime("%d-%m-%Y %I:%M:%S %p")

        for row in urls:
            url = row["url"]
            url_id = row["id"]
            _log(f"Checking: {url}")
            current = get_website_content(url)
            if current is None: continue
            current_str = str(current)
            old = get_saved_content(url_id)
            if old is None:
                save_content(url_id, current_str)
                _log(f"Initial snapshot saved for {url}")
                continue
            if current_str != old:
                _log(f"🔔 Update detected: {url}")
                all_updates.append({"url": url, "headlines": current})
                save_content(url_id, current_str)
            else:
                _log(f"No change: {url}")

        if all_updates:
            send_alert_email(config, all_updates)
        else:
            _log("No updates. No email sent.")

        _stop_event.wait(CHECK_INTERVAL)
    _log("Monitor stopped 🔴")

# ── Public API ───────────────────────────────────────────────
def start_monitor():
    global _monitor_thread
    if _status["running"]:
        return False
    _stop_event.clear()
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _monitor_thread.start()
    _status["running"] = True
    return True

def stop_monitor():
    _stop_event.set()
    _status["running"] = False
    return True

def get_status():
    return dict(_status)