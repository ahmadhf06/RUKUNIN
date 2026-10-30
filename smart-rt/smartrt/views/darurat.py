"""Alarm Darurat – tiga tombol alarm (bencana alam, kebakaran, maling) dengan sirene MP3 masing-masing.

Endpoints:
  GET  /darurat                          – halaman panel alarm
  GET  /api/darurat/status               – status alarm aktif (JSON, untuk pengguna login)
  POST /api/darurat/aktifkan             – aktifkan alarm (ketua)      body JSON: {kode, pesan}
  POST /api/darurat/stop                 – hentikan alarm (ketua)
  GET  /api/darurat/sirene/<kode>        – putar / unduh sirene milik tombol <kode>
  POST /api/darurat/upload-sirene/<kode> – ganti sirene milik tombol <kode> (ketua / admin)
"""
import os

from flask import (Blueprint, abort, current_app, jsonify, render_template,
                   request, send_file, url_for)

from ..core import (current_user, ex, log, login_required, notify, q,
                    roles_required)

bp = Blueprint("darurat", __name__)

# ---------------------------------------------------------------- jenis alarm (3 tombol)
JENIS = {
    "bencana": dict(
        label="Bencana Alam", ikon="🌪️", warna="#0369a1",
        info="Gempa bumi, banjir, tanah longsor, angin kencang, dan bencana alam lainnya.",
        pesan="Terjadi bencana alam. Warga dimohon tetap tenang, waspada, dan mengikuti arahan petugas."),
    "kebakaran": dict(
        label="Kebakaran", ikon="🔥", warna="#dc2626",
        info="Ada kebakaran di lingkungan RT. Segera evakuasi dan hubungi pemadam kebakaran.",
        pesan="Terjadi kebakaran. Segera menjauh dari lokasi, selamatkan diri, dan hubungi pemadam kebakaran."),
    "maling": dict(
        label="Maling", ikon="🥷", warna="#7c3aed",
        info="Ada indikasi pencurian atau orang mencurigakan di lingkungan RT.",
        pesan="Ada indikasi pencurian di lingkungan RT. Warga dimohon waspada dan mengunci rumah masing-masing."),
}
_KODE_DARI_LABEL = {v["label"]: k for k, v in JENIS.items()}

# Format sirene yang diterima (dikenali dari isi berkas, bukan dari ekstensi)
_SIREN_EXT = ("mp3", "wav", "ogg", "webm", "m4a")
_SIREN_MIME = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
               "webm": "audio/webm", "m4a": "audio/mp4"}


def _jenis_atau_404(kode: str) -> dict:
    if kode not in JENIS:
        abort(404)
    return JENIS[kode]


def _darurat_dir() -> str:
    d = os.path.join(current_app.config["UPLOAD_FOLDER"], "darurat")
    os.makedirs(d, exist_ok=True)
    return d


def _siren_path(kode: str) -> str:
    """Path file sirene milik tombol <kode> (ekstensi mengikuti format yang diunggah)."""
    d = _darurat_dir()
    for ext in _SIREN_EXT:
        p = os.path.join(d, f"sirene_{kode}.{ext}")
        if os.path.isfile(p):
            return p
    return os.path.join(d, f"sirene_{kode}.mp3")          # belum ada sirene


def _detect_siren_ext(head: bytes):
    """Kenali format audio dari isi berkas. None bila tidak dikenali."""
    if head[:4] == b"\x1aEw\xa3" or b"webm" in head[:12]:
        return "webm"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0 and head[1] & 0x06):
        return "mp3"
    return None


def _alarm_aktif():
    """Kembalikan baris alarm yang sedang aktif, atau None."""
    return q("SELECT * FROM alarm_darurat WHERE status='aktif' ORDER BY id DESC LIMIT 1", one=True)


def _kode_alarm(alarm) -> str:
    """Kode tombol dari alarm aktif. Alarm lama bertipe bebas memakai sirene bencana."""
    return _KODE_DARI_LABEL.get(alarm["jenis"], "bencana") if alarm else ""


# ---------------------------------------------------------------- halaman utama
@bp.route("/darurat")
@login_required
def panel():
    u = current_user()
    alarm = _alarm_aktif()
    logs = q(
        "SELECT a.*, u.nama AS aktivator, s.nama AS penghenti "
        "FROM alarm_darurat a "
        "LEFT JOIN users u ON u.id = a.diaktifkan_oleh "
        "LEFT JOIN users s ON s.id = a.dihentikan_oleh "
        "ORDER BY a.id DESC LIMIT 20"
    )
    jenis_list = [dict(kode=k, siren_ada=os.path.isfile(_siren_path(k)), **v) for k, v in JENIS.items()]
    return render_template("darurat.html", alarm=alarm, alarm_kode=_kode_alarm(alarm),
                           logs=logs, jenis_list=jenis_list, u=u)


