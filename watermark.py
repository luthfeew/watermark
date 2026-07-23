#!/usr/bin/env python3
import argparse
import datetime
import hashlib
import io
import os
import random
import re
import sys
import urllib.request
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
    from PIL.ExifTags import TAGS
except ImportError:
    print("Pillow belum terinstall. Jalankan: pip install Pillow")
    sys.exit(1)

try:
    import numpy as np
except ImportError:
    np = None


# ==================================================
# CONFIG
# ==================================================

DEFAULT_LOGO_FILENAME  = "logo.jpg"
DEFAULT_LOGO_URL       = "https://github.com/luthfeew/watermark/blob/main/logo.jpg"
LOGO_DOWNLOAD_TIMEOUT  = 15
DEFAULT_OUTPUT_SUFFIX  = "_wm"
DEFAULT_OUTPUT_QUALITY = 95
DEFAULT_SCALE          = 1.0

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# Skala umum
BASE_IMAGE_WIDTH = 4000
MIN_SCALE        = 0.35
MAX_SCALE        = 2.50

# Logo kanan atas
LOGO_HEIGHT_RATIO = 0.19    # 19% dari tinggi foto
LOGO_OPACITY      = 220     # 0 transparan, 255 penuh
LOGO_MARGIN_X     = 0
LOGO_MARGIN_Y     = 0

# Teks kanan bawah
TEXT_COLOR         = (255, 255, 0, 255)
SHADOW_COLOR       = (120, 100, 0, 220)
TEXT_MARGIN        = 50
TEXT_SHADOW_OFFSET = 5
TIMESTAMP_FONT_SIZE = 125
TAG_FONT_SIZE       = 125
LINE_GAP            = 30

# Caption (aktif hanya jika --caption dipakai)
CAPTION_LINE_1   = "Kecamatan Karawang Timur, Karawang 41371"
CAPTION_LINE_2   = "Indonesia"
CAPTION_GAP      = 45    # jarak timestamp ↔ caption line 1
CAPTION_LINE_GAP = 8     # jarak caption line 1 ↔ caption line 2
CAPTION_BOTTOM   = 75    # jarak caption line 2 ke tepi bawah foto

# Mode replace timestamp lama
CROP_SCAN_BOTTOM_RATIO = 0.15
CROP_PADDING_FACTOR    = 1.4
YELLOW_MIN_RED         = 180
YELLOW_MIN_GREEN       = 180
YELLOW_MAX_BLUE        = 100

# Format tanggal
DAYS_ID   = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
MONTHS_ID = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
             "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
EXIF_DATETIME_FORMAT   = "%Y:%m:%d %H:%M:%S"
MANUAL_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
MANUAL_DATE_FORMAT     = "%Y-%m-%d"

# Jam random saat nama file hanya punya tanggal (menghindari jam makan siang)
RANDOM_TIME_START       = datetime.time(9, 0, 0)
RANDOM_TIME_BLOCK_START = datetime.time(12, 0, 0)
RANDOM_TIME_BLOCK_END   = datetime.time(13, 0, 0)
RANDOM_TIME_END         = datetime.time(15, 0, 0)

FILENAME_DATETIME_RE = re.compile(
    r"(?P<date>(?:19|20)\d{6})(?:[ _.-]?(?P<time>[0-2]\d[0-5]\d[0-5]\d))?"
)
EXIF_DATETIME_TAGS = [
    (36867, "DateTimeOriginal"),
    (36868, "DateTimeDigitized"),
    (306,   "DateTime"),
]

