#!/usr/bin/env python3
import argparse
import datetime
import hashlib
import io
import os
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
# Ubah nilai di bagian ini kalau mau atur tampilan watermark.
# ==================================================

DEFAULT_LOGO_FILENAME = "logo.jpg"
DEFAULT_LOGO_URL = "https://github.com/luthfeew/watermark/blob/main/logo.jpg"
LOGO_DOWNLOAD_TIMEOUT = 15
DEFAULT_OUTPUT_SUFFIX = "_wm"
DEFAULT_OUTPUT_QUALITY = 95
DEFAULT_SCALE = 1.0

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# Skala umum
BASE_IMAGE_WIDTH = 4000          # patokan skala untuk foto 4000px lebar
MIN_SCALE = 0.35
MAX_SCALE = 2.50

# Logo kanan atas
LOGO_HEIGHT_RATIO = 0.19         # 0.19 = 19% dari tinggi foto
LOGO_OPACITY = 220               # 0 transparan, 255 penuh
LOGO_MARGIN_X = 0
LOGO_MARGIN_Y = 0

# Teks kanan bawah
TEXT_COLOR = (255, 255, 0, 255)
SHADOW_COLOR = (120, 100, 0, 220)
TEXT_MARGIN = 35
TEXT_SHADOW_OFFSET = 5
TIMESTAMP_FONT_SIZE = 125
TAG_FONT_SIZE = 125
LINE_GAP = 30

# Mode replace timestamp lama
CROP_SCAN_BOTTOM_RATIO = 0.15    # area bawah yang discan
CROP_PADDING_FACTOR = 1.4
YELLOW_MIN_RED = 180
YELLOW_MIN_GREEN = 180
YELLOW_MAX_BLUE = 100

