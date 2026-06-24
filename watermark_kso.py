#!/usr/bin/env python3
"""
watermark_kso.py
================
Menambahkan watermark ke foto lapangan proyek KSO LPPSLH – Saranabudi.

WATERMARK YANG DITAMBAHKAN
  - Pojok kanan atas  : logo KSO (logo.jpg/png) flush ke sudut, tanpa margin
  - Pojok kanan bawah : timestamp dalam format Bahasa Indonesia + hashtag (opsional)
    Contoh: "Senin, 22 Juni 2026 15.35.36 WIB"

PRIORITAS SUMBER TIMESTAMP
  1. --datetime  (input manual, prioritas tertinggi)
  2. EXIF DateTimeOriginal  (waktu jepretan kamera)
  3. EXIF DateTimeDigitized
  4. EXIF DateTime
  5. Waktu sekarang saat script dijalankan (fallback, muncul peringatan)

MODE OPERASI
  Normal  : tambah logo + timestamp ke foto bersih
  Replace : hapus timestamp lama (crop kiri+bawah, rasio tetap) → tempel
            timestamp baru tanpa logo (logo di foto asli tetap terlihat)

CARA PAKAI
  # Watermark seluruh folder tempat script berada (tanpa argumen)
  python watermark_kso.py

  # Watermark normal — satu atau beberapa file
  python watermark_kso.py foto.jpg
  python watermark_kso.py *.jpg --output-dir ./hasil/

  # Watermark seluruh folder (1 level)
  python watermark_kso.py ./foto_lapangan/

  # Watermark seluruh folder termasuk subfolder
  python watermark_kso.py ./foto_lapangan/ --recursive

  # Override tanggal manual
  python watermark_kso.py foto.jpg --datetime "2026-06-18 14:21:58"

  # Tambah hashtag/nama proyek di bawah timestamp
  python watermark_kso.py foto.jpg --tag "#Pembangunan Dome UNSIKA"

  # Tentukan logo custom
  python watermark_kso.py foto.jpg --logo logo_kso.png

  # Replace timestamp yang sudah ada
  python watermark_kso.py foto_wm.jpg --replace
  python watermark_kso.py foto_wm.jpg --replace --datetime "2026-06-23 09:00:00"
  python watermark_kso.py ./foto_lapangan/ --replace --output-dir ./hasil/

  # Lihat info EXIF saja (tanpa proses)
  python watermark_kso.py foto.jpg --info

REQUIREMENTS
  pip install Pillow
  pip install numpy   # opsional, mempercepat deteksi warna di mode --replace
"""

import argparse
import datetime
import os
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
    from PIL.ExifTags import TAGS
except ImportError:
    print("❌ Pillow belum terinstall. Jalankan: pip install Pillow")
    sys.exit(1)

try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False

# ──────────────────────────────────────────────
#  FONT
# ──────────────────────────────────────────────
# Ketebalan teks dikontrol dari pemilihan file .ttf-nya.
# Ganti daftar di bawah jika ingin font lain; urutan = prioritas.

# Font Bold — dipakai untuk timestamp & tag (lebih terbaca di foto)
_FONT_BOLD = [
    "C:\\Users\\luthf\\AppData\\Local\\Microsoft\\Windows\\Fonts\\Roboto-Regular.ttf",
]

