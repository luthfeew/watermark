#!/usr/bin/env python3
"""
sort_photos.py
Mengelompokkan foto ke subfolder YYYY-MM-DD berdasarkan tanggal pengambilan.

═══════════════════════════════════════════════════════════════
  INSTALASI
═══════════════════════════════════════════════════════════════
  pip install Pillow

═══════════════════════════════════════════════════════════════
  PENGGUNAAN
═══════════════════════════════════════════════════════════════
  python sort_photos.py <folder_sumber> [opsi]

  Argumen:
    folder_sumber          Folder berisi foto yang akan diurutkan

  Opsi:
    -d, --dest <folder>    Folder tujuan output
                           (default: sama dengan folder_sumber)
    --copy                 Salin file, jangan hapus dari sumber
                           (default: pindah / move)
    --dry-run              Simulasi saja, tidak ada file yang diubah
    -h, --help             Tampilkan bantuan ini

═══════════════════════════════════════════════════════════════
  CONTOH
═══════════════════════════════════════════════════════════════
  # Pindahkan foto ke subfolder dalam folder yang sama
  python sort_photos.py /sdcard/DCIM/Camera

  # Simulasi dulu sebelum eksekusi
  python sort_photos.py /sdcard/DCIM/Camera --dry-run

  # Pindahkan ke folder tujuan berbeda
  python sort_photos.py /sdcard/DCIM/Camera -d D:/Foto/Sorted

  # Salin (bukan pindah), ke folder tujuan
  python sort_photos.py /sdcard/DCIM/Camera -d D:/Foto/Sorted --copy

═══════════════════════════════════════════════════════════════
  POLA NAMA FILE YANG DIKENALI
═══════════════════════════════════════════════════════════════
  IMG_20260602_121611.jpg          → kamera standar Android
  IMG-20260611-WA0017.jpg          → WhatsApp
  VID-20260615-WA0001.mp4          → WhatsApp video
  Screenshot_2026-06-09-15-31.jpg  → screenshot Android
  (fallback) file dengan 8 digit tanggal berurutan: 20260602

  Prioritas: metadata EXIF > nama file > gagal (dilaporkan)
═══════════════════════════════════════════════════════════════
"""

import os
import re
import shutil
import argparse
from pathlib import Path
from datetime import datetime

# ── EXIF via Pillow (opsional) ──────────────────────────────────────────────
try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

# ── EXIF via piexif (opsional, fallback tambahan) ───────────────────────────
try:
    import piexif
    PIEXIF_AVAILABLE = True
except ImportError:
    PIEXIF_AVAILABLE = False

# ── Ekstensi yang diproses ───────────────────────────────────────────────────
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".tiff", ".tif", ".webp", ".heic", ".heif",
    ".raw", ".cr2", ".nef", ".orf", ".arw",
}

# ── Pola regex nama file ─────────────────────────────────────────────────────
# Urutan penting: pola lebih spesifik duluan
FILENAME_PATTERNS = [
    # Screenshot_2026-06-09-15-31-21-874_com.whatsapp.jpg
    re.compile(r"Screenshot[_-](\d{4})-(\d{2})-(\d{2})[_-]"),

    # IMG-20260611-WA0017.jpg  (WhatsApp)
    re.compile(r"IMG-(\d{4})(\d{2})(\d{2})-WA"),

    # IMG_20260602_121611.jpg  (kamera standar)
    re.compile(r"(?:IMG|VID|DSC|PXL|MVIMG|PANO)[_-](\d{4})(\d{2})(\d{2})[_-]"),

    # Video/file WhatsApp: VID-20260602-WA0001
    re.compile(r"VID-(\d{4})(\d{2})(\d{2})-WA"),

    # Fallback: 8 digit berturut  20260602  di mana saja dalam nama file
    re.compile(r"(\d{4})(\d{2})(\d{2})"),
]


