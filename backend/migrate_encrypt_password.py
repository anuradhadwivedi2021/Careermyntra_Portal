#!/usr/bin/env python3
"""
migrate_encrypt_password.py
───────────────────────────
Ek baar chalao — DB mein jo plain text app_password hai usse encrypt kar dega.

Run karne se pehle:
  1. .env mein ENCRYPT_KEY set karo
  2. python migrate_encrypt_password.py

Ye script idempotent hai — agar password already encrypted hai to kuch nahi karega.
"""
import sys
import os

# Backend folder aur routes folder dono path mein daalo
_base = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _base)
sys.path.insert(0, os.path.join(_base, "routes"))

from dotenv import load_dotenv
load_dotenv()

from db import get_connection, get_cursor
from crypto_utils import encrypt_password, decrypt_password


def is_already_encrypted(value: str) -> bool:
    """
    Fernet tokens hamesha 'gAAAAA' se shuru hote hain aur base64 encoded hote hain.
    Agar plain text Gmail app password hai to ye pattern match nahi karega.
    """
    return value.startswith("gAAAAA")


def migrate():
    print("=" * 50)
    print("  Monitor Config Password Encryption Migration")
    print("=" * 50)

    conn = get_connection()
    cur  = get_cursor(conn)

    cur.execute("SELECT id, app_password FROM monitor_config LIMIT 1")
    row = cur.fetchone()

    if not row:
        print("[INFO] monitor_config table mein koi record nahi mila.")
        cur.close(); conn.close()
        return

    record_id    = row["id"]
    current_pass = row["app_password"] or ""

    if not current_pass:
        print("[INFO] app_password blank hai — kuch karne ki zaroorat nahi.")
        cur.close(); conn.close()
        return

    if is_already_encrypted(current_pass):
        print("[OK] Password already encrypted hai — migration ki zaroorat nahi.")
        cur.close(); conn.close()
        return

    print(f"[INFO] Plain text password mila (length: {len(current_pass)}) — encrypt kar raha hoon...")

    encrypted = encrypt_password(current_pass)

    write_cur = conn.cursor()
    write_cur.execute(
        "UPDATE monitor_config SET app_password = %s WHERE id = %s",
        (encrypted, record_id)
    )
    conn.commit()
    write_cur.close()

    # Verify
    verification = decrypt_password(encrypted)
    if verification == current_pass:
        print("[SUCCESS] Password successfully encrypt hua aur verify bhi ho gaya!")
        print(f"   Original length : {len(current_pass)}")
        print(f"   Encrypted length: {len(encrypted)}")
    else:
        print("[ERROR] Verification failed! Manual check karo.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    migrate()