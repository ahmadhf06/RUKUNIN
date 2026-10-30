"""Uji end-to-end: jalankan dengan `python -m pytest -q` dari folder proyek."""
import io
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from smartrt import create_app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PW = {"admin": "Admin#2026", "ketua": "Ketua#2026", "sekretaris": "Sekre#2026",
      "bendahara": "Bendahara#2026", "humas": "Humas#2026", "warga": "Warga#2026", "warga2": "Warga#2026"}


@pytest.fixture()
def app(tmp_path):
    from smartrt import core
    core._FAILS.clear()
    return create_app({"DATABASE": str(tmp_path / "t.db"), "UPLOAD_FOLDER": str(tmp_path / "up"), "TESTING": True})


def tok(c):
    with c.session_transaction() as s:
        return s["csrf"]


def login(app, user):
    c = app.test_client()
    c.get("/login")
    r = c.post("/login", data={"username": user, "password": PW[user], "csrf_token": tok(c)})
    assert r.status_code == 302, user
    return c


def post(c, url, data=None, **kw):
    d = dict(data or {})
    d["csrf_token"] = tok(c)
    return c.post(url, data=d, **kw)


def test_login_csrf_and_lockout(app):
    c = app.test_client()
    c.get("/login")
    assert c.post("/login", data={"username": "admin", "password": "x"}).status_code == 400   # tanpa CSRF
    for _ in range(5):
        post(c, "/login", {"username": "ghost", "password": "salah"})
    assert b"Terlalu banyak" in post(c, "/login", {"username": "ghost", "password": "salah"}).data
    assert app.test_client().get("/surat/").status_code == 302                                  # wajib login


def test_rbac(app):
    w, s = login(app, "warga"), login(app, "sekretaris")
    for url in ("/admin/akun", "/keuangan/", "/warga/", "/tagihan", "/warga/baru"):
        assert w.get(url).status_code == 403, url
    assert s.get("/keuangan/").status_code == 403
    assert login(app, "bendahara").get("/surat/").status_code == 403
    assert post(s, "/pengumuman", {"judul": "x", "isi": "y"}).status_code == 403


def test_surat_flow(app):
    w, s, k = login(app, "warga"), login(app, "sekretaris"), login(app, "ketua")
    r = post(w, "/surat/baru", {"jenis": "Surat Keterangan Domisili", "keperluan": "Pindah kerja"})
    sid = int(re.search(r"/surat/(\d+)", r.headers["Location"]).group(1))
    assert b"Periksa" not in w.get(f"/surat/{sid}").data
    assert login(app, "warga2").get(f"/surat/{sid}").status_code == 403          # warga lain tidak boleh
    post(k, f"/surat/{sid}/aksi", {"aksi": "setujui"})                              # Ketua tak bisa lompati Sekretaris
    assert b"Diajukan" in k.get(f"/surat/{sid}").data
    assert post(s, f"/surat/{sid}/aksi", {"aksi": "setujui"}).status_code == 403   # Sekretaris tak bisa menyetujui
    page = s.get(f"/surat/{sid}").data.decode()
    draf = re.search(r'name="draf"[^>]*>(.*?)</textarea>', page, re.S).group(1)
    nomor = re.search(r'name="nomor" id="nomor" value="([^"]+)"', page).group(1)
    assert post(s, f"/surat/{sid}/aksi", {"aksi": "teruskan", "nomor": nomor, "draf": "terlalu pendek"}).status_code == 302
    assert b"menunggu_ketua" not in s.get(f"/surat/{sid}").data  # masih diajukan karena draf pendek ditolak
    import html
    post(s, f"/surat/{sid}/aksi", {"aksi": "teruskan", "nomor": nomor, "draf": html.unescape(draf)})
    assert b"Persetujuan Ketua RT" in k.get(f"/surat/{sid}").data
    assert w.get(f"/surat/{sid}/cetak").status_code == 403                          # belum selesai
    post(k, f"/surat/{sid}/aksi", {"aksi": "setujui"})
    assert post(k, f"/surat/{sid}/aksi", {"aksi": "setujui"}).status_code == 302   # status berubah -> ditolak lembut
    post(s, f"/surat/{sid}/aksi", {"aksi": "arsipkan"})
    assert w.get(f"/surat/{sid}/cetak").status_code == 200
    n = w.get("/api/notifikasi").get_json()
    assert n["unread"] >= 3
    assert any("selesai" in i["judul"].lower() for i in n["items"])