# Font — Path.home() otomatis menyesuaikan user Windows
FONT_PATHS = [
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts/MiSansLatin-Regular.ttf",
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts/Roboto-Regular.ttf",
    Path("C:/Windows/Fonts/arial.ttf"),
    # Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    # Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]


# ==================================================
# FONT & TEKS
# ==================================================

_font_path_cache = None

def find_font():
    global _font_path_cache
    if _font_path_cache is None:
        _font_path_cache = next((str(p) for p in FONT_PATHS if p.exists()), "")
    return _font_path_cache or None


def load_font(size):
    path = find_font()
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()


def auto_scale(width, manual_scale=1.0):
    return max(MIN_SCALE, min((width / BASE_IMAGE_WIDTH) * manual_scale, MAX_SCALE))


def scaled(value, scale):
    return max(1, int(value * scale))


def format_datetime_id(dt):
    return (
        f"{DAYS_ID[dt.weekday()]}, {dt.day:02d} {MONTHS_ID[dt.month]}"
        f" {dt.year} {dt.strftime('%H.%M.%S')} WIB"
    )


def draw_text_shadow(draw, position, text, font, scale):
    x, y   = position
    offset = scaled(TEXT_SHADOW_OFFSET, scale)
    draw.text((x + offset, y + offset), text, font=font, fill=SHADOW_COLOR)
    draw.text((x, y),                   text, font=font, fill=TEXT_COLOR)


# ==================================================
# TANGGAL FOTO
# ==================================================

def read_exif_datetime(image_path):
    try:
        with Image.open(image_path) as img:
            exif = img._getexif()
    except Exception as e:
        return None, f"gagal baca EXIF ({e})"

    if not exif:
        return None, "tidak ada EXIF"

    for tag_id, tag_name in EXIF_DATETIME_TAGS:
        value = exif.get(tag_id)
        if not value:
            continue
        try:
            return datetime.datetime.strptime(value.strip(), EXIF_DATETIME_FORMAT), f"EXIF {tag_name}"
        except ValueError:
            pass

    return None, "EXIF ada tapi tidak ada tanggal"


def parse_filename_datetime(image_path):
    """Kembalikan (date, time|None) dari nama file, atau (None, None)."""
    for match in FILENAME_DATETIME_RE.finditer(Path(image_path).stem):
        try:
            date = datetime.datetime.strptime(match.group("date"), "%Y%m%d").date()
        except ValueError:
            continue

        time = None
        if match.group("time"):
            try:
                time = datetime.datetime.strptime(match.group("time"), "%H%M%S").time()
            except ValueError:
                pass

        return date, time

    return None, None


def is_valid_photo_time(t):
    """True jika jam berada di rentang kerja (bukan jam makan siang)."""
    return (RANDOM_TIME_START <= t < RANDOM_TIME_BLOCK_START
            or RANDOM_TIME_BLOCK_END <= t < RANDOM_TIME_END)


def _random_time_total_seconds(date):
    morning   = (datetime.datetime.combine(date, RANDOM_TIME_BLOCK_START)
                 - datetime.datetime.combine(date, RANDOM_TIME_START)).seconds
    afternoon = (datetime.datetime.combine(date, RANDOM_TIME_END)
                 - datetime.datetime.combine(date, RANDOM_TIME_BLOCK_END)).seconds
    return morning + afternoon


def _offset_to_time(date, offset):
    morning = (datetime.datetime.combine(date, RANDOM_TIME_BLOCK_START)
               - datetime.datetime.combine(date, RANDOM_TIME_START)).seconds
    if offset < morning:
        return (datetime.datetime.combine(date, RANDOM_TIME_START)
                + datetime.timedelta(seconds=offset)).time()
    return (datetime.datetime.combine(date, RANDOM_TIME_BLOCK_END)
            + datetime.timedelta(seconds=offset - morning)).time()


def stable_random_time(image_path, date):
    total  = _random_time_total_seconds(date)
    seed   = f"{Path(image_path).name}|{date.isoformat()}"
    number = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")
    return _offset_to_time(date, number % total)


class OrderedRandomTimePlan:
    """Menetapkan jam random yang berurutan untuk sekumpulan foto per tanggal."""

    def __init__(self, requests):
        self.times = {}
        grouped = {}
        for path, date in requests:
            grouped.setdefault(date, []).append(path)
        for date, paths in grouped.items():
            self._assign(date, paths)

    def _assign(self, date, paths):
        total = _random_time_total_seconds(date)
        seed  = f"{date.isoformat()}|" + "|".join(Path(p).name for p in paths)
        rng   = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big"))
        offsets = sorted(
            rng.sample(range(total), len(paths)) if len(paths) <= total
            else [rng.randrange(total) for _ in paths]
        )
        for path, offset in zip(paths, offsets):
            self.times[(str(Path(path).resolve()), date.isoformat())] = _offset_to_time(date, offset)

    def get(self, image_path, date):
        return (self.times.get((str(Path(image_path).resolve()), date.isoformat()))
                or stable_random_time(image_path, date))


def get_random_time(image_path, date, plan=None):
    return plan.get(image_path, date) if plan else stable_random_time(image_path, date)


def get_timestamp(image_path, manual_datetime=None, manual_date=None, plan=None):
    """Tentukan timestamp akhir untuk satu foto."""
    if manual_datetime:
        return manual_datetime, "manual"

    if manual_date:
        return _timestamp_from_custom_date(image_path, manual_date, plan)

    dt, source = read_exif_datetime(image_path)
    if dt:
        return dt, source

    filename_date, filename_time = parse_filename_datetime(image_path)
    if filename_date:
        if filename_time:
            return datetime.datetime.combine(filename_date, filename_time), "nama file"
        t = get_random_time(image_path, filename_date, plan)
        return datetime.datetime.combine(filename_date, t), "nama file + jam random urut"

    return datetime.datetime.now().replace(microsecond=0), "fallback waktu sekarang"


def _timestamp_from_custom_date(image_path, custom_date, plan=None):
    """Gabungkan tanggal manual dengan jam terbaik yang tersedia."""
    dt, source = read_exif_datetime(image_path)
    if dt:
        if is_valid_photo_time(dt.time()):
            return datetime.datetime.combine(custom_date, dt.time()), f"tanggal manual + jam {source}"
        t = get_random_time(image_path, custom_date, plan)
        return datetime.datetime.combine(custom_date, t), f"tanggal manual + jam random ({source} tidak valid)"

    _, filename_time = parse_filename_datetime(image_path)
    if filename_time:
        return datetime.datetime.combine(custom_date, filename_time), "tanggal manual + jam nama file"

    t = get_random_time(image_path, custom_date, plan)
    return datetime.datetime.combine(custom_date, t), "tanggal manual + jam random"


def get_replace_date_timestamp(image_path, replacement_date, plan=None):
    """Timestamp untuk mode --replace-date: tanggal diganti, jam dipertahankan."""
    _, filename_time = parse_filename_datetime(image_path)
    if filename_time:
        return datetime.datetime.combine(replacement_date, filename_time), "tanggal manual + jam nama file"

    dt, source = read_exif_datetime(image_path)
    if dt:
        if is_valid_photo_time(dt.time()):
            return datetime.datetime.combine(replacement_date, dt.time()), f"tanggal manual + jam {source}"
        t = get_random_time(image_path, replacement_date, plan)
        return datetime.datetime.combine(replacement_date, t), f"tanggal manual + jam random ({source} tidak valid)"

    t = get_random_time(image_path, replacement_date, plan)
    return datetime.datetime.combine(replacement_date, t), "tanggal manual + jam random"


def get_forced_random_timestamp(image_path, date, plan=None):
    t = get_random_time(image_path, date, plan)
    return datetime.datetime.combine(date, t), "tanggal manual + jam random urut"


def _needs_random_time_date(image_path, args, manual_datetime, manual_date, replace_date, forced_random_date):
    """Kembalikan date jika foto ini perlu masuk OrderedRandomTimePlan, else None."""
    if forced_random_date:
        return forced_random_date

    if manual_datetime:
        return None

    if args.replace and replace_date:
        _, ft = parse_filename_datetime(image_path)
        if ft:
            return None
        dt, _ = read_exif_datetime(image_path)
        if dt:
            return None if is_valid_photo_time(dt.time()) else replace_date
        return replace_date

    if manual_date:
        dt, _ = read_exif_datetime(image_path)
        if dt:
            return None if is_valid_photo_time(dt.time()) else manual_date
        _, ft = parse_filename_datetime(image_path)
        return None if ft else manual_date

    # Normal mode: EXIF dipakai langsung, tidak butuh random time
    dt, _ = read_exif_datetime(image_path)
    if dt:
        return None

    filename_date, filename_time = parse_filename_datetime(image_path)
    if filename_date and not filename_time:
        return filename_date

    return None


def build_random_time_plan(images, args, manual_datetime=None, manual_date=None,
                           replace_date=None, forced_random_date=None):
    requests = [
        (path, date)
        for path in images
        if (date := _needs_random_time_date(
            path, args, manual_datetime, manual_date, replace_date, forced_random_date
        ))
    ]
    return OrderedRandomTimePlan(requests)


def print_exif_info(image_path):
    try:
        with Image.open(image_path) as img:
            exif = img._getexif()
            print(f"\n{image_path} ({img.size[0]}×{img.size[1]}px)")
    except Exception as e:
        print(f"Gagal membuka {image_path}: {e}")
        return

    if not exif:
        print("Tidak ada data EXIF")
        return

    for tag_id, value in sorted(exif.items()):
        name = TAGS.get(tag_id, f"Tag#{tag_id}")
        if any(k in name.lower() for k in ("date", "time", "make", "model", "software")):
            print(f"{name:30s}: {value}")


# ==================================================
# WATERMARK
# ==================================================

def add_logo(overlay, logo_image, scale):
    if logo_image is None:
        return
    width, height = overlay.size
    logo = logo_image.convert("RGBA")
    lh   = int(height * LOGO_HEIGHT_RATIO)
    lw   = int(logo.width * lh / logo.height)
    logo = logo.resize((lw, lh), Image.LANCZOS)
    r, g, b, a = logo.split()
    logo = Image.merge("RGBA", (r, g, b, a.point(lambda v: int(v * LOGO_OPACITY / 255))))
    overlay.paste(logo, (width - lw - scaled(LOGO_MARGIN_X, scale), scaled(LOGO_MARGIN_Y, scale)), logo)


def add_timestamp_text(overlay, timestamp, tag, scale, caption=False):
    width, height = overlay.size
    draw   = ImageDraw.Draw(overlay)
    margin = scaled(TEXT_MARGIN, scale)
    gap    = scaled(LINE_GAP, scale)
    ts_font  = load_font(scaled(TIMESTAMP_FONT_SIZE, scale))
    tag_font = load_font(scaled(TAG_FONT_SIZE, scale))

    def tw(text, font):
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0], bb[3] - bb[1]

    ts_text    = format_datetime_id(timestamp)
    ts_w, ts_h = tw(ts_text, ts_font)

    # Posisi dihitung dari bawah ke atas: tag → caption2 → caption1 → timestamp
    if tag:
        tag_w, tag_h = tw(tag, tag_font)
        tag_y    = height - margin - tag_h
        anchor_y = tag_y  # titik acuan untuk caption / timestamp
    else:
        tag_w = tag_h = tag_y = 0
        anchor_y = height - margin

    if caption:
        c1_w, c1_h = tw(CAPTION_LINE_1, ts_font)
        c2_w, c2_h = tw(CAPTION_LINE_2, ts_font)
        cg  = scaled(CAPTION_GAP,      scale)
        clg = scaled(CAPTION_LINE_GAP, scale)
        cb  = scaled(CAPTION_BOTTOM,   scale)
        c2_y = (anchor_y - cg - c2_h) if tag else (height - cb - c2_h)
        c1_y = c2_y - clg - c1_h
        ts_y = c1_y - cg  - ts_h
    else:
        ts_y = (anchor_y - gap - ts_h) if tag else (height - margin - ts_h)

    draw_text_shadow(draw, (width - margin - ts_w, ts_y), ts_text, ts_font, scale)

    if caption:
        draw_text_shadow(draw, (width - margin - c1_w, c1_y), CAPTION_LINE_1, ts_font, scale)
        draw_text_shadow(draw, (width - margin - c2_w, c2_y), CAPTION_LINE_2, ts_font, scale)

    if tag:
        draw_text_shadow(draw, (width - margin - tag_w, tag_y), tag, tag_font, scale)


