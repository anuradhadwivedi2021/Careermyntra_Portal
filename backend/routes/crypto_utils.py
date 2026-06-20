# crypto_utils.py — App Password Encryption/Decryption Helper
# Uses Fernet symmetric encryption (AES-128-CBC + HMAC)
#
# Setup (ek baar karna hai):
#   1. python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#   2. Output ko .env mein daalo:  ENCRYPT_KEY=<generated_key>
#
# Install: pip install cryptography

import os
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv()

def _get_fernet() -> Fernet:
    """
    .env se ENCRYPT_KEY load karo aur Fernet object banao.
    Agar key nahi hai to runtime error raise hoga — intentional hai
    taaki silent failure na ho.
    """
    key = os.getenv("ENCRYPT_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ENCRYPT_KEY .env mein set nahi hai! "
            "Run: python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            " aur output ko ENCRYPT_KEY=... .env mein daalo."
        )
    return Fernet(key.encode())


def encrypt_password(plain_text: str) -> str:
    """
    Plain text password ko encrypt karke string return karo.
    DB mein yahi encrypted string store hogi.
    """
    if not plain_text:
        return ""
    f = _get_fernet()
    return f.encrypt(plain_text.encode()).decode()


def decrypt_password(encrypted_text: str) -> str:
    """
    DB se aaya encrypted string ko decrypt karke plain text return karo.
    Agar token invalid hai (corrupt / wrong key) to empty string return karo.
    """
    if not encrypted_text:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(encrypted_text.encode()).decode()
    except (InvalidToken, Exception) as e:
        print(f"[CryptoUtils] Decryption failed: {e}")
        return ""