def test_laporan_foto_dan_akses(app):
    w, k, w2 = login(app, "warga"), login(app, "ketua"), login(app, "warga2")
    bad = post(w, "/laporan/baru", {"judul": "Jalan", "kategori": "Infrastruktur", "deskripsi": "rusak", "lokasi": "Blok A",
                                    "foto": (io.BytesIO(b"<html>not image"), "x.png")}, content_type="multipart/form-data")
    assert bad.status_code == 302 and b"Jalan" not in w.get("/laporan/").data       # berkas palsu ditolak, tak tersimpan
    r = post(w, "/laporan/baru", {"judul": "Jalan berlubang", "kategori": "Infrastruktur", "deskripsi": "rusak parah", "lokasi": "Blok A",
                                  "lat": "-6.2", "lng": "106.8", "foto": (io.BytesIO(PNG), "a.png")}, content_type="multipart/form-data")
    lid = int(re.search(r"/laporan/(\d+)", r.headers["Location"]).group(1))
    img = re.search(r"/media/([0-9a-f]{32}\.png)", w.get(f"/laporan/{lid}").data.decode()).group(0)
    assert w.get(img).status_code == 200 and k.get(img).status_code == 200 and w2.get(img).status_code == 403
    assert w2.get(f"/laporan/{lid}").status_code == 403
    h, sk = login(app, "humas"), login(app, "sekretaris")
    assert h.get(img).status_code == 200                                              # Humas menangani semua laporan
    assert b"Tindak lanjut" in h.get(f"/laporan/{lid}").data and b"Tindak lanjut" not in k.get(f"/laporan/{lid}").data
    assert post(k, f"/laporan/{lid}/update", {"status": "diproses"}).status_code == 403   # Ketua RT hanya memantau
    assert post(h, f"/laporan/{lid}/update", {"status": "diproses", "catatan": "ditangani"}).status_code == 302
    assert b"Diproses" in w.get(f"/laporan/{lid}").data and b"Maya Anggraini" in w.get(f"/laporan/{lid}").data
    assert sk.get(f"/laporan/{lid}").status_code == 403 and sk.get("/laporan/").status_code == 403   # sekretaris tak punya akses
    assert post(w, f"/laporan/{lid}/update", {"status": "selesai"}).status_code == 403


def test_keuangan(app):
    w, b, k = login(app, "warga"), login(app, "bendahara"), login(app, "ketua")
    page = w.get("/tagihan-saya").data.decode()
    tid = re.search(r"/tagihan-saya/(\d+)/bayar", page).group(1)
    assert post(w, f"/tagihan-saya/{tid}/bayar", {"metode": "QRIS"}, content_type="multipart/form-data").status_code == 302  # tanpa bukti
    assert b"Menunggu Verifikasi" not in w.get("/tagihan-saya").data
    post(w, f"/tagihan-saya/{tid}/bayar", {"metode": "QRIS", "bukti": (io.BytesIO(PNG), "b.png")}, content_type="multipart/form-data")
    saldo0 = re.search(r"<b>(Rp [\d.]+)</b><span>Saldo kas", k.get("/keuangan/").data.decode()).group(1)
    assert post(k, f"/tagihan/{tid}/aksi", {"aksi": "terima"}).status_code == 403          # ketua tak boleh verifikasi
    post(b, f"/tagihan/{tid}/aksi", {"aksi": "terima"})
    saldo1 = re.search(r"<b>(Rp [\d.]+)</b><span>Saldo kas", k.get("/keuangan/").data.decode()).group(1)
    assert saldo1 != saldo0
    assert b"Lunas" in w.get("/tagihan-saya").data
    assert post(b, "/keuangan/transaksi", {"tipe": "pengeluaran", "kategori": "Kebersihan", "nominal": "999999999",
                                           "keterangan": "x", "tanggal": "2026-10-21"}).status_code == 302
    post(b, "/keuangan/transaksi", {"tipe": "pengeluaran", "kategori": "Kebersihan", "nominal": "10000",
                                    "keterangan": "sapu", "tanggal": "2026-10-21"})
    assert b"sapu" in b.get("/keuangan/").data
    assert b"Tanggal,Jenis" in k.get("/keuangan/export.csv").data
    post(b, "/tagihan/generate", {"periode": "2027-01", "jenis": "Iuran Bulanan", "nominal": "50000"})
    assert b"Rina Marlina" in b.get("/tagihan?periode=2027-01").data