def add_watermark(image_path, output_path, timestamp, logo_image=None,
                  tag=None, scale=1.0, skip_logo=False, image=None, caption=False):
    base = image if image is not None else Image.open(image_path)
    icc  = base.info.get("icc_profile")
    base = base.convert("RGB")

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    if not skip_logo:
        add_logo(overlay, logo_image, auto_scale(base.width, scale))
    add_timestamp_text(overlay, timestamp, tag, auto_scale(base.width, scale), caption=caption)

    result = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    save_kw = {"quality": DEFAULT_OUTPUT_QUALITY}
    if icc:
        save_kw["icc_profile"] = icc
    result.save(output_path, **save_kw)


# ==================================================
# REPLACE TIMESTAMP LAMA
# ==================================================

def detect_timestamp_crop(image):
    width, height = image.size
    scan_h = int(height * CROP_SCAN_BOTTOM_RATIO)

    if np is not None:
        bottom = np.array(image.convert("RGB"))[height - scan_h:, :]
        yellow = ((bottom[:, :, 0] > YELLOW_MIN_RED)
                  & (bottom[:, :, 1] > YELLOW_MIN_GREEN)
                  & (bottom[:, :, 2] < YELLOW_MAX_BLUE))
        rows = np.where(yellow)[0]
        topmost = int(rows.min()) if len(rows) else scan_h
    else:
        topmost = scan_h
        for y in range(scan_h):
            for x in range(width):
                r, g, b = image.getpixel((x, height - scan_h + y))[:3]
                if r > YELLOW_MIN_RED and g > YELLOW_MIN_GREEN and b < YELLOW_MAX_BLUE:
                    topmost = y
                    break
            if topmost != scan_h:
                break

    return max(0, int((scan_h - topmost) * CROP_PADDING_FACTOR))