def extract_date_exif(filepath: Path) -> datetime | None:
    """Baca tanggal dari metadata EXIF."""
    if not PILLOW_AVAILABLE:
        return None
    try:
        img = Image.open(filepath)
        exif_data = img._getexif()  # type: ignore[attr-defined]
        if not exif_data:
            return None
        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
                # Format EXIF: "2026:06:02 12:16:11"
                try:
                    return datetime.strptime(str(value).strip(), "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    continue
    except Exception:
        pass
    return None


def extract_date_filename(filename: str) -> datetime | None:
    """Parse tanggal dari nama file menggunakan pola regex."""
    stem = Path(filename).stem  # tanpa ekstensi
    for pattern in FILENAME_PATTERNS:
        m = pattern.search(stem)
        if m:
            try:
                year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
                # Validasi tanggal
                dt = datetime(year, month, day)
                # Sanity check: tahun masuk akal
                if 1990 <= dt.year <= 2100:
                    return dt
            except (ValueError, AttributeError):
                continue
    return None


def get_date(filepath: Path) -> tuple[datetime | None, str]:
    """
    Coba dapatkan tanggal, kembalikan (datetime, sumber).
    sumber: 'exif' | 'filename' | None
    """
    dt = extract_date_exif(filepath)
    if dt:
        return dt, "exif"

    dt = extract_date_filename(filepath.name)
    if dt:
        return dt, "filename"

    return None, "unknown"


def sort_photos(
    source_dir: str,
    dest_dir: str | None = None,
    move: bool = False,
    dry_run: bool = False,
) -> None:
    source = Path(source_dir).resolve()
    dest = Path(dest_dir).resolve() if dest_dir else source

    if not source.is_dir():
        print(f"[ERROR] Direktori tidak ditemukan: {source}")
        return

    # ── Kumpulkan semua file gambar ──────────────────────────────────────────
    all_files = [
        f for f in source.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    ]

    total = len(all_files)
    success = 0
    failed: list[tuple[str, str]] = []   # (nama file, alasan)
    skipped = 0

    exif_count = 0
    filename_count = 0

    print(f"\n{'='*60}")
    print(f"  sort_photos.py")
    print(f"{'='*60}")
    print(f"  Sumber  : {source}")
    print(f"  Tujuan  : {dest}")
    print(f"  Mode    : {'DRY RUN (tidak ada perubahan)' if dry_run else ('pindah' if move else 'salin')}")
    print(f"  Total   : {total} file gambar ditemukan")
    print(f"{'='*60}\n")

    if total == 0:
        print("Tidak ada file gambar yang ditemukan. Selesai.")
        return

    for filepath in sorted(all_files):
        dt, source_type = get_date(filepath)

        if dt is None:
            failed.append((filepath.name, "tidak ada metadata EXIF & nama file tidak dikenali"))
            continue

        # Nama folder ISO 8601 (tanggal saja)
        folder_name = dt.strftime("%Y-%m-%d")
        target_dir = dest / folder_name

        target_path = target_dir / filepath.name

        # Skip jika sudah ada dan isinya sama
        if target_path.exists() and target_path.stat().st_size == filepath.stat().st_size:
            skipped += 1
            print(f"  [SKIP]  {filepath.name}  →  {folder_name}/  (sudah ada)")
            continue

        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
            try:
                if move:
                    shutil.move(str(filepath), target_path)
                else:
                    shutil.copy2(str(filepath), target_path)
            except Exception as e:
                failed.append((filepath.name, str(e)))
                continue

        action = "PINDAH" if move else "SALIN"
        if dry_run:
            action = "DRY"
        src_label = "EXIF" if source_type == "exif" else "NAMA"
        print(f"  [{action}] [{src_label}]  {filepath.name}  →  {folder_name}/")

        success += 1
        if source_type == "exif":
            exif_count += 1
        else:
            filename_count += 1

    # ── Ringkasan ────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  RINGKASAN")
    print(f"{'='*60}")
    print(f"  Total file ditemukan : {total}")
    print(f"  Berhasil diproses    : {success}")
    print(f"    - dari metadata EXIF  : {exif_count}")
    print(f"    - dari nama file      : {filename_count}")
    print(f"  Dilewati (duplikat)  : {skipped}")
    print(f"  Gagal diproses       : {len(failed)}")

    if failed:
        print(f"\n  {'─'*50}")
        print(f"  FILE YANG GAGAL DIPROSES:")
        print(f"  {'─'*50}")
        for name, reason in failed:
            print(f"  ✗  {name}")
            print(f"     Alasan: {reason}")

    if not PILLOW_AVAILABLE:
        print(f"\n  [!] Pillow tidak terinstall — deteksi EXIF dinonaktifkan.")
        print(f"      Install: pip install Pillow")

    print(f"\n{'='*60}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Kelompokkan foto ke subfolder YYYY-MM-DD berdasarkan tanggal.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("source", help="Folder sumber yang berisi foto")
    parser.add_argument(
        "-d", "--dest",
        help="Folder tujuan (default: sama dengan sumber)",
        default=None,
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Salin file, jangan hapus dari sumber (default: pindah)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulasi saja, tidak ada file yang dipindah/disalin",
    )

    args = parser.parse_args()
    sort_photos(
        source_dir=args.source,
        dest_dir=args.dest,
        move=not args.copy,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
