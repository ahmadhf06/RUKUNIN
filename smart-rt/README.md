# Smart RT — Sistem Informasi Manajemen Lingkungan Digital

Flask + SQLite, satu database terpusat. Semua data (surat, laporan, tagihan, voting, presensi, notifikasi) tersimpan di database;
tidak ada data statis pengganti. Tampilan hijau zamrud–putih, sidebar kiri, responsif (HP/tablet/laptop).

## Instalasi & menjalankan
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                      # buka http://localhost:5000
```
Database `instance/smartrt.db` dibuat otomatis beserta data awal. Reset: hapus folder `instance/` lalu jalankan ulang.

**Akses dari HP/tablet:** jalankan di satu komputer, cari IP-nya (mis. `192.168.1.10`), lalu buka `http://192.168.1.10:5000`
dari perangkat lain di WiFi yang sama. Agar QR presensi memakai alamat itu secara tetap: `PUBLIC_BASE_URL=http://192.168.1.10:5000`.

## Akun uji
Hanya ada pada database baru (kosong). Akun contoh ini tidak lagi ditampilkan di halaman login.

| Peran | Username | Kata sandi |
|---|---|---|
| Admin | `admin` | `Admin#2026` |
| Ketua RT | `ketua` | `Ketua#2026` |
| Sekretaris | `sekretaris` | `Sekre#2026` |
| Bendahara | `bendahara` | `Bendahara#2026` |
| Humas | `humas` | `Humas#2026` |
| Warga | `warga` / `warga2` | `Warga#2026` |

Warga baru mendaftar sendiri lewat halaman **Daftar** (`/daftar`); datanya langsung masuk ke Data Warga Ketua RT dan Ketua mendapat notifikasi.
Pada database lama, akun **Humas** dibuat oleh Admin di menu Kelola Akun. Tabel pengguna dimigrasi otomatis saat server pertama dijalankan (backup `instance/smartrt.db` dulu).

## Hak akses
| Fitur | Admin | Ketua | Sekretaris | Bendahara | Humas | Warga |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Kelola akun, peran, pengaturan, log | ✔ | | | | | |
| Data warga | | kelola | lihat | lihat | | profil sendiri |
| Surat | | setujui/tolak | periksa, draf, nomor, arsip | | | ajukan, lihat sendiri |
| Laporan lingkungan | | pantau | | | tanggapi & tindak lanjuti | buat, lihat sendiri |
| Aspirasi | | pantau | | | kelola & tanggapi | kirim, lihat sendiri |
| Kas, tagihan, verifikasi | | lihat | | kelola | | tagihan sendiri + unggah bukti |
| Pengumuman | | buat/hapus | lihat | lihat | lihat | lihat |
| Ronda | | kelola | kelola | lihat | lihat | lihat |
| Voting | | buat/tutup, lihat hasil | lihat | lihat | lihat | memilih (1 akun = 1 suara) |
| Presensi QR | buat kegiatan | buat kegiatan | buat kegiatan | hadir | hadir | hadir (pindai QR) |
| Alarm darurat (bencana, kebakaran, maling) | unggah sirene | aktifkan, hentikan, unggah sirene | dengar | dengar | dengar | dengar |

Alur surat: **Warga → Sekretaris (periksa, draf, nomor) → Ketua RT (setujui) → Sekretaris (arsip) → Selesai**.
Sekretaris tidak memiliki aksi "setujui" sama sekali (ditegakkan di server, bukan hanya disembunyikan di UI).

## Struktur
```
run.py                  titik masuk
smartrt/__init__.py     app factory, header keamanan, menu per peran
smartrt/core.py         database, RBAC, CSRF, validasi, unggah aman, notifikasi
smartrt/schema.sql      skema database (17 tabel)
smartrt/seed.py         akun & data awal
smartrt/views/          main (login, dashboard, API), admin, warga, surat, laporan, keuangan, komunitas
smartrt/templates/      Jinja2     smartrt/static/   CSS & JS
tests/test_smoke.py     uji end-to-end seluruh alur & RBAC  ->  python -m pytest -q
```

## API JSON (untuk sinkronisasi antarperangkat)
`GET /api/notifikasi`, `GET /api/stats` (angka dashboard sesuai peran). Dipoll browser tiap 20 detik; 401 bila belum login.

## Keamanan
RBAC di setiap route · hash kata sandi (scrypt, Werkzeug) · token CSRF pada semua POST · cookie sesi HttpOnly + SameSite=Lax,
sesi 4 jam, sesi diganti saat login · pembatasan 5x gagal login/5 menit · query berparameter (anti SQL injection) · Jinja autoescape (anti XSS) ·
unggahan diperiksa isi berkasnya, dinamai acak, disajikan lewat route yang memeriksa hak akses · CSP, X-Frame-Options, nosniff · ekspor CSV anti formula-injection.

## Produksi
Set `SECRET_KEY`, `COOKIE_SECURE=1` (HTTPS), `SHOW_DEMO=0`, ganti semua kata sandi uji, dan jalankan dengan
`gunicorn "smartrt:create_app()"` di belakang HTTPS (nginx/Caddy). Untuk MySQL, ganti lapisan `core.db()` ke konektor MySQL
(SQL yang dipakai standar; `datetime('now')`/`INSERT OR IGNORE` perlu disesuaikan).