# Font Regular — dipakai jika bold tidak tersedia
_FONT_REGULAR = [
    "/usr/share/fonts/truetype/liberation/LiberationSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibril.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _find_font(candidates: list) -> str | None:
    """Kembalikan path font pertama yang ditemukan di sistem, atau None."""
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """
    Muat font dengan ukuran tertentu.

    Args:
        size : ukuran font dalam piksel
        bold : True = cari varian Bold terlebih dahulu (default),
               False = langsung pakai Regular

    Fallback: jika bold tidak ditemukan → coba Regular → PIL default bitmap.
    """
    path = _find_font(_FONT_BOLD if bold else _FONT_REGULAR)
    if path is None and bold:
        # Bold tidak ada di sistem, turun ke Regular
        path = _find_font(_FONT_REGULAR)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()

# ──────────────────────────────────────────────
#  KONSTANTA
# ──────────────────────────────────────────────
DAYS_ID   = ["Senin","Selasa","Rabu","Kamis","Jumat","Sabtu","Minggu"]
MONTHS_ID = ["","Januari","Februari","Maret","April","Mei","Juni",
             "Juli","Agustus","September","Oktober","November","Desember"]

YELLOW = (255, 255, 0, 255)

EXIF_DATETIME_TAGS = [
    (36867, "DateTimeOriginal"),
    (36868, "DateTimeDigitized"),
    (306,   "DateTime"),
]
EXIF_DATETIME_FMT = "%Y:%m:%d %H:%M:%S"


# ──────────────────────────────────────────────
#  EXIF
# ──────────────────────────────────────────────
def read_exif_datetime(image_path: str):
    """Return (datetime | None, sumber_string)."""
    try:
        img  = Image.open(image_path)
        exif = img._getexif()
        if not exif:
            return None, "tidak ada EXIF"
        for tag_id, tag_name in EXIF_DATETIME_TAGS:
            val = exif.get(tag_id)
            if val:
                try:
                    dt = datetime.datetime.strptime(val.strip(), EXIF_DATETIME_FMT)
                    return dt, f"EXIF {tag_name}"
                except ValueError:
                    continue
        return None, "EXIF ada tapi tidak ada tag tanggal"
    except Exception as e:
        return None, f"gagal baca EXIF ({e})"


def print_exif_info(image_path: str):
    """Cetak semua tag EXIF yang relevan (tanggal, kamera) ke stdout."""
    try:
        img  = Image.open(image_path)
        exif = img._getexif()
        print(f"\n📷 {image_path}  ({img.size[0]}×{img.size[1]}px)")
        if not exif:
            print("   ⚠️  Tidak ada data EXIF")
            return
        for tag_id, val in sorted(exif.items()):
            name = TAGS.get(tag_id, f"Tag#{tag_id}")
            if any(k in name.lower() for k in ("date","time","make","model","software")):
                print(f"   {name:30s}: {val}")
    except Exception as e:
        print(f"   ❌ Error: {e}")


# ──────────────────────────────────────────────
#  UTILITAS GAMBAR
# ──────────────────────────────────────────────
def draw_text_shadow(draw, pos, text, font, fill, shadow_offset=3):
    """
    Gambar teks dengan drop-shadow gelap di belakangnya.
    Shadow memberikan kontras supaya teks tetap terbaca di atas foto terang.

    Args:
        draw         : ImageDraw.Draw target
        pos          : (x, y) posisi teks utama
        text         : string yang digambar
        font         : ImageFont
        fill         : warna teks utama (RGBA)
        shadow_offset: geser shadow ke kanan-bawah (px), default 3
    """
    x, y = pos
    draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=(0, 0, 0, 180))
    draw.text((x, y), text, font=font, fill=fill)


def format_datetime_id(dt: datetime.datetime) -> str:
    """
    Format datetime ke string Bahasa Indonesia untuk timestamp watermark.
    Contoh output: "Senin, 22 Juni 2026 15.35.36 WIB"
    """
    day = DAYS_ID[dt.weekday()]
    mon = MONTHS_ID[dt.month]
    return f"{day}, {dt.day} {mon} {dt.year} {dt.strftime('%H.%M.%S')} WIB"


# ──────────────────────────────────────────────
#  CORE
# ──────────────────────────────────────────────
def detect_timestamp_crop(img: "Image.Image", padding_factor: float = 1.4) -> int:
    """
    Deteksi baris timestamp kuning di bagian bawah, kembalikan crop_b (px dari bawah).
    Menggunakan numpy jika tersedia, fallback ke Pillow pixel-by-pixel.
    padding_factor: pengali ekstra supaya teks terpotong bersih.
    """
    W, H = img.size
    scan_h = int(H * 0.15)          # scan 15% bawah

    if _NUMPY_OK:
        arr = np.array(img.convert("RGB"))
        bottom = arr[H - scan_h:, :]
        yellow = (bottom[:, :, 0] > 180) & (bottom[:, :, 1] > 180) & (bottom[:, :, 2] < 100)
        ys = np.where(yellow)[0]
        if len(ys) == 0:
            return 0
        topmost = int(ys.min())                      # relatif terhadap bottom region
    else:
        # Fallback tanpa numpy
        topmost = scan_h
        for y in range(scan_h):
            row_y = H - scan_h + y
            for x in range(W):
                r, g, b = img.getpixel((x, row_y))[:3]
                if r > 180 and g > 180 and b < 100:
                    topmost = y
                    break
            else:
                continue
            break

    # crop_b = dari topmost kuning sampai tepi bawah, dikali padding
    crop_b = int((scan_h - topmost) * padding_factor)
    return max(crop_b, 0)