def crop_keep_ratio(image, crop_bottom):
    width, height = image.size
    if crop_bottom <= 0 or crop_bottom >= height:
        return image
    return image.crop((round(width * crop_bottom / height), 0, width, height - crop_bottom))


# ==================================================
# FILE INPUT / OUTPUT
# ==================================================

def open_logo_file(path):
    try:
        with Image.open(path) as img:
            return img.convert("RGBA").copy()
    except Exception as e:
        print(f"Gagal membuka logo: {path} ({e})")
        return None


def download_logo_image(url):
    raw_url = url.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/")
    try:
        print(f"Logo lokal tidak ditemukan. Ambil logo online: {url}")
        with urllib.request.urlopen(raw_url, timeout=LOGO_DOWNLOAD_TIMEOUT) as r:
            data = r.read()
        with Image.open(io.BytesIO(data)) as img:
            return img.convert("RGBA").copy()
    except Exception as e:
        print(f"Gagal ambil logo online: {e}")
        return None


def resolve_logo(cli_logo):
    if cli_logo:
        logo = open_logo_file(cli_logo)
        return logo, {cli_logo} if logo else set()

    default = Path(__file__).parent / DEFAULT_LOGO_FILENAME
    if default.exists():
        return open_logo_file(default), {str(default)}

    return download_logo_image(DEFAULT_LOGO_URL), set()


