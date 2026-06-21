# seed_users.py — Insert the 4 allowed CareerMyntra users into the DB
# Run this ONCE: python seed_users.py
# Place this file in: backend/seed_users.py

import bcrypt
from db import get_connection

USERS = [
    {"email": "careermyntrapune@gmail.com",     "first_name": "CareerMyntra", "password": "Career@123"},
    {"email": "collegescutoff@gmail.com",       "first_name": "CollegeCutoff", "password": "Career@123"},
    {"email": "khamgaonkarpawan@gmail.com",     "first_name": "Pawan",        "password": "Career@123"},
    {"email": "anuradha.dwivedi2021@gmail.com", "first_name": "Anuradha",     "password": "Career@123"},
]

def seed():
    conn = get_connection()
    cur = conn.cursor()

    for u in USERS:
        password_hash = bcrypt.hashpw(u["password"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        cur.execute("""
            INSERT INTO users (first_name, email, password_hash, role, is_verified)
            VALUES (%s, %s, %s, 'admin', TRUE)
            ON CONFLICT (email) DO UPDATE
            SET password_hash = EXCLUDED.password_hash
        """, (u["first_name"], u["email"], password_hash))
        print(f"✅ {u['email']} added/updated")

    conn.commit()
    cur.close()
    conn.close()
    print("\n🎉 All users seeded successfully!")

if __name__ == "__main__":
    seed()