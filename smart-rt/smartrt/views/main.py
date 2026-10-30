"""Login/logout, dashboard per peran, notifikasi, profil, dan API JSON."""
import re
import sqlite3
from datetime import date

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash

from ..core import (ROLES, hash_pw, STATUS, Invalid, can_see_laporan, choice, csrf_token, current_user, datestr, db, ex,
                    log, login_fail, login_locked, login_required, login_reset, monthly, my_warga_id, notify, q,
                    rupiah, safe_next, saldo, scalar, text, users_by_role, validate_password)

bp = Blueprint("main", __name__)
_DUMMY = hash_pw("dummy-password-1")      # menyamakan waktu respons login


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()[:50]
        pw = request.form.get("password") or ""
        key = (request.remote_addr, username)
        if login_locked(key):
            flash("Terlalu banyak percobaan gagal. Coba lagi dalam 5 menit.", "error")
        else:
            u = q("SELECT * FROM users WHERE username=?", (username,), one=True)
            ok = check_password_hash(u["password_hash"] if u else _DUMMY, pw) and u and u["aktif"]
            if ok:
                login_reset(key)
                session.clear()                       # cegah session fixation
                session["uid"] = u["id"]
                session.permanent = True
                csrf_token()
                ex("UPDATE users SET last_login=datetime('now','localtime') WHERE id=?", (u["id"],))
                log("login", username)
                return redirect(safe_next(request.args.get("next"), url_for("main.dashboard")))
            login_fail(key)
            log("login_gagal", username)
            flash("Username atau kata sandi salah, atau akun nonaktif.", "error")
    return render_template("login.html")


