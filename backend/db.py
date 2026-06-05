# db.py
import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv  # ← ADD

load_dotenv()  # ← ADD — .env file load karega

DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "localhost"),
    "port":     os.getenv("DB_PORT",     "5432"),
    "database": os.getenv("DB_NAME",     "careermyntra_portal"),
    "user":     os.getenv("DB_USER",     "postgres"),
    "password": os.getenv("DB_PASSWORD", "Anuradha1224"),
}

def get_connection():
    return psycopg2.connect(**DB_CONFIG)

def get_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

def init_db():
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS courses (
            id            SERIAL PRIMARY KEY,
            name          VARCHAR(100) NOT NULL,
            exam          VARCHAR(100) NOT NULL,
            icon          VARCHAR(10)  DEFAULT '📁',
            script        VARCHAR(200),
            sample_input  VARCHAR(200),
            sample_output VARCHAR(200),
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("[DB] PostgreSQL connected ✅")


    