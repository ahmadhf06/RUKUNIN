"""Laporan lingkungan: Warga melapor (foto + lokasi), Humas menanggapi & menindaklanjuti, Ketua RT memantau."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..core import (STATUS, can_see_laporan, choice, current_user, db, flt, log, notify, q,
                    roles_required, save_image, text, users_by_role)

bp = Blueprint("laporan", __name__, url_prefix="/laporan")
KATEGORI = ["Kebersihan", "Keamanan", "Infrastruktur", "Fasilitas Umum", "Sosial", "Lainnya"]


@bp.route("/")
@roles_required("warga", "humas", "ketua")
def index():
    u, status = current_user(), request.args.get("status", "")
    where, args = [], []
    if u["role"] == "warga":                         # warga: hanya laporan miliknya; Humas & Ketua: semua laporan
        where.append("l.user_id=?")
        args.append(u["id"])
    if status in STATUS["laporan"]:
        where.append("l.status=?")
        args.append(status)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    rows = q("SELECT l.*, p.nama AS pelapor, j.nama AS pj FROM laporan l JOIN users p ON p.id=l.user_id "
             f"LEFT JOIN users j ON j.id=l.pj_id {w} ORDER BY l.id DESC LIMIT 300", args)
    return render_template("laporan_list.html", rows=rows, status=status)


@bp.route("/baru", methods=["GET", "POST"])
@roles_required("warga")
def baru():
    if request.method == "POST":
        judul = text("judul", "Judul", 120)
        kategori = choice("kategori", "kategori", KATEGORI)
        desk = text("deskripsi", "Deskripsi", 2000)
        lokasi = text("lokasi", "Lokasi", 150)
        lat, lng = flt("lat", "Latitude", -90, 90), flt("lng", "Longitude", -180, 180)
        foto = save_image("foto")
        lid = db().execute("INSERT INTO laporan(user_id,judul,kategori,deskripsi,lokasi,lat,lng,foto) VALUES(?,?,?,?,?,?,?,?)",
                           (current_user()["id"], judul, kategori, desk, lokasi, lat, lng, foto)).lastrowid
        db().commit()
        log("laporan_baru", f"#{lid} {judul}")
        notify(users_by_role("humas", "ketua"), "Laporan lingkungan baru", f"{kategori}: {judul}",
               url_for("laporan.detail", lid=lid))
        flash("Laporan terkirim ke Humas dan Ketua RT.", "ok")
        return redirect(url_for("laporan.detail", lid=lid))
    return render_template("laporan_form.html", kategori=KATEGORI)


def _get(lid):
    lap = q("SELECT l.*, p.nama AS pelapor, j.nama AS pj FROM laporan l JOIN users p ON p.id=l.user_id "
            "LEFT JOIN users j ON j.id=l.pj_id WHERE l.id=?", (lid,), one=True)
    if not lap:
        abort(404)
    if not can_see_laporan(current_user(), lap):
        abort(403)
    return lap


@bp.route("/<int:lid>")
@roles_required("warga", "humas", "ketua")
def detail(lid):
    lap, u = _get(lid), current_user()
    can_edit = u["role"] == "humas"                  # hanya Humas yang menindaklanjuti; Ketua RT memantau
    opsi_status = [(k, v[0]) for k, v in STATUS["laporan"].items()]
    return render_template("laporan_detail.html", lap=lap, can_edit=can_edit, opsi_status=opsi_status)


@bp.post("/<int:lid>/update")
@roles_required("humas")
def update(lid):
    lap, u = _get(lid), current_user()
    status = choice("status", "status", STATUS["laporan"])
    catatan = text("catatan", "Catatan", 500, required=False)
    # Humas yang menindaklanjuti tercatat sebagai penanggung jawab laporan
    db().execute("UPDATE laporan SET status=?, catatan=?, pj_id=?, updated_at=datetime('now','localtime') WHERE id=?",
                 (status, catatan, u["id"], lid))
    db().commit()
    log("laporan_update", f"#{lid} -> {status}")
    link = url_for("laporan.detail", lid=lid)
    notify([lap["user_id"]], "Status laporan Anda diperbarui", f"{lap['judul']}: {STATUS['laporan'][status][0]}", link)
    flash("Laporan diperbarui.", "ok")
    return redirect(link)
