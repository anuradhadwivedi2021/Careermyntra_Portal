import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "careermyntra_portal"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD")
    )

def get_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS courses (
            id            SERIAL PRIMARY KEY,
            name          VARCHAR(100) NOT NULL,
            exam          VARCHAR(100) NOT NULL,
            icon          VARCHAR(10)  DEFAULT '📁',
            script        VARCHAR(200),
            script_content TEXT,
            sample_input  VARCHAR(200),
            sample_output VARCHAR(200),
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        ALTER TABLE courses ADD COLUMN IF NOT EXISTS script_content TEXT;
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS college_master (
            id              SERIAL PRIMARY KEY,
            college_code    VARCHAR(20) NOT NULL UNIQUE,
            college_name    VARCHAR(300) NOT NULL,
            district        VARCHAR(100),
            city            VARCHAR(100),
            university      VARCHAR(300),
            college_type    VARCHAR(100),
            management      VARCHAR(100),
            minority_status VARCHAR(100),
            autonomy_status VARCHAR(100),
            website         VARCHAR(300),
            phone           VARCHAR(50),
            address         TEXT,
            state           VARCHAR(100) DEFAULT 'Maharashtra',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            first_name    VARCHAR(100) NOT NULL,
            last_name     VARCHAR(100),
            email         VARCHAR(150) UNIQUE NOT NULL,
            phone         VARCHAR(20),
            password_hash VARCHAR(255) NOT NULL,
            role          VARCHAR(20) DEFAULT 'user',
            is_verified   BOOLEAN DEFAULT FALSE,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS monitor_config (
            id               SERIAL PRIMARY KEY,
            alert_email      VARCHAR(200) NOT NULL,
            app_password     VARCHAR(200) NOT NULL,
            recipient_emails TEXT DEFAULT '',
            interval_seconds INTEGER DEFAULT 120,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        ALTER TABLE monitor_config ADD COLUMN IF NOT EXISTS recipient_emails TEXT DEFAULT '';
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS monitor_urls (
            id         SERIAL PRIMARY KEY,
            url        TEXT NOT NULL,
            label      VARCHAR(200),
            is_active  BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS monitor_snapshots (
            url_id     INTEGER PRIMARY KEY REFERENCES monitor_urls(id),
            content    TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS monitor_alerts (
            id            SERIAL PRIMARY KEY,
            url_id        INTEGER REFERENCES monitor_urls(id) ON DELETE CASCADE,
            url           VARCHAR(500),
            label         VARCHAR(200),
            new_headlines TEXT,
            email_sent    BOOLEAN DEFAULT FALSE,
            detected_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("SELECT COUNT(*) FROM monitor_urls")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO monitor_urls (url, label) VALUES
            ('https://mahafyjcadmissions.in/landing', 'MahaFYJC Admissions'),
            ('https://timesofindia.indiatimes.com/', 'Times of India'),
            ('https://www.thehindu.com/', 'The Hindu'),
            ('https://www.hindustantimes.com/', 'Hindustan Times');
        """)

    cur.execute("""
        INSERT INTO monitor_config (alert_email, app_password, recipient_emails, interval_seconds)
        SELECT %s, %s, %s, %s
        WHERE NOT EXISTS (SELECT 1 FROM monitor_config);
    """, (
        os.getenv("MONITOR_EMAIL", "anuradha.dwivedi2021@gmail.com"),
        os.getenv("MONITOR_EMAIL_PASSWORD"),
        os.getenv("MONITOR_ALERT_TO", "careermyntrapune@gmail.com, collegescutoff@gmail.com, khamgaonkarpawan@gmail.com, anuradha.dwivedi2021@gmail.com"),
        120
    ))

    # ── Reminders Feature Tables ──────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminder_categories (
            id         SERIAL PRIMARY KEY,
            name       VARCHAR(150) NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminder_subcategories (
            id          SERIAL PRIMARY KEY,
            name        VARCHAR(150) NOT NULL UNIQUE,
            category_id INTEGER REFERENCES reminder_categories(id) ON DELETE SET NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminder_events (
            id              SERIAL PRIMARY KEY,
            title           VARCHAR(300) NOT NULL,
            category_id     INTEGER REFERENCES reminder_categories(id) ON DELETE SET NULL,
            subcategory_id  INTEGER REFERENCES reminder_subcategories(id) ON DELETE SET NULL,
            description     TEXT,
            event_date      DATE NOT NULL,
            event_time      TIME,
            start_dt        TIMESTAMP,
            end_dt          TIMESTAMP,
            priority        VARCHAR(20) DEFAULT 'medium',
            status          VARCHAR(20) DEFAULT 'upcoming',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminder_schedules (
            id         SERIAL PRIMARY KEY,
            event_id   INTEGER REFERENCES reminder_events(id) ON DELETE CASCADE,
            remind_at  TIMESTAMP NOT NULL,
            label      VARCHAR(50),
            is_sent    BOOLEAN DEFAULT FALSE,
            sent_at    TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("""
        ALTER TABLE reminder_schedules ADD COLUMN IF NOT EXISTS is_sent BOOLEAN DEFAULT FALSE;
    """)
    cur.execute("""
        ALTER TABLE reminder_schedules ADD COLUMN IF NOT EXISTS sent_at TIMESTAMP;
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminder_recipients (
            id         SERIAL PRIMARY KEY,
            event_id   INTEGER REFERENCES reminder_events(id) ON DELETE CASCADE,
            type       VARCHAR(20) NOT NULL,
            value      VARCHAR(200) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminder_event_attachments (
            id         SERIAL PRIMARY KEY,
            event_id   INTEGER REFERENCES reminder_events(id) ON DELETE CASCADE,
            filename   VARCHAR(300),
            filepath   VARCHAR(500),
            filesize   INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminder_templates (
            id         SERIAL PRIMARY KEY,
            channel    VARCHAR(20) NOT NULL UNIQUE,
            subject    VARCHAR(300),
            body       TEXT NOT NULL,
            html_body  TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # FIX: html_body was added after this table already existed in
    # production — ADD COLUMN IF NOT EXISTS makes this safe to re-run.
    cur.execute("""
        ALTER TABLE reminder_templates
        ADD COLUMN IF NOT EXISTS html_body TEXT;
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminder_notification_logs (
            id          SERIAL PRIMARY KEY,
            event_id    INTEGER REFERENCES reminder_events(id) ON DELETE CASCADE,
            channel     VARCHAR(20),
            recipient   VARCHAR(200),
            status      VARCHAR(20),
            error_msg   TEXT,
            sent_at     TIMESTAMP,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reminder_email_logs (
            id             SERIAL PRIMARY KEY,
            to_email       VARCHAR(200),
            subject        VARCHAR(300),
            body           TEXT,
            smtp_response  TEXT,
            status         VARCHAR(20),
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("SELECT COUNT(*) FROM reminder_templates")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO reminder_templates (channel, subject, body) VALUES
            ('email',
             'Reminder: {{EventTitle}} on {{EventDate}}',
             'Dear Student,%0A%0AThis is a reminder for:%0A%0AEvent: {{EventTitle}}%0ACategory: {{Category}}%0ADate: {{EventDate}}%0ATime: {{EventTime}}%0A%0ADescription:%0A{{EventDescription}}%0A%0APlease complete the required action before the deadline.%0A%0ARegards,%0ACareerMyntra Admission Guidance Team'),
            ('whatsapp',
             NULL,
             'Reminder Alert%0A%0AEvent: {{EventTitle}}%0ADate: {{EventDate}}%0ATime: {{EventTime}}%0ACategory: {{Category}}%0A%0A{{EventDescription}}%0A%0AThis event is scheduled within the next {{ReminderDuration}}.%0A%0ACareerMyntra%0AAdmission Guidance Team');
        """)

    # ── Seed Default Categories ───────────────────────────────
    cur.execute("SELECT COUNT(*) FROM reminder_categories")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO reminder_categories (name) VALUES
            ('Entrance Exam'),
            ('Admission Process'),
            ('CAP Round'),
            ('Counseling'),
            ('Document Verification'),
            ('Scholarship'),
            ('Fee Payment'),
            ('College Reporting'),
            ('Other');
        """)

    # ── Seed Default Subcategories ────────────────────────────
    cur.execute("SELECT COUNT(*) FROM reminder_subcategories")
    if cur.fetchone()[0] == 0:
        cur.execute("SELECT id, name FROM reminder_categories")
        cat_map = {row[1]: row[0] for row in cur.fetchall()}

        subcategories = [
            ("MHT-CET",         "Entrance Exam"),
            ("JEE Main",        "Entrance Exam"),
            ("JEE Advanced",    "Entrance Exam"),
            ("NEET",            "Entrance Exam"),
            ("FYJC",            "Admission Process"),
            ("CAP Round 1",     "CAP Round"),
            ("CAP Round 2",     "CAP Round"),
            ("CAP Round 3",     "CAP Round"),
            ("Merit List",      "Admission Process"),
            ("Document Upload", "Document Verification"),
            ("Tuition Fee",     "Fee Payment"),
            ("Development Fee", "Fee Payment"),
        ]
        for name, cat_name in subcategories:
            cat_id = cat_map.get(cat_name)
            if cat_id:
                cur.execute(
                    "INSERT INTO reminder_subcategories (name, category_id) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                    (name, cat_id)
                )

    conn.commit()
    cur.close()
    conn.close()
    print("[DB] PostgreSQL connected ✅")