# ---------------------------------------------------------------- pendaftaran warga mandiri
@bp.route("/daftar", methods=["GET", "POST"])
def daftar():
    """Warga membuat akun + mengisi data diri. Data langsung masuk ke Data Warga milik Ketua RT."""
    if current_user():
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        key = ("daftar", request.remote_addr)
        try:
            if login_locked(key):
                raise Invalid("Terlalu banyak pendaftaran dari perangkat ini. Coba lagi dalam 5 menit.")
            username = (text("username", "Username", 30, pattern=r"[A-Za-z0-9_.]{3,30}") or "").lower()
            pw = request.form.get("password") or ""
            validate_password(pw)
            if pw != (request.form.get("konfirmasi") or ""):
                raise Invalid("Konfirmasi kata sandi tidak cocok.")
            d = dict(
                nik=text("nik", "NIK", 16, pattern=r"\d{16}"),
                kk=text("kk", "No. KK", 16, required=False, pattern=r"\d{16}"),
                nama=text("nama", "Nama lengkap", 100),
                jk=choice("jk", "jenis kelamin", ("L", "P")),
                tempat_lahir=text("tempat_lahir", "Tempat lahir", 60, required=False),
                tgl_lahir=datestr("tgl_lahir", "lahir", required=False),
                alamat=text("alamat", "Alamat", 150),
                telepon=text("telepon", "Telepon", 20, pattern=r"[0-9+\- ]{8,20}"),
                pekerjaan=text("pekerjaan", "Pekerjaan", 60, required=False),
                status_tinggal=choice("status_tinggal", "status tinggal", ("Tetap", "Kontrak", "Kos")))
            if scalar("SELECT 1 FROM users WHERE username=?", (username,)):
                raise Invalid("Username sudah dipakai, silakan pilih yang lain.")
            if scalar("SELECT 1 FROM warga WHERE nik=?", (d["nik"],)):
                raise Invalid("NIK sudah terdaftar. Jika itu NIK Anda, hubungi Ketua RT.")
            con = db()
            try:                                           # akun + data warga disimpan sekaligus (atomik)
                uid = ex("INSERT INTO users(username,password_hash,nama,role) VALUES(?,?,?,'warga')",
                         (username, hash_pw(pw), d["nama"]), commit=False)
                cols = ["user_id", *d]
                ex(f"INSERT INTO warga({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                   [uid, *d.values()], commit=False)
                con.commit()
            except sqlite3.IntegrityError:
                con.rollback()
                raise Invalid("Username atau NIK sudah terdaftar.")
        except Invalid as e:
            flash(str(e), "error")
            return render_template("daftar.html", v=dict(request.form))
        login_fail(key)                                    # hitung pendaftaran sukses (anti spam per perangkat)
        notify(users_by_role("ketua"), "Warga baru mendaftar",
               f"{d['nama']} ({d['alamat']}) mendaftar mandiri. Datanya sudah masuk ke Data Warga.",
               url_for("warga.index", q=d["nama"]))
        log("daftar_warga", f"{username} / {d['nama']}")
        flash("Pendaftaran berhasil. Silakan masuk dengan username dan kata sandi Anda.", "ok")
        return redirect(url_for("main.login"))
    return render_template("daftar.html", v={})


@bp.post("/logout")
@login_required
def logout():
    log("logout")
    session.clear()
    flash("Anda telah keluar.", "ok")
    return redirect(url_for("main.login"))


# ---------------------------------------------------------------- dashboard
def _by_status(table, kind, where="", args=()):
    got = {r["k"]: r["n"] for r in q(f"SELECT status k, COUNT(*) n FROM {table} {where} GROUP BY status", args)}
    return [(lbl, got.get(k, 0)) for k, (lbl, _) in STATUS[kind].items()]


def stats_for(u):
    """Kartu & grafik dashboard sesuai peran. Dipakai halaman dashboard dan /api/stats."""
    role, uid = u["role"], u["id"]
    today, bulan = date.today().isoformat(), date.today().strftime("%Y-%m")
    cards, charts = [], []

    def card(key, label, value, icon, link=None):
        cards.append(dict(key=key, label=label, value=str(value), icon=icon, link=link))

    def cnt(sql, a=()):
        return scalar(sql, a) or 0

    if role == "admin":
        card("akun", "Akun aktif", cnt("SELECT COUNT(*) FROM users WHERE aktif=1"), "users", url_for("admin.akun"))
        card("nonaktif", "Akun nonaktif", cnt("SELECT COUNT(*) FROM users WHERE aktif=0"), "key", url_for("admin.akun"))
        card("login24", "Login 24 jam terakhir",
             cnt("SELECT COUNT(*) FROM users WHERE last_login>=datetime('now','localtime','-1 day')"), "user")
        card("aktivitas", "Aktivitas hari ini",
             cnt("SELECT COUNT(*) FROM activity_log WHERE date(created_at)=date('now','localtime')"), "list",
             url_for("admin.log_aktivitas"))
        got = {r["role"]: r["n"] for r in q("SELECT role, COUNT(*) n FROM users WHERE aktif=1 GROUP BY role")}
        charts.append(dict(title="Akun aktif per peran", items=[(ROLES[k], got.get(k, 0)) for k in ROLES]))
    elif role == "ketua":
        card("warga", "Warga aktif", cnt("SELECT COUNT(*) FROM warga WHERE aktif=1"), "users", url_for("warga.index"))
        card("surat", "Surat menunggu persetujuan", cnt("SELECT COUNT(*) FROM surat WHERE status='menunggu_ketua'"),
             "file", url_for("surat.index", status="menunggu_ketua"))
        card("laporan", "Laporan berjalan", cnt("SELECT COUNT(*) FROM laporan WHERE status IN ('baru','diproses')"),
             "alert", url_for("laporan.index"))
        card("saldo", "Saldo kas RT", rupiah(saldo()), "wallet", url_for("keuangan.index"))
        charts += [dict(title="Laporan per status", items=_by_status("laporan", "laporan")),
                   dict(title="Aspirasi per status", items=_by_status("aspirasi", "aspirasi")),
                   dict(title="Surat per status", items=_by_status("surat", "surat")),
                   dict(title="Pemasukan 6 bulan terakhir", items=monthly("pemasukan"), money=True),
                   dict(title="Pengeluaran 6 bulan terakhir", items=monthly("pengeluaran"), money=True)]
    elif role == "sekretaris":
        card("periksa", "Perlu diperiksa", cnt("SELECT COUNT(*) FROM surat WHERE status='diajukan'"), "file",
             url_for("surat.index", status="diajukan"))
        card("ketua", "Menunggu Ketua RT", cnt("SELECT COUNT(*) FROM surat WHERE status='menunggu_ketua'"), "list",
             url_for("surat.index", status="menunggu_ketua"))
        card("arsip", "Siap diarsipkan", cnt("SELECT COUNT(*) FROM surat WHERE status='disetujui'"), "check",
             url_for("surat.index", status="disetujui"))
        card("selesai", "Arsip bulan ini", cnt("SELECT COUNT(*) FROM surat WHERE status='selesai' AND substr(updated_at,1,7)=?", (bulan,)),
             "megaphone", url_for("surat.index", status="selesai"))
        charts.append(dict(title="Surat per status", items=_by_status("surat", "surat")))
        charts.append(dict(title="Surat per jenis", items=[(r["jenis"], r["n"]) for r in q(
            "SELECT jenis, COUNT(*) n FROM surat GROUP BY jenis ORDER BY n DESC LIMIT 6")]))
    elif role == "bendahara":
        card("saldo", "Saldo kas RT", rupiah(saldo()), "wallet", url_for("keuangan.index"))
        card("masuk", "Pemasukan bulan ini", rupiah(cnt(
            "SELECT SUM(nominal) FROM transaksi WHERE tipe='pemasukan' AND substr(tanggal,1,7)=?", (bulan,))), "plus")
        card("keluar", "Pengeluaran bulan ini", rupiah(cnt(
            "SELECT SUM(nominal) FROM transaksi WHERE tipe='pengeluaran' AND substr(tanggal,1,7)=?", (bulan,))), "receipt")
        card("verif", "Menunggu verifikasi", cnt("SELECT COUNT(*) FROM tagihan WHERE status='menunggu_verifikasi'"),
             "check", url_for("tagihan.index", status="menunggu_verifikasi"))
        card("tunggak", "Tagihan belum dibayar", cnt("SELECT COUNT(*) FROM tagihan WHERE status IN ('belum_bayar','ditolak')"),
             "alert", url_for("tagihan.index", status="belum_bayar"))
        charts += [dict(title="Tagihan per status", items=_by_status("tagihan", "tagihan")),
                   dict(title="Pemasukan 6 bulan terakhir", items=monthly("pemasukan"), money=True)]
    elif role == "humas":
        card("baru", "Laporan baru", cnt("SELECT COUNT(*) FROM laporan WHERE status='baru'"), "alert",
             url_for("laporan.index", status="baru"))
        card("proses", "Laporan diproses", cnt("SELECT COUNT(*) FROM laporan WHERE status='diproses'"), "list",
             url_for("laporan.index", status="diproses"))
        card("aspirasi", "Aspirasi belum ditanggapi", cnt("SELECT COUNT(*) FROM aspirasi WHERE status='baru'"),
             "message", url_for("komunitas.aspirasi"))
        card("selesai", "Laporan selesai bulan ini", cnt(
            "SELECT COUNT(*) FROM laporan WHERE status='selesai' AND substr(updated_at,1,7)=?", (bulan,)),
             "check", url_for("laporan.index", status="selesai"))
        charts += [dict(title="Laporan per status", items=_by_status("laporan", "laporan")),
                   dict(title="Aspirasi per status", items=_by_status("aspirasi", "aspirasi"))]
    else:  # warga
        wid = my_warga_id() or 0
        card("tunggakan", "Tagihan belum dibayar", rupiah(cnt(
            "SELECT SUM(nominal) FROM tagihan WHERE warga_id=? AND status IN ('belum_bayar','ditolak')", (wid,))),
             "receipt", url_for("tagihan.saya"))
        card("surat", "Surat berjalan", cnt(
            "SELECT COUNT(*) FROM surat WHERE warga_id=? AND status NOT IN ('selesai','ditolak')", (wid,)),
             "file", url_for("surat.index"))
        card("laporan", "Laporan saya berjalan", cnt(
            "SELECT COUNT(*) FROM laporan WHERE user_id=? AND status IN ('baru','diproses')", (uid,)),
             "alert", url_for("laporan.index"))
        card("voting", "Voting menunggu suara", cnt(
            "SELECT COUNT(*) FROM voting v WHERE v.status='aktif' AND v.berakhir>=? AND NOT EXISTS "
            "(SELECT 1 FROM voting_suara s WHERE s.voting_id=v.id AND s.user_id=?)", (today, uid)),
             "vote", url_for("komunitas.voting"))
        charts.append(dict(title="Status tagihan saya",
                           items=_by_status("tagihan", "tagihan", "WHERE warga_id=?", (wid,))))
    return cards, charts


@bp.route("/")
@login_required
def dashboard():
    u = current_user()
    cards, charts = stats_for(u)
    pengumuman = q("SELECT * FROM pengumuman ORDER BY id DESC LIMIT 3")
    ronda = q("SELECT r.tanggal, r.pos, w.nama, w.user_id FROM ronda r JOIN warga w ON w.id=r.warga_id "
              "WHERE r.tanggal>=date('now','localtime') ORDER BY r.tanggal, r.id LIMIT 5")
    return render_template("dashboard.html", cards=cards, charts=charts, pengumuman=pengumuman, ronda=ronda)


@bp.get("/api/stats")
def api_stats():
    u = current_user()
    if not u:
        return jsonify(error="auth"), 401
    cards, _ = stats_for(u)
    return jsonify(cards={c["key"]: c["value"] for c in cards})


# ---------------------------------------------------------------- notifikasi
@bp.get("/api/notifikasi")
def api_notifikasi():
    u = current_user()
    if not u:
        return jsonify(error="auth"), 401
    rows = q("SELECT id,judul,pesan,link,dibaca,created_at FROM notifikasi WHERE user_id=? ORDER BY id DESC LIMIT 8",
             (u["id"],))
    unread = scalar("SELECT COUNT(*) FROM notifikasi WHERE user_id=? AND dibaca=0", (u["id"],))
    return jsonify(unread=unread, items=[dict(r) for r in rows])


@bp.route("/notifikasi")
@login_required
def notifikasi():
    rows = q("SELECT * FROM notifikasi WHERE user_id=? ORDER BY id DESC LIMIT 100", (current_user()["id"],))
    return render_template("notifikasi.html", rows=rows)


@bp.post("/notifikasi/baca-semua")
@login_required
def notifikasi_baca_semua():
    ex("UPDATE notifikasi SET dibaca=1 WHERE user_id=?", (current_user()["id"],))
    return redirect(url_for("main.notifikasi"))


@bp.get("/notifikasi/<int:nid>/buka")
@login_required
def notifikasi_buka(nid):
    n = q("SELECT * FROM notifikasi WHERE id=? AND user_id=?", (nid, current_user()["id"]), one=True)
    if not n:
        abort(404)
    ex("UPDATE notifikasi SET dibaca=1 WHERE id=?", (nid,))
    return redirect(safe_next(n["link"], url_for("main.notifikasi")))


# ---------------------------------------------------------------- profil
@bp.route("/profil", methods=["GET", "POST"])
@login_required
def profil():
    u = current_user()
    if request.method == "POST":
        if request.form.get("aksi") == "password":
            lama, baru = request.form.get("lama", ""), request.form.get("baru", "")
            if not check_password_hash(u["password_hash"], lama):
                raise Invalid("Kata sandi lama salah.")
            if baru != request.form.get("konfirmasi", ""):
                raise Invalid("Konfirmasi kata sandi tidak cocok.")
            validate_password(baru)
            ex("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(baru), u["id"]))
            log("ganti_password")
            flash("Kata sandi berhasil diganti.", "ok")
        else:
            nama = text("nama", "Nama", 100)
            telp = text("telepon", "Telepon", 20, required=False, pattern=r"[0-9+\- ]{8,20}")
            ex("UPDATE users SET nama=? WHERE id=?", (nama, u["id"]))
            if u["role"] == "warga":
                ex("UPDATE warga SET telepon=? WHERE user_id=?", (telp, u["id"]))
            flash("Profil diperbarui.", "ok")
        return redirect(url_for("main.profil"))
    w = q("SELECT * FROM warga WHERE user_id=?", (u["id"],), one=True)
    return render_template("profil.html", w=w)


# ---------------------------------------------------------------- berkas unggahan (dijaga RBAC)
@bp.get("/media/<name>")
@login_required
def media(name):
    if not re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|webp)", name):
        abort(404)
    u = current_user()
    lap = q("SELECT * FROM laporan WHERE foto=?", (name,), one=True)
    if lap:
        if not can_see_laporan(u, lap):
            abort(403)
    else:
        t = q("SELECT w.user_id FROM tagihan t JOIN warga w ON w.id=t.warga_id WHERE t.bukti=?", (name,), one=True)
        if not t:
            abort(404)
        if u["role"] not in ("bendahara", "ketua") and t["user_id"] != u["id"]:
            abort(403)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], name)