def test_komunitas(app):
    w, k, s = login(app, "warga"), login(app, "ketua"), login(app, "sekretaris")
    post(k, "/voting", {"judul": "Portal", "berakhir": "2099-01-01", "opsi": "Ya\nTidak"})
    page = w.get("/voting").data.decode()
    vid, oid = re.search(r"/voting/(\d+)/pilih", page).group(1), re.search(r'name="opsi" value="(\d+)"', page).group(1)
    post(w, f"/voting/{vid}/pilih", {"opsi": oid})
    r = post(w, f"/voting/{vid}/pilih", {"opsi": oid}, follow_redirects=True)
    assert b"sudah memberikan suara" in r.data                                    # satu akun satu suara
    assert post(login(app, "bendahara"), f"/voting/{vid}/pilih", {"opsi": oid}).status_code == 403
    post(k, "/pengumuman", {"judul": "Rapat", "isi": "Besok", "penting": "on"})
    assert b"Rapat" in w.get("/pengumuman").data
    post(w, "/aspirasi", {"judul": "Lampu jalan", "isi": "Mohon diganti"})
    assert post(k, "/aspirasi/1/tanggapi", {"tanggapan": "Siap"}).status_code == 403       # Ketua RT hanya memantau
    assert b"Lampu jalan" in k.get("/aspirasi").data
    post(login(app, "humas"), "/aspirasi/1/tanggapi", {"tanggapan": "Siap"})
    assert b"Siap" in w.get("/aspirasi").data and b"Maya Anggraini" in w.get("/aspirasi").data
    r = post(s, "/kegiatan", {"nama": "Kerja Bakti", "jenis": "Kerja Bakti", "tanggal": "2026-10-25", "lokasi": "Taman"})
    kid = re.search(r"/kegiatan/(\d+)", r.headers["Location"]).group(1)
    d = s.get(f"/kegiatan/{kid}").data.decode()
    assert "<svg" in d
    token = re.search(r"/presensi/([\w-]+)", d).group(1)
    assert b"Ya, saya hadir" in w.get(f"/presensi/{token}").data
    post(w, f"/presensi/{token}")
    assert b"Anda hadir" in w.get("/kegiatan").data
    assert b"Budi Santoso" in s.get(f"/kegiatan/{kid}").data


def test_admin_dan_warga(app):
    a, k = login(app, "admin"), login(app, "ketua")
    assert post(a, "/admin/akun", {"username": "baru1", "nama": "Warga Baru", "role": "warga", "password": "abc"}).status_code == 302
    assert b"baru1" not in a.get("/admin/akun").data                               # sandi lemah ditolak
    post(a, "/admin/akun", {"username": "baru1", "nama": "Warga Baru", "role": "warga", "password": "Rahasia123"})
    assert b"baru1" in a.get("/admin/akun").data
    c = app.test_client()
    c.get("/login")
    assert post(c, "/login", {"username": "baru1", "password": "Rahasia123"}).status_code == 302
    me = [x for x in a.get("/admin/akun").data.decode().split("/admin/akun/")[1:] if x]
    assert post(a, "/admin/akun/1", {"nama": "Admin", "role": "warga", "aktif": "on"}).status_code == 302   # tak bisa turunkan diri
    assert b"tidak dapat mengubah" in a.get("/admin/akun").data
    assert b"akun_baru" in a.get("/admin/log").data
    post(k, "/warga/baru", {"nama": "Warga X", "nik": "3201010101900099", "jk": "L", "alamat": "Jl. X", "status_tinggal": "Tetap", "aktif": "on"})
    assert b"Warga X" in k.get("/warga/?q=Warga X").data
    assert b"Warga X" in login(app, "bendahara").get("/warga/").data
    for u in PW:
        cl = login(app, u)
        assert cl.get("/").status_code == 200 and cl.get("/api/stats").get_json()["cards"]
    assert "Content-Security-Policy" in cl.get("/").headers


