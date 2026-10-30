"""Inti aplikasi: database, keamanan (RBAC, CSRF, sesi), validasi input, notifikasi."""
import hmac
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import abort, current_app, g, redirect, request, session, url_for

ROLES = {"admin": "Admin Sistem", "ketua": "Ketua RT", "sekretaris": "Sekretaris",
         "bendahara": "Bendahara", "humas": "Humas", "warga": "Warga"}
STAFF = ("ketua", "sekretaris", "bendahara")
ALL = tuple(ROLES)

STATUS = {  # kind -> status -> (label, warna badge)
    "surat": {"diajukan": ("Diajukan", "blue"), "menunggu_ketua": ("Menunggu Ketua RT", "amber"),
              "disetujui": ("Disetujui", "green"), "selesai": ("Selesai", "emerald"),
              "ditolak": ("Ditolak", "red")},
    "laporan": {"baru": ("Baru", "blue"), "diproses": ("Diproses", "amber"),
                "selesai": ("Selesai", "emerald"), "ditolak": ("Ditolak", "red")},
    "tagihan": {"belum_bayar": ("Belum Bayar", "red"), "menunggu_verifikasi": ("Menunggu Verifikasi", "blue"),
                "lunas": ("Lunas", "emerald"), "ditolak": ("Bukti Ditolak", "amber")},
    "aspirasi": {"baru": ("Baru", "blue"), "ditanggapi": ("Ditanggapi", "emerald")},
}
BLN = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


class Invalid(Exception):
    """Kesalahan validasi input; pesannya ditampilkan ke pengguna."""


# ---------------------------------------------------------------- database
def db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(_=None):
    d = g.pop("db", None)
    if d is not None:
        d.close()


def q(sql, args=(), one=False):
    cur = db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def scalar(sql, args=()):
    r = db().execute(sql, args).fetchone()
    return r[0] if r else None


def ex(sql, args=(), commit=True):
    cur = db().execute(sql, args)
    if commit:
        db().commit()
    return cur.lastrowid


_USERS_BARU = """CREATE TABLE users_new(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  nama TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','ketua','sekretaris','bendahara','humas','warga')),
  aktif INTEGER NOT NULL DEFAULT 1,
  last_login TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
)"""


def _migrate(con):
    """Migrasi database lama. Peran 'humas' butuh batasan CHECK baru pada tabel users, dan SQLite tidak bisa
    mengubah CHECK langsung, jadi tabel dibangun ulang (seluruh data akun tetap)."""
    row = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    if not row or "'humas'" in row[0]:
        return
    con.commit()
    con.execute("PRAGMA foreign_keys=OFF")
    try:
        con.execute("BEGIN")
        con.execute(_USERS_BARU)
        con.execute("INSERT INTO users_new(id,username,password_hash,nama,role,aktif,last_login,created_at) "
                    "SELECT id,username,password_hash,nama,role,aktif,last_login,created_at FROM users")
        con.execute("DROP TABLE users")
        con.execute("ALTER TABLE users_new RENAME TO users")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.execute("PRAGMA foreign_keys=ON")


def init_db():
    from .seed import seed
    con = sqlite3.connect(current_app.config["DATABASE"])
    con.execute("PRAGMA journal_mode=WAL")      # baca/tulis bersamaan dari banyak perangkat
    with open(os.path.join(current_app.root_path, "schema.sql"), encoding="utf-8") as f:
        con.executescript(f.read())
    _migrate(con)
    seed(con)
    con.commit()
    con.close()


# ---------------------------------------------------------------- sesi & RBAC
def current_user():
    if "user" not in g:
        uid = session.get("uid")
        g.user = q("SELECT * FROM users WHERE id=? AND aktif=1", (uid,), one=True) if uid else None
    return g.user


def roles_required(*roles):
    """Wajib login; bila `roles` diisi, peran pengguna harus termasuk di dalamnya."""
    def deco(f):
        @wraps(f)
        def wrapper(*a, **k):
            u = current_user()
            if not u:
                nxt = request.full_path.rstrip("?") if request.method == "GET" else None
                return redirect(url_for("main.login", next=nxt))
            if roles and u["role"] not in roles:
                abort(403)
            return f(*a, **k)
        return wrapper
    return deco


def login_required(f):
    return roles_required()(f)


def safe_next(url, default="/"):
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return default


def safe_back(default="/"):
    ref = request.referrer
    if ref:
        p = urlparse(ref)
        if p.netloc == request.host:
            return p.path + ("?" + p.query if p.query else "")
    return default


# ---------------------------------------------------------------- CSRF
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
        real = session.get("csrf") or ""
        if not real or not hmac.compare_digest(sent, real):
            abort(400, "Token keamanan (CSRF) tidak valid atau kedaluwarsa. Muat ulang halaman lalu coba lagi.")


# ---------------------------------------------------------------- pembatasan login
_FAILS = {}


def login_locked(key):
    c = _FAILS.get(key)
    if c and time.time() - c[1] > 300:
        _FAILS.pop(key, None)
        return False
    return bool(c and c[0] >= 5)


def login_fail(key):
    c = _FAILS.get(key)
    if not c or time.time() - c[1] > 300:
        _FAILS[key] = [1, time.time()]
    else:
        c[0] += 1


def login_reset(key):
    _FAILS.pop(key, None)