def collect_images(inputs, recursive=False, exclude=None):
    excluded = {Path(p).resolve() for p in (exclude or [])}
    seen, result = set(), []

    for item in inputs:
        path = Path(item)
        if path.is_file():
            if path.resolve() not in excluded and path.suffix.lower() in IMAGE_EXTENSIONS:
                if path.resolve() not in seen:
                    result.append(path)
                    seen.add(path.resolve())
        elif path.is_dir():
            pattern = "**/*" if recursive else "*"
            for f in sorted(path.glob(pattern)):
                if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS and f.resolve() not in excluded:
                    if f.resolve() not in seen:
                        result.append(f)
                        seen.add(f.resolve())
        else:
            print(f"Tidak ditemukan: {item}")

    return result


def make_output_path(image_path, output_dir=None, suffix=DEFAULT_OUTPUT_SUFFIX):
    p = Path(image_path)
    name = f"{p.stem}{suffix}.jpg"
    return str(Path(output_dir) / name) if output_dir else str(p.parent / name)


# ==================================================
# CLI
# ==================================================

def parse_args():
    p = argparse.ArgumentParser(description="Tambah logo dan timestamp ke foto.")
    p.add_argument("images", nargs="*", help="File gambar atau folder. Kosong = folder script.")
    p.add_argument("--recursive", "-r", action="store_true", help="Scan subfolder.")
    p.add_argument("--datetime",  dest="datetime_text", metavar="YYYY-MM-DD [HH:MM:SS]",
                   help="Tanggal/jam manual.")
    p.add_argument("--random-time", action="store_true",
                   help="Jam random urut. Pakai bersama --datetime atau --replace-date.")
    p.add_argument("--replace-date", dest="replace_date_text", metavar="YYYY-MM-DD",
                   help="Ganti tanggal di foto yang sudah ada watermark.")
    p.add_argument("--logo",     help="Path logo. Default: logo.jpg di folder script.")
    p.add_argument("--no-logo",  action="store_true", help="Tanpa logo.")
    p.add_argument("--tag",      help="Teks tambahan di bawah timestamp.")
    p.add_argument("--caption",  action="store_true", help="Tampilkan caption proyek.")
    p.add_argument("--output-dir", help="Folder output.")
    p.add_argument("--suffix",   default=DEFAULT_OUTPUT_SUFFIX, help="Suffix nama file output.")
    p.add_argument("--scale",    type=float, default=DEFAULT_SCALE, help="Skala watermark.")
    p.add_argument("--replace",  action="store_true", help="Hapus timestamp lama, tulis baru.")
    p.add_argument("--info",     action="store_true", help="Tampilkan EXIF saja.")
    return p.parse_args()


