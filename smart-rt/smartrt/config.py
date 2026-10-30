import os
import secrets
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTANCE = os.path.join(ROOT, "instance")
os.makedirs(INSTANCE, exist_ok=True)


def _secret_key():
    """SECRET_KEY dari env, atau dibuat sekali dan disimpan di instance/secret.key."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(INSTANCE, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    with open(path) as f:
        return f.read().strip()


class Config:
    SECRET_KEY = _secret_key()
    DATABASE = os.environ.get("DATABASE", os.path.join(INSTANCE, "smartrt.db"))
    UPLOAD_FOLDER = os.path.join(INSTANCE, "uploads")
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024            # batas unggah 5 MB
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE") == "1"   # set 1 bila memakai HTTPS
    PERMANENT_SESSION_LIFETIME = timedelta(hours=4)
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")          # mis. http://192.168.1.10:5000
    SHOW_DEMO_ACCOUNTS = os.environ.get("SHOW_DEMO", "1") == "1"     # set SHOW_DEMO=0 di produksi