# Format tanggal
DAYS_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
MONTHS_ID = [
    "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]
EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"
MANUAL_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
MANUAL_DATE_FORMAT = "%Y-%m-%d"

# Jika nama file hanya punya tanggal tanpa jam, jam akan dibuat stabil dari nama file.
RANDOM_TIME_START = datetime.time(9, 0, 0)
RANDOM_TIME_BLOCK_START = datetime.time(12, 0, 0)
RANDOM_TIME_BLOCK_END = datetime.time(13, 0, 0)
RANDOM_TIME_END = datetime.time(15, 0, 0)
FILENAME_DATETIME_RE = re.compile(r"(?P<date>(?:19|20)\d{6})(?:[ _.-]?(?P<time>[0-2]\d[0-5]\d[0-5]\d))?")
EXIF_DATETIME_TAGS = [
    (36867, "DateTimeOriginal"),
    (36868, "DateTimeDigitized"),
    (306, "DateTime"),
]

# Font. Path.home() membuat folder user Windows otomatis mengikuti komputer masing-masing. (untuk custom font)
FONT_PATHS = [
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts/MiSansLatin-Regular.ttf",
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts/Roboto-Regular.ttf",
    Path("C:/Windows/Fonts/arial.ttf"),
    # Path("C:/Windows/Fonts/calibri.ttf"),
    # Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    # Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    # Path("/Library/Fonts/Arial.ttf"),
    # Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
]


# ==================================================
# FONT & TEKS
# ==================================================

def find_font():
    for path in FONT_PATHS:
        if path.exists():
            return str(path)
    return None


def load_font(size):
    font_path = find_font()
    if font_path:
        return ImageFont.truetype(font_path, size)
    return ImageFont.load_default()


def auto_scale(width, manual_scale=1.0):
    scale = (width / BASE_IMAGE_WIDTH) * manual_scale
    return max(MIN_SCALE, min(scale, MAX_SCALE))


def scaled(value, scale):
    return max(1, int(value * scale))


def format_datetime_id(dt):
    day = DAYS_ID[dt.weekday()]
    month = MONTHS_ID[dt.month]
    time_text = dt.strftime("%H.%M.%S")
    return f"{day}, {dt.day} {month} {dt.year} {time_text} WIB"


def draw_text_shadow(draw, position, text, font, scale):
    x, y = position
    offset = scaled(TEXT_SHADOW_OFFSET, scale)
    draw.text((x + offset, y + offset), text, font=font, fill=SHADOW_COLOR)
    draw.text((x, y), text, font=font, fill=TEXT_COLOR)


# ==================================================
# TANGGAL FOTO
# ==================================================

def read_exif_datetime(image_path):
    try:
        with Image.open(image_path) as image:
            exif = image._getexif()
    except Exception as error:
        return None, f"gagal baca EXIF ({error})"

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
    name = Path(image_path).stem

    for match in FILENAME_DATETIME_RE.finditer(name):
        date_text = match.group("date")
        time_text = match.group("time")

        try:
            date_value = datetime.datetime.strptime(date_text, "%Y%m%d").date()
        except ValueError:
            continue

        time_value = None
        if time_text:
            try:
                time_value = datetime.datetime.strptime(time_text, "%H%M%S").time()
            except ValueError:
                time_value = None

        return date_value, time_value

    return None, None


def stable_random_time(image_path, date_value):
    morning_seconds = (
        datetime.datetime.combine(date_value, RANDOM_TIME_BLOCK_START)
        - datetime.datetime.combine(date_value, RANDOM_TIME_START)
    ).seconds
    afternoon_seconds = (
        datetime.datetime.combine(date_value, RANDOM_TIME_END)
        - datetime.datetime.combine(date_value, RANDOM_TIME_BLOCK_END)
    ).seconds
    total_seconds = morning_seconds + afternoon_seconds

    seed_text = f"{Path(image_path).name}|{date_value.isoformat()}"
    seed_number = int.from_bytes(hashlib.sha256(seed_text.encode("utf-8")).digest()[:8], "big")
    offset = seed_number % total_seconds

    if offset < morning_seconds:
        base = datetime.datetime.combine(date_value, RANDOM_TIME_START)
        return (base + datetime.timedelta(seconds=offset)).time()

    base = datetime.datetime.combine(date_value, RANDOM_TIME_BLOCK_END)
    return (base + datetime.timedelta(seconds=offset - morning_seconds)).time()


def read_filename_datetime(image_path):
    date_value, time_value = parse_filename_datetime(image_path)

    if not date_value:
        return None, "nama file tidak berisi tanggal"

    if time_value:
        return (
            datetime.datetime.combine(date_value, time_value),
            "nama file",
        )

    random_time = stable_random_time(image_path, date_value)
    return (
        datetime.datetime.combine(date_value, random_time),
        "nama file + jam random",
    )


def get_timestamp(image_path, manual_datetime=None):
    if manual_datetime:
        return manual_datetime, "manual"

    photo_datetime, source = read_exif_datetime(image_path)
    if photo_datetime:
        return photo_datetime, source

    filename_datetime, source = read_filename_datetime(image_path)
    if filename_datetime:
        return filename_datetime, source

    return datetime.datetime.now().replace(microsecond=0), "fallback waktu sekarang"


def get_replace_date_timestamp(image_path, replacement_date):
    filename_date, filename_time = parse_filename_datetime(image_path)

    if filename_time:
        return (
            datetime.datetime.combine(replacement_date, filename_time),
            "tanggal manual + jam nama file",
        )

    photo_datetime, source = read_exif_datetime(image_path)
    if photo_datetime:
        return (
            datetime.datetime.combine(replacement_date, photo_datetime.time()),
            f"tanggal manual + jam {source}",
        )

    if filename_date:
        random_time = stable_random_time(image_path, replacement_date)
        return (
            datetime.datetime.combine(replacement_date, random_time),
            "tanggal manual + jam random",
        )

    now = datetime.datetime.now().replace(microsecond=0)
    return (
        datetime.datetime.combine(replacement_date, now.time()),
        "tanggal manual + fallback jam sekarang",
    )


def print_exif_info(image_path):
    try:
        with Image.open(image_path) as image:
            exif = image._getexif()
            print(f"\n{image_path} ({image.size[0]}×{image.size[1]}px)")
    except Exception as error:
        print(f"Gagal membuka {image_path}: {error}")
        return

    if not exif:
        print("Tidak ada data EXIF")
        return

    for tag_id, value in sorted(exif.items()):
        name = TAGS.get(tag_id, f"Tag#{tag_id}")
        if any(keyword in name.lower() for keyword in ("date", "time", "make", "model", "software")):
            print(f"{name:30s}: {value}")


# ==================================================
# WATERMARK
# ==================================================

def add_logo(overlay, logo_image, scale):
    if logo_image is None:
        return

    width, height = overlay.size
    logo = logo_image.convert("RGBA")
    logo_height = int(height * LOGO_HEIGHT_RATIO)
    logo_width = int(logo.width * logo_height / logo.height)
    logo = logo.resize((logo_width, logo_height), Image.LANCZOS)

    red, green, blue, alpha = logo.split()
    alpha = alpha.point(lambda value: int(value * LOGO_OPACITY / 255))
    logo = Image.merge("RGBA", (red, green, blue, alpha))

    x = width - logo_width - scaled(LOGO_MARGIN_X, scale)
    y = scaled(LOGO_MARGIN_Y, scale)
    overlay.paste(logo, (x, y), logo)


def add_timestamp_text(overlay, timestamp, tag, scale):
    width, height = overlay.size
    draw = ImageDraw.Draw(overlay)
    margin = scaled(TEXT_MARGIN, scale)
    timestamp_font = load_font(scaled(TIMESTAMP_FONT_SIZE, scale))
    tag_font = load_font(scaled(TAG_FONT_SIZE, scale))
    timestamp_text = format_datetime_id(timestamp)

    timestamp_box = draw.textbbox((0, 0), timestamp_text, font=timestamp_font)
    timestamp_width = timestamp_box[2] - timestamp_box[0]
    timestamp_height = timestamp_box[3] - timestamp_box[1]

    if not tag:
        x = width - margin - timestamp_width
        y = height - margin - timestamp_height
        draw_text_shadow(draw, (x, y), timestamp_text, timestamp_font, scale)
        return

    tag_box = draw.textbbox((0, 0), tag, font=tag_font)
    tag_width = tag_box[2] - tag_box[0]
    tag_height = tag_box[3] - tag_box[1]
    line_gap = scaled(LINE_GAP, scale)

    tag_x = width - margin - tag_width
    tag_y = height - margin - tag_height
    timestamp_x = width - margin - timestamp_width
    timestamp_y = tag_y - line_gap - timestamp_height

    draw_text_shadow(draw, (timestamp_x, timestamp_y), timestamp_text, timestamp_font, scale)
    draw_text_shadow(draw, (tag_x, tag_y), tag, tag_font, scale)


def add_watermark(image_path, output_path, timestamp, logo_image=None, tag=None, scale=1.0, skip_logo=False, image=None):
    base_image = image if image is not None else Image.open(image_path)
    base_image = base_image.convert("RGB")
    width, height = base_image.size
    final_scale = auto_scale(width, scale)

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if not skip_logo:
        add_logo(overlay, logo_image, final_scale)
    add_timestamp_text(overlay, timestamp, tag, final_scale)

    result = Image.alpha_composite(base_image.convert("RGBA"), overlay).convert("RGB")
    result.save(output_path, quality=DEFAULT_OUTPUT_QUALITY)
    return output_path


# ==================================================
# REPLACE TIMESTAMP LAMA
# ==================================================

def detect_timestamp_crop(image):
    width, height = image.size
    scan_height = int(height * CROP_SCAN_BOTTOM_RATIO)

    if np is not None:
        data = np.array(image.convert("RGB"))
        bottom = data[height - scan_height:, :]
        yellow = (
            (bottom[:, :, 0] > YELLOW_MIN_RED)
            & (bottom[:, :, 1] > YELLOW_MIN_GREEN)
            & (bottom[:, :, 2] < YELLOW_MAX_BLUE)
        )
        rows = np.where(yellow)[0]
        if len(rows) == 0:
            return 0
        topmost = int(rows.min())
    else:
        topmost = scan_height
        for y in range(scan_height):
            row_y = height - scan_height + y
            for x in range(width):
                red, green, blue = image.getpixel((x, row_y))[:3]
                if red > YELLOW_MIN_RED and green > YELLOW_MIN_GREEN and blue < YELLOW_MAX_BLUE:
                    topmost = y
                    break
            if topmost != scan_height:
                break

    return max(0, int((scan_height - topmost) * CROP_PADDING_FACTOR))


def crop_keep_ratio(image, crop_bottom):
    width, height = image.size
    if crop_bottom <= 0 or crop_bottom >= height:
        return image

    crop_left = round(width * crop_bottom / height)
    return image.crop((crop_left, 0, width, height - crop_bottom))


# ==================================================
# FILE INPUT / OUTPUT
# ==================================================

def github_blob_url_to_raw(url):
    if "github.com" in url and "/blob/" in url:
        return url.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/")
    return url


def open_logo_file(path):
    try:
        with Image.open(path) as image:
            return image.convert("RGBA").copy()
    except Exception as error:
        print(f"Gagal membuka logo: {path} ({error})")
        return None


def download_logo_image(url):
    download_url = github_blob_url_to_raw(url)

    try:
        print(f"Logo lokal tidak ditemukan. Ambil logo online tanpa menyimpan: {url}")
        with urllib.request.urlopen(download_url, timeout=LOGO_DOWNLOAD_TIMEOUT) as response:
            data = response.read()

        with Image.open(io.BytesIO(data)) as image:
            return image.convert("RGBA").copy()
    except Exception as error:
        print(f"Gagal ambil logo online: {error}")
        return None


def resolve_logo(cli_logo):
    if cli_logo:
        logo_image = open_logo_file(cli_logo)
        exclude = {cli_logo} if logo_image is not None else set()
        return logo_image, exclude

    default_logo = Path(__file__).parent / DEFAULT_LOGO_FILENAME
    if default_logo.exists():
        return open_logo_file(default_logo), {str(default_logo)}

    # Tidak disimpan ke file. Setiap script dijalankan dan logo lokal tidak ada,
    # logo akan diambil online lagi.
    return download_logo_image(DEFAULT_LOGO_URL), set()


def collect_images(inputs, recursive=False, exclude=None):
    excluded = {Path(path).resolve() for path in (exclude or [])}
    result = []

    for item in inputs:
        path = Path(item)

        if path.is_file():
            if path.resolve() not in excluded and path.suffix.lower() in IMAGE_EXTENSIONS:
                result.append(path)
            continue

        if path.is_dir():
            pattern = "**/*" if recursive else "*"
            result.extend(
                file_path
                for file_path in sorted(path.glob(pattern))
                if file_path.is_file()
                and file_path.suffix.lower() in IMAGE_EXTENSIONS
                and file_path.resolve() not in excluded
            )
            continue

        print(f"Tidak ditemukan: {item}")

    unique = []
    seen = set()
    for path in result:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(path)
            seen.add(resolved)
    return unique


def make_output_path(image_path, output_dir=None, suffix=DEFAULT_OUTPUT_SUFFIX):
    image_path = Path(image_path)
    output_name = f"{image_path.stem}{suffix}.jpg"

    if output_dir:
        return str(Path(output_dir) / output_name)
    return str(image_path.parent / output_name)


# ==================================================
# CLI
# ==================================================

def parse_args():
    parser = argparse.ArgumentParser(description="Tambah logo dan timestamp ke foto.")
    parser.add_argument("images", nargs="*", help="File gambar atau folder. Kosong = folder script.")
    parser.add_argument("--recursive", "-r", action="store_true", help="Scan subfolder.")
    parser.add_argument("--datetime", dest="datetime_text", help="Tanggal manual: YYYY-MM-DD HH:MM:SS")
    parser.add_argument(
        "--replace-date",
        dest="replace_date_text",
        help="Mode replace: ganti tanggal ke YYYY-MM-DD, jam tetap dari nama file/metadata jika ada.",
    )
    parser.add_argument("--logo", help="Path logo. Default: logo.jpg di folder script.")
    parser.add_argument("--tag", help="Teks tambahan di bawah timestamp.")
    parser.add_argument("--output-dir", help="Folder output.")
    parser.add_argument("--suffix", default=DEFAULT_OUTPUT_SUFFIX, help="Suffix output.")
    parser.add_argument("--scale", type=float, default=DEFAULT_SCALE, help="Skala watermark.")
    parser.add_argument("--replace", action="store_true", help="Hapus timestamp lama, lalu tulis timestamp baru.")
    parser.add_argument("--info", action="store_true", help="Tampilkan EXIF tanpa memproses foto.")
    return parser.parse_args()


def parse_manual_datetime(datetime_text):
    if not datetime_text:
        return None

    try:
        return datetime.datetime.strptime(datetime_text, MANUAL_DATETIME_FORMAT)
    except ValueError:
        print("Format --datetime salah. Gunakan: YYYY-MM-DD HH:MM:SS")
        sys.exit(1)


def parse_manual_date(date_text):
    if not date_text:
        return None

    try:
        return datetime.datetime.strptime(date_text, MANUAL_DATE_FORMAT).date()
    except ValueError:
        print("Format --replace-date salah. Gunakan: YYYY-MM-DD")
        sys.exit(1)


def process_image(image_path, args, logo_image, manual_datetime, replace_date):
    if args.replace and replace_date:
        timestamp, source = get_replace_date_timestamp(image_path, replace_date)
    else:
        timestamp, source = get_timestamp(image_path, manual_datetime)
    output_path = make_output_path(image_path, args.output_dir, args.suffix)

    if args.replace:
        original = Image.open(image_path)
        crop_bottom = detect_timestamp_crop(original)
        if crop_bottom > 0:
            cropped = crop_keep_ratio(original, crop_bottom)
        else:
            cropped = original
            print(f"Timestamp kuning tidak terdeteksi: {Path(image_path).name}")

        add_watermark(
            image_path=image_path,
            output_path=output_path,
            timestamp=timestamp,
            tag=args.tag,
            scale=args.scale,
            skip_logo=True,
            image=cropped,
        )
    else:
        add_watermark(
            image_path=image_path,
            output_path=output_path,
            timestamp=timestamp,
            logo_image=logo_image,
            tag=args.tag,
            scale=args.scale,
        )

    print(f"OK: {Path(image_path).name} -> {output_path} [{source}]")
    return output_path


def main():
    args = parse_args()

    if args.info:
        for image_path in args.images:
            print_exif_info(image_path)
        return

    manual_datetime = parse_manual_datetime(args.datetime_text)
    replace_date = parse_manual_date(args.replace_date_text)
    if replace_date:
        args.replace = True

    if args.replace:
        logo_image, logo_exclude = None, set()
    else:
        logo_image, logo_exclude = resolve_logo(args.logo)

    inputs = args.images or [str(Path(__file__).parent)]

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

    images = collect_images(inputs, recursive=args.recursive, exclude=logo_exclude)
    if not images:
        print("Tidak ada gambar yang bisa diproses.")
        sys.exit(1)

    font_path = find_font() or "PIL default"
    print(f"Font  : {font_path}")
    print(f"Total : {len(images)} file\n")

    success = 0
    for image_path in images:
        try:
            process_image(image_path, args, logo_image, manual_datetime, replace_date)
            success += 1
        except Exception as error:
            print(f"Gagal: {image_path}: {error}")

    print(f"\nSelesai: {success}/{len(images)} file berhasil diproses.")


if __name__ == "__main__":
    main()