def test_pendaftaran_warga(app):
    c = app.test_client()
    r = c.get("/login")
    assert b"RUKUNIN" in r.data and b"data-demo" not in r.data and b"Admin#2026" not in r.data
    assert b"Smart RT" not in r.data and b"/daftar" in r.data
    assert c.get("/daftar").status_code == 200
    data = {"username": "Siti_Baru", "password": "Rahasia123", "konfirmasi": "Rahasia123", "nama": "Siti Baru",
            "nik": "3201010101990077", "jk": "P", "alamat": "Jl. Mawar 5", "telepon": "081234567890",
            "status_tinggal": "Tetap"}
    bad = post(c, "/daftar", {**data, "konfirmasi": "beda123"})                     # form dirender ulang, isian tetap
    assert bad.status_code == 200 and b"tidak cocok" in bad.data and b"Siti Baru" in bad.data
    assert b"NIK sudah terdaftar" in post(c, "/daftar", {**data, "nik": "3201010101900001"}).data
    assert post(c, "/daftar", data).status_code == 302
    assert b"Username sudah dipakai" in post(c, "/daftar", {**data, "nik": "3201010101990078"}).data
    # data langsung ada di Ketua RT + notifikasi, tanpa input manual
    k = login(app, "ketua")
    assert b"Siti Baru" in k.get("/warga/?q=Siti").data
    assert b"Warga baru mendaftar" in k.get("/notifikasi").data
    # akun baru bisa masuk sebagai warga dan terhubung ke datanya
    w = app.test_client()
    w.get("/login")
    assert post(w, "/login", {"username": "siti_baru", "password": "Rahasia123"}).status_code == 302
    assert w.get("/").status_code == 200 and b"3201010101990077" in w.get("/profil").data
    assert w.get("/daftar").status_code == 302                                         # sudah login -> dashboard
    assert w.get("/warga/").status_code == 403                                         # tetap peran warga


def test_alarm_tiga_tombol(app):
    import io
    jpost = lambda c, url, body=None: c.post(url, json=body or {}, headers={"X-CSRF-Token": tok(c)})
    k = login(app, "ketua")
    html = k.get("/darurat").data
    assert b"Bencana Alam" in html and b"Kebakaran" in html and b"Maling" in html
    assert b"btnRekam" not in html and b"Mulai Rekam" not in html          # voice note sudah dihapus
    assert jpost(k, "/api/darurat/upload").status_code == 404
    mp3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 200
    up = lambda kode, data, name: post(k, f"/api/darurat/upload-sirene/{kode}", {"sirene": (io.BytesIO(data), name)},
                                       content_type="multipart/form-data")
    assert up("kebakaran", mp3, "api.mp3").get_json()["ok"] is True
    assert k.get("/api/darurat/sirene/kebakaran").mimetype == "audio/mpeg"
    assert k.get("/api/darurat/sirene/maling").status_code == 404              # sirene tiap tombol terpisah
    assert up("maling", b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 50, "m.wav").get_json()["ok"] is True
    assert k.get("/api/darurat/sirene/maling").mimetype == "audio/wav"
    assert up("bencana", b"MZ" + b"\x00" * 50, "x.exe").status_code == 400
    assert up("../etc", mp3, "a.mp3").status_code == 404 and up("lain", mp3, "a.mp3").status_code == 404
    # aktifkan alarm kebakaran
    assert jpost(k, "/api/darurat/aktifkan", {"kode": "ngawur"}).status_code == 400
    r = jpost(k, "/api/darurat/aktifkan", {"kode": "kebakaran", "pesan": "Blok C"})
    assert r.get_json()["ok"] is True
    st = k.get("/api/darurat/status").get_json()
    assert st["aktif"] and st["kode"] == "kebakaran" and st["jenis"] == "Kebakaran"
    assert jpost(k, "/api/darurat/aktifkan", {"kode": "maling"}).status_code == 409
    w = login(app, "warga")                                                    # warga ikut menerima notifikasi
    assert b"ALARM DARURAT: Kebakaran" in w.get("/notifikasi").data
    assert jpost(w, "/api/darurat/aktifkan", {"kode": "maling"}).status_code == 403
    assert post(w, "/api/darurat/upload-sirene/kebakaran", {"sirene": (io.BytesIO(mp3), "a.mp3")},
                content_type="multipart/form-data").status_code == 403
    assert jpost(k, "/api/darurat/stop").get_json()["ok"] is True
    assert k.get("/api/darurat/status").get_json()["aktif"] is False