def parse_manual_timestamp(text):
    if not text:
        return None, None
    for fmt, is_dt in [(MANUAL_DATETIME_FORMAT, True), (MANUAL_DATE_FORMAT, False)]:
        try:
            parsed = datetime.datetime.strptime(text, fmt)
            return (parsed, None) if is_dt else (None, parsed.date())
        except ValueError:
            pass
    print("Format --datetime salah. Gunakan: YYYY-MM-DD HH:MM:SS atau YYYY-MM-DD")
    sys.exit(1)


def parse_manual_date(text):
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text, MANUAL_DATE_FORMAT).date()
    except ValueError:
        print("Format --replace-date salah. Gunakan: YYYY-MM-DD")
        sys.exit(1)


def process_image(image_path, args, logo_image, manual_datetime, manual_date,
                  replace_date, forced_random_date, plan):
    if forced_random_date:
        timestamp, source = get_forced_random_timestamp(image_path, forced_random_date, plan)
    elif args.replace and replace_date:
        timestamp, source = get_replace_date_timestamp(image_path, replace_date, plan)
    else:
        timestamp, source = get_timestamp(image_path, manual_datetime, manual_date, plan)

    output_path = make_output_path(image_path, args.output_dir, args.suffix)

    if args.replace:
        original   = Image.open(image_path)
        crop_b     = detect_timestamp_crop(original)
        cropped    = crop_keep_ratio(original, crop_b) if crop_b > 0 else original
        if crop_b == 0:
            print(f"Timestamp kuning tidak terdeteksi: {Path(image_path).name}")
        add_watermark(image_path, output_path, timestamp,
                      tag=args.tag, scale=args.scale, skip_logo=True,
                      image=cropped, caption=args.caption)
    else:
        add_watermark(image_path, output_path, timestamp, logo_image=logo_image,
                      tag=args.tag, scale=args.scale, skip_logo=args.no_logo,
                      caption=args.caption)

    print(f"OK: {Path(image_path).name} -> {output_path} [{source}]")


def main():
    args = parse_args()

    if args.info:
        for p in args.images:
            print_exif_info(p)
        return

    manual_datetime, manual_date = parse_manual_timestamp(args.datetime_text)
    replace_date    = parse_manual_date(args.replace_date_text)
    forced_random_date = None

    if replace_date:
        args.replace = True

    if args.random_time:
        if manual_datetime:
            forced_random_date, manual_datetime = manual_datetime.date(), None
        elif manual_date:
            forced_random_date, manual_date = manual_date, None
        elif replace_date:
            forced_random_date = replace_date
        else:
            print("--random-time harus dipakai bersama --datetime atau --replace-date")
            sys.exit(1)

    if args.replace or args.no_logo:
        logo_image   = None
        logo_exclude = {args.logo} if args.logo else set()
        default_logo = Path(__file__).parent / DEFAULT_LOGO_FILENAME
        if default_logo.exists():
            logo_exclude.add(str(default_logo))
    else:
        logo_image, logo_exclude = resolve_logo(args.logo)

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    inputs = args.images or [str(Path(__file__).parent)]
    images = collect_images(inputs, recursive=args.recursive, exclude=logo_exclude)

    if not images:
        print("Tidak ada gambar yang bisa diproses.")
        sys.exit(1)

    plan = build_random_time_plan(
        images, args,
        manual_datetime=manual_datetime,
        manual_date=manual_date,
        replace_date=replace_date,
        forced_random_date=forced_random_date,
    )

    print(f"Font  : {find_font() or 'PIL default'}")
    print(f"Total : {len(images)} file\n")

    success = 0
    for image_path in images:
        try:
            process_image(image_path, args, logo_image, manual_datetime, manual_date,
                          replace_date, forced_random_date, plan)
            success += 1
        except Exception as e:
            print(f"Gagal: {image_path}: {e}")

    print(f"\nSelesai: {success}/{len(images)} file berhasil diproses.")


if __name__ == "__main__":
    main()
