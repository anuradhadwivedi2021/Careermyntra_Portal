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
        password=os.getenv("DB_PASSWORD", "Anuradha1411")
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
        DELETE FROM monitor_snapshots;
        DELETE FROM monitor_urls;
        INSERT INTO monitor_urls (url, label) VALUES
        ('https://mahafyjcadmissions.in/landing', 'MahaFYJC Admissions'),
        ('https://timesofindia.indiatimes.com/', 'Times of India'),
        ('https://www.thehindu.com/', 'The Hindu'),
        ('https://www.hindustantimes.com/', 'Hindustan Times');
    """)
    cur.execute("""
        INSERT INTO monitor_config (alert_email, app_password, interval_seconds)
        SELECT 'anuradha.dwivedi2021@gmail.com', 'ootc qsfd tori cfcq', 120
        WHERE NOT EXISTS (SELECT 1 FROM monitor_config);
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("[DB] PostgreSQL connected ✅")