# ---------------------------------------------------------------- validasi input
def text(name, label, max=200, required=True, pattern=None):
    v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", (request.form.get(name) or "").strip())
    if not v:
        if required:
            raise Invalid(f"{label} wajib diisi.")
        return None
    if len(v) > max:
        raise Invalid(f"{label} maksimal {max} karakter.")
    if pattern and not re.fullmatch(pattern, v):
        raise Invalid(f"Format {label} tidak valid.")
    return v


def choice(name, label, options, required=True):
    v = request.form.get(name) or ""
    if not v and not required:
        return None
    if v not in options:
        raise Invalid(f"Pilihan {label} tidak valid.")
    return v


def num(name, label, lo=0, hi=10 ** 10, required=True):
    raw = (request.form.get(name) or "").strip()
    if not raw and not required:
        return None
    if not re.fullmatch(r"\d{1,12}", raw) or not lo <= int(raw) <= hi:
        raise Invalid(f"{label} harus berupa angka bulat antara {lo} dan {hi}.")
    return int(raw)


def flt(name, label, lo, hi):
    raw = (request.form.get(name) or "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        raise Invalid(f"{label} tidak valid.")
    if not lo <= v <= hi:
        raise Invalid(f"{label} di luar rentang.")
    return v


def datestr(name, label, required=True):
    raw = (request.form.get(name) or "").strip()
    if not raw and not required:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise Invalid(f"Tanggal {label} tidak valid.")


def hash_pw(pw):
    """PBKDF2-SHA256: tersedia di semua build Python (scrypt bawaan Werkzeug tidak selalu ada)."""
    from werkzeug.security import generate_password_hash
    return generate_password_hash(pw, method="pbkdf2:sha256:600000")


def validate_password(pw):
    if len(pw) < 8 or len(pw) > 128 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        raise Invalid("Kata sandi minimal 8 karakter dan harus memuat huruf serta angka.")


def save_image(field, required=False):
    """Simpan gambar unggahan setelah memeriksa isi berkas (bukan sekadar ekstensi)."""
    f = request.files.get(field)
    if not f or not f.filename:
        if required:
            raise Invalid("Berkas gambar wajib diunggah.")
        return None
    head = f.stream.read(12)
    f.stream.seek(0)
    if head[:3] == b"\xff\xd8\xff":
        ext = "jpg"
    elif head[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        ext = "webp"
    else:
        raise Invalid("Format gambar harus JPG, PNG, atau WEBP.")
    name = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(current_app.config["UPLOAD_FOLDER"], exist_ok=True)
    f.save(os.path.join(current_app.config["UPLOAD_FOLDER"], name))
    return name


# ---------------------------------------------------------------- notifikasi, log, pengaturan
def notify(user_ids, judul, pesan="", link=None):
    ids = {i for i in user_ids if i}
    for i in ids:
        db().execute("INSERT INTO notifikasi(user_id,judul,pesan,link) VALUES(?,?,?,?)", (i, judul, pesan, link))
    db().commit()


def users_by_role(*roles):
    marks = ",".join("?" * len(roles))
    return [r["id"] for r in q(f"SELECT id FROM users WHERE aktif=1 AND role IN ({marks})", roles)]


def user_of_warga(warga_id):
    return scalar("SELECT user_id FROM warga WHERE id=?", (warga_id,))


def log(aksi, detail=""):
    ex("INSERT INTO activity_log(user_id,aksi,detail,ip) VALUES(?,?,?,?)",
       (session.get("uid"), aksi, (detail or "")[:300], request.remote_addr))


def get_settings():
    if "settings" not in g:
        g.settings = {r["nama"]: r["nilai"] for r in q("SELECT nama,nilai FROM settings")}
    return g.settings


def my_warga_id():
    u = current_user()
    return scalar("SELECT id FROM warga WHERE user_id=?", (u["id"],)) if u else None


def ensure_warga(uid, nama):
    if not scalar("SELECT 1 FROM warga WHERE user_id=?", (uid,)):
        ex("INSERT INTO warga(user_id,nama) VALUES(?,?)", (uid, nama))


def can_see_laporan(u, lap):
    """Pelapor melihat laporannya sendiri; Humas (menangani) dan Ketua RT (memantau) melihat semuanya."""
    return u["role"] in ("humas", "ketua") or lap["user_id"] == u["id"]


# ---------------------------------------------------------------- format & statistik
def rupiah(n):
    return "Rp " + f"{int(n or 0):,}".replace(",", ".")


def tgl(s, jam=False):
    if not s:
        return "-"
    try:
        d = datetime.fromisoformat(str(s)[:19])
    except ValueError:
        return str(s)
    return f"{d.day} {BLN[d.month - 1]} {d.year}" + (d.strftime(" %H:%M") if jam else "")


def bulan_label(ym):
    y, m = ym.split("-")
    return f"{BLN[int(m) - 1]} {y}"


def last_months(n=6):
    d, out = date.today().replace(day=1), []
    for _ in range(n):
        out.append(d.strftime("%Y-%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    return out[::-1]


def monthly(tipe):
    rows = {r["m"]: r["t"] for r in q(
        "SELECT substr(tanggal,1,7) m, SUM(nominal) t FROM transaksi WHERE tipe=? GROUP BY m", (tipe,))}
    return [(bulan_label(m), rows.get(m, 0)) for m in last_months()]


def saldo():
    return scalar("SELECT COALESCE(SUM(CASE tipe WHEN 'pemasukan' THEN nominal ELSE -nominal END),0) FROM transaksi")