def crop_maintain_ratio(img: "Image.Image", crop_b: int) -> "Image.Image":
    """
    Potong crop_b px dari bawah, dan potong kiri secukupnya supaya rasio W:H tetap.
    Box: (crop_l, 0, W, H - crop_b)
    """
    W, H = img.size
    if crop_b <= 0 or crop_b >= H:
        return img
    # crop_l / W = crop_b / H  →  crop_l = W * crop_b / H
    # Pakai round() supaya rasio lebih presisi dibanding int()
    crop_l = round(W * crop_b / H)
    return img.crop((crop_l, 0, W, H - crop_b))   # (left, top, right, bottom)


def add_watermark(
    image_path:  str,
    output_path: str,
    dt:          datetime.datetime,
    logo_path:   str   = None,
    tag:         str   = None,       # None = tidak ada hashtag
    scale:       float = 1.0,
    skip_logo:   bool  = False,      # True saat mode --replace (logo sudah ada di foto)
    src_img:     "Image.Image" = None,  # opsional: gambar sudah pre-crop dari mode replace
) -> str:
    """
    Tempel watermark ke satu gambar lalu simpan ke output_path.

    Alur kerja:
      1. Buka gambar (atau gunakan src_img jika sudah di-crop)
      2. Hitung ukuran font & margin proporsional terhadap lebar gambar
      3. Buat layer RGBA transparan (overlay) untuk menggambar watermark
      4. [A] Tempel logo di pojok kanan atas (dilewati jika skip_logo=True)
      5. [B] Tulis timestamp (+ tag opsional) di pojok kanan bawah
      6. Alpha-composite overlay ke gambar asli → simpan sebagai JPEG

    Args:
        image_path  : path file input (dibaca jika src_img tidak diberikan)
        output_path : path file output
        dt          : objek datetime untuk timestamp
        logo_path   : path file logo PNG/JPG; None = tanpa logo
        tag         : baris kedua di bawah timestamp, misal "#Proyek Dome UNSIKA"
        scale       : pengali ukuran watermark (1.0 = normal)
        skip_logo   : lewati penempelan logo (dipakai mode --replace)
        src_img     : gambar PIL yang sudah siap dipakai (bypass buka file)

    Returns:
        output_path yang sama dengan argumen.
    """
    img = (src_img if src_img is not None else Image.open(image_path)).convert("RGB")
    W, H = img.size

    # base: faktor skala watermark terhadap resolusi gambar.
    # Rumus: foto 4000px lebar → base=1.0; 1600px → base≈0.4; dikali --scale.
    # Diklem antara 0.35 (minimum terbaca) dan 2.5 (maksimum wajar).
    base   = max(0.35, min(W / 4000.0 * scale, 2.5))
    margin = int(35 * base)   # jarak teks dari tepi gambar (px)
    shadow = int(3  * base)   # geser drop-shadow (px)

    # Ukuran font dalam piksel; ubah angka 120 untuk memperbesar/memperkecil teks
    fs_ts  = int(125 * base)   # timestamp
    fs_tag = int(125 * base)   # hashtag/tag proyek

    font_ts  = load_font(fs_ts,  bold=True)
    font_tag = load_font(fs_tag, bold=True)

    # Tampilkan font yang dipakai (ambil path dari font object)
    font_name = getattr(font_ts, "path", "PIL default bitmap")
    print(f"   🔤 Font: {font_name} (size={fs_ts}px)")

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)

    # ── [A] POJOK KANAN ATAS: logo flush ke sudut ────────────────────────
    # Dilewati saat mode --replace karena logo sudah ada di foto asli
    if not skip_logo:
        if logo_path and os.path.exists(logo_path):
            raw     = Image.open(logo_path).convert("RGBA")
            # Tinggi logo = 20% tinggi foto, lebar proporsional
            logo_h  = int(H * 0.19)
            logo_w  = int(raw.width * logo_h / raw.height)
            logo_img = raw.resize((logo_w, logo_h), Image.LANCZOS)

            # Opacity logo: 0 = transparan, 255 = penuh. Ganti 180 sesuai selera
            r, g, b, a = logo_img.split()
            logo_img = Image.merge("RGBA", (r, g, b, a.point(lambda x: int(x * 220 / 255))))

            # Tempel persis di pojok kanan atas (0 margin)
            overlay.paste(logo_img, (W - logo_w, 0), logo_img)
        else:
            if logo_path:
                print(f"   ⚠️  Logo tidak ditemukan: {logo_path}")

    # ── [B] POJOK KANAN BAWAH: timestamp (+ tag opsional) ───────────────
    date_str = format_datetime_id(dt)
    bb_d = draw.textbbox((0, 0), date_str, font=font_ts)
    dw   = bb_d[2] - bb_d[0]
    dh   = bb_d[3] - bb_d[1]

    if tag:
        bb_t     = draw.textbbox((0, 0), tag, font=font_tag)
        tw       = bb_t[2] - bb_t[0]
        th       = bb_t[3] - bb_t[1]
        # Jarak antar baris (timestamp ↔ tag)
        line_gap = int(30 * base)
        total_h  = dh + line_gap + th

        # Tag (baris bawah)
        hx = W - margin - tw
        hy = H - margin - th
        draw_text_shadow(draw, (hx, hy), tag, font_tag, YELLOW, shadow)

        # Tanggal (baris atas dari tag)
        dx = W - margin - dw
        dy = hy - line_gap - dh
        draw_text_shadow(draw, (dx, dy), date_str, font_ts, YELLOW, shadow)
    else:
        # Hanya tanggal, langsung di bawah
        dx = W - margin - dw
        dy = H - margin - dh
        draw_text_shadow(draw, (dx, dy), date_str, font_ts, YELLOW, shadow)

    # ── Komposit & simpan ────────────────────────────────────────────────
    result = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    result.save(output_path, quality=95)
    return output_path