# ---------------------------------------------------------------- API status (JSON)
@bp.get("/api/darurat/status")
@login_required
def api_status():
    alarm = _alarm_aktif()
    if not alarm:
        return jsonify(aktif=False)
    return jsonify(
        aktif=True,
        id=alarm["id"],
        jenis=alarm["jenis"],
        kode=_kode_alarm(alarm),
        pesan=alarm["pesan"],
        diaktifkan_pada=alarm["diaktifkan_pada"],
    )


# ---------------------------------------------------------------- aktifkan alarm
@bp.post("/api/darurat/aktifkan")
@roles_required("ketua")
def aktifkan():
    """Aktifkan alarm sesuai tombol yang ditekan, lalu beri notifikasi ke semua pengguna aktif."""
    if _alarm_aktif():
        return jsonify(ok=False, pesan="Alarm sedang aktif. Hentikan dulu sebelum mengaktifkan yang baru."), 409

    data = request.get_json(silent=True) or {}
    kode = (data.get("kode") or "").strip()
    if kode not in JENIS:
        return jsonify(ok=False, pesan="Jenis alarm tidak valid."), 400
    j = JENIS[kode]
    pesan = (data.get("pesan") or "").strip()[:500] or j["pesan"]

    u = current_user()
    aid = ex(
        "INSERT INTO alarm_darurat(jenis, pesan, status, diaktifkan_oleh) VALUES(?, ?, 'aktif', ?)",
        (j["label"], pesan, u["id"])
    )
    log("darurat_aktifkan", f"#{aid} jenis={j['label']}")

    semua = [r["id"] for r in q("SELECT id FROM users WHERE aktif=1 AND id<>?", (u["id"],))]
    notify(semua, f"🚨 ALARM DARURAT: {j['label']}", pesan, url_for("darurat.panel"))
    return jsonify(ok=True, id=aid)


# ---------------------------------------------------------------- stop alarm
@bp.post("/api/darurat/stop")
@roles_required("ketua")
def stop_alarm():
    """Hentikan alarm yang sedang aktif."""
    alarm = _alarm_aktif()
    if not alarm:
        return jsonify(ok=False, pesan="Tidak ada alarm yang sedang aktif."), 404

    u = current_user()
    ex(
        "UPDATE alarm_darurat SET status='selesai', dihentikan_oleh=?, "
        "dihentikan_pada=datetime('now','localtime') WHERE id=?",
        (u["id"], alarm["id"])
    )
    log("darurat_stop", f"#{alarm['id']}")

    semua = [r["id"] for r in q("SELECT id FROM users WHERE aktif=1 AND id<>?", (u["id"],))]
    notify(semua, "✅ Alarm darurat dihentikan",
           f"Situasi '{alarm['jenis']}' telah dinyatakan selesai oleh petugas.",
           url_for("darurat.panel"))
    return jsonify(ok=True)


# ---------------------------------------------------------------- sirene per tombol
@bp.get("/api/darurat/sirene/<kode>")
@login_required
def serve_sirene(kode):
    """Sajikan file sirene milik tombol <kode>."""
    _jenis_atau_404(kode)
    path = _siren_path(kode)
    if not os.path.isfile(path):
        abort(404)
    mime = _SIREN_MIME.get(path.rsplit(".", 1)[-1], "audio/mpeg")
    return send_file(path, mimetype=mime, conditional=True)


@bp.post("/api/darurat/upload-sirene/<kode>")
@roles_required("ketua", "admin")
def upload_sirene(kode):
    """Ganti sirene milik tombol <kode> (MP3 / WAV / OGG / WebM / M4A, maks. 5 MB)."""
    j = _jenis_atau_404(kode)
    f = request.files.get("sirene")
    if not f or not f.filename:
        return jsonify(ok=False, pesan="Berkas tidak ditemukan."), 400

    head = f.stream.read(12)
    f.stream.seek(0)
    ext = _detect_siren_ext(head)
    if not ext:
        return jsonify(ok=False, pesan="Format harus MP3 (atau WAV, OGG, WebM, M4A)."), 400

    d = _darurat_dir()
    f.save(os.path.join(d, f"sirene_{kode}.{ext}"))
    for e in _SIREN_EXT:                       # hapus sirene lama yang formatnya berbeda
        if e != ext:
            try:
                os.remove(os.path.join(d, f"sirene_{kode}.{e}"))
            except OSError:
                pass
    log("darurat_upload_sirene", f"{j['label']} ({ext})")
    return jsonify(ok=True)