def test_humas_hak_akses(app):
    h = login(app, "humas")
    for url in ("/", "/laporan/", "/aspirasi", "/pengumuman", "/ronda", "/voting", "/kegiatan", "/darurat", "/profil"):
        assert h.get(url).status_code == 200, url
    for url in ("/warga/", "/surat/", "/keuangan/", "/tagihan", "/tagihan-saya", "/admin/akun", "/laporan/baru"):
        assert h.get(url).status_code == 403, url
    page = h.get("/").data
    assert b"Laporan baru" in page and b"Aspirasi belum ditanggapi" in page
    nav = h.get("/aspirasi").data
    assert b"Aspirasi" in nav and b"Data Warga" not in nav and b"Keuangan" not in nav
    assert post(h, "/pengumuman", {"judul": "x", "isi": "y"}).status_code == 403
    assert post(h, "/voting", {"judul": "x", "berakhir": "2099-01-01", "opsi": "a\nb"}).status_code == 403
    # warga melapor / beraspirasi -> Humas dan Ketua RT menerima notifikasi
    w = login(app, "warga")
    post(w, "/aspirasi", {"judul": "Parkir", "isi": "Mohon ditertibkan"})
    post(w, "/laporan/baru", {"judul": "Got mampet", "kategori": "Kebersihan", "deskripsi": "banjir", "lokasi": "Blok B"},
         content_type="multipart/form-data")
    assert b"Aspirasi warga baru" in h.get("/notifikasi").data and b"Laporan lingkungan baru" in h.get("/notifikasi").data
    assert b"Laporan lingkungan baru" in login(app, "ketua").get("/notifikasi").data
    # admin dapat membuat akun berperan Humas
    a = login(app, "admin")
    assert post(a, "/admin/akun", {"username": "humas2", "nama": "Humas Dua", "role": "humas",
                                    "password": "Rahasia123"}).status_code == 302
    assert b"Humas Dua" in a.get("/admin/akun").data


def test_migrasi_database_lama(tmp_path):
    """Database lama (CHECK tanpa 'humas') dimigrasi tanpa kehilangan data, lalu dapat menampung peran Humas."""
    import sqlite3
    path = tmp_path / "lama.db"
    schema = open("smartrt/schema.sql", encoding="utf-8").read().replace("'bendahara','humas','warga'", "'bendahara','warga'")
    con = sqlite3.connect(path)
    con.executescript(schema)
    con.execute("INSERT INTO users(username,password_hash,nama,role) VALUES('lama','x','Akun Lama','warga')")
    con.execute("INSERT INTO warga(user_id,nik,nama) VALUES(1,'3201010101990001','Akun Lama')")
    con.execute("INSERT INTO laporan(user_id,judul,kategori,deskripsi,lokasi) VALUES(1,'L','Lainnya','d','l')")
    con.commit(); con.close()
    create_app({"DATABASE": str(path), "UPLOAD_FOLDER": str(tmp_path / "up"), "TESTING": True})
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    assert "'humas'" in con.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
    assert con.execute("SELECT username FROM users WHERE id=1").fetchone()[0] == "lama"
    assert con.execute("SELECT COUNT(*) FROM warga WHERE user_id=1").fetchone()[0] == 1
    con.execute("INSERT INTO users(username,password_hash,nama,role) VALUES('hms','x','H','humas')")
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    con.close()