# ──────────────────────────────────────────────
#  INPUT COLLECTION
# ──────────────────────────────────────────────
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}

def collect_images(inputs: list[str], recursive: bool = False, exclude: set = None) -> list[str]:
    """
    Expand argumen input menjadi daftar path file gambar.

    Tiap elemen di `inputs` bisa berupa:
      - Path file langsung  → langsung dimasukkan ke hasil
      - Path folder         → di-scan isinya untuk file gambar

    Args:
        inputs    : list path dari argumen CLI (file dan/atau folder)
        recursive : True = scan subfolder secara rekursif (--recursive)
        exclude   : set path (resolved) yang dilewati, misal file logo

    Returns:
        List path file gambar yang sudah di-deduplikasi, urut alfabet.
    """
    excluded = {Path(p).resolve() for p in (exclude or [])}
    collected = []
    for inp in inputs:
        p = Path(inp)
        if p.is_file():
            if p.resolve() in excluded:
                print(f"   ⏭️  Dilewati (logo): {p.name}")
            elif p.suffix in _IMG_EXTS:
                collected.append(str(p))
            else:
                print(f"⚠️  Dilewati (bukan gambar): {inp}")
        elif p.is_dir():
            pattern = "**/*" if recursive else "*"
            found = sorted(
                f for f in p.glob(pattern)
                if f.is_file() and f.suffix in _IMG_EXTS and f.resolve() not in excluded
            )
            if not found:
                print(f"⚠️  Tidak ada gambar di folder: {inp}")
            else:
                print(f"📁 Folder '{inp}': ditemukan {len(found)} gambar"
                      + (" (rekursif)" if recursive else ""))
            collected.extend(str(f) for f in found)
        else:
            print(f"⚠️  Tidak ditemukan: {inp}")

    # Deduplikasi, pertahankan urutan
    seen = set()
    result = []
    for f in collected:
        if f not in seen:
            seen.add(f)
            result.append(f)
    return result


# ──────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description="Watermark foto lapangan — logo pojok kanan atas, timestamp pojok kanan bawah",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "images", nargs="*",
        help=(
            "File gambar (jpg/png) dan/atau folder. Bisa mix keduanya. "
            "Kosongkan untuk memproses semua gambar di folder yang sama dengan script."
        )
    )
    p.add_argument(
        "--recursive", "-r", action="store_true",
        help="Scan subfolder secara rekursif saat input adalah folder."
    )
    p.add_argument(
        "--datetime", dest="dt", default=None, metavar="YYYY-MM-DD HH:MM:SS",
        help="Override timestamp manual. Default: baca dari EXIF foto."
    )
    p.add_argument(
        "--logo", default=None, metavar="logo.jpg",
        help="Path logo PNG (default: logo.jpg di folder yang sama dengan script)"
    )
    p.add_argument(
        "--tag", default=None, metavar="#HASHTAG",
        help="Hashtag/nama proyek di bawah timestamp (opsional, hilangkan untuk tanpa tag)"
    )
    p.add_argument("--output-dir", dest="output_dir", default=None,
                   help="Folder output (default: folder yang sama, suffix _wm)")
    p.add_argument("--suffix", default="_wm",
                   help="Suffix nama file output (default: _wm)")
    p.add_argument("--scale", type=float, default=1.0,
                   help="Skala ukuran watermark (default: 1.0)")
    p.add_argument("--info", action="store_true",
                   help="Tampilkan info EXIF saja, tanpa proses watermark")
    p.add_argument(
        "--replace", action="store_true",
        help=(
            "Replace timestamp lama: crop kiri+bawah untuk hapus timestamp lama "
            "(rasio tetap), lalu tempel timestamp baru tanpa logo."
        ),
    )
    return p.parse_args()


def resolve_logo(cli_logo: str) -> str | None:
    """
    Tentukan path logo yang dipakai.
    Prioritas: argumen --logo → logo.jpg di folder script → None (tanpa logo).
    """
    if cli_logo:
        return cli_logo
    default = Path(__file__).parent / "logo.jpg"
    if default.exists():
        return str(default)
    return None


def main():
    args = parse_args()

    if args.info:
        for img_path in args.images:
            print_exif_info(img_path)
        return

    # Kalau tidak ada argumen → pakai folder tempat script berada
    inputs = args.images if args.images else [str(Path(__file__).parent)]
    if not args.images:
        print(f"📂 Tidak ada argumen, memproses folder script: {Path(__file__).parent}")

    # Timestamp manual
    manual_dt = None
    if args.dt:
        try:
            manual_dt = datetime.datetime.strptime(args.dt, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            print(f"❌ Format salah: '{args.dt}' — gunakan: YYYY-MM-DD HH:MM:SS")
            sys.exit(1)

    logo_path = resolve_logo(args.logo)

    # File logo diexclude supaya tidak ikut diproses saat scan folder
    logo_exclude = {logo_path} if logo_path else set()

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    # Expand file & folder → flat list gambar (logo otomatis diexclude)
    all_images = collect_images(inputs, recursive=args.recursive, exclude=logo_exclude)

    success = 0
    for img_path in all_images:
        if not os.path.isfile(img_path):
            print(f"⚠️  Tidak ditemukan: {img_path}")
            continue

        # Tentukan timestamp
        if manual_dt:
            dt, dt_src = manual_dt, "manual"
        else:
            dt, dt_src = read_exif_datetime(img_path)
            if dt is None:
                dt     = datetime.datetime.now()
                dt_src = "⚠️  fallback waktu sekarang (tidak ada EXIF)"

        p        = Path(img_path)
        out_name = p.stem + args.suffix + ".jpg"
        out_path = (
            os.path.join(args.output_dir, out_name)
            if args.output_dir
            else str(p.parent / out_name)
        )

        try:
            if args.replace:
                # ── Mode replace ────────────────────────────────────────
                raw_img = Image.open(img_path)
                W0, H0  = raw_img.size

                crop_b = detect_timestamp_crop(raw_img)
                if crop_b == 0:
                    print(f"   ⚠️  Tidak ada timestamp kuning terdeteksi di {p.name}, "
                          "diproses tanpa crop.")
                else:
                    print(f"   ✂️  Crop: -{crop_b}px bawah, "
                          f"-{int(W0 * crop_b / H0)}px kiri  "
                          f"(rasio {W0}:{H0} tetap)")

                cropped = crop_maintain_ratio(raw_img, crop_b)

                add_watermark(
                    image_path  = img_path,
                    output_path = out_path,
                    dt          = dt,
                    logo_path   = None,       # skip logo
                    tag         = args.tag,
                    scale       = args.scale,
                    skip_logo   = True,
                    src_img     = cropped,
                )
            else:
                # ── Mode normal ─────────────────────────────────────────
                add_watermark(
                    image_path  = img_path,
                    output_path = out_path,
                    dt          = dt,
                    logo_path   = logo_path,
                    tag         = args.tag,
                    scale       = args.scale,
                )

            print(f"✅ {p.name}  →  {out_path}  [{dt_src}]")
            success += 1
        except Exception as e:
            print(f"❌ Gagal: {img_path}: {e}")

    print(f"\nSelesai: {success}/{len(all_images)} file berhasil diproses.")


if __name__ == "__main__":
    main()
