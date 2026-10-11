"""Safe image pipeline: validate -> strip metadata -> resize -> WebP (+ thumbnail)."""
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_SIDE = 2400
THUMB_SIDE = 480
ALLOWED_FORMATS = {"JPEG", "PNG", "GIF", "WEBP", "BMP"}   # SVG is deliberately NOT allowed (script/XSS vector)
Image.MAX_IMAGE_PIXELS = 40_000_000                         # decompression-bomb guard


def _open(uploaded):
    if uploaded.size > MAX_UPLOAD_BYTES:
        raise ValueError("حجم فایل بیشتر از ۸ مگابایت است")
    try:
        Image.open(uploaded).verify()
        uploaded.seek(0)
        img = Image.open(uploaded)
        fmt = img.format
        img.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        raise ValueError("فایل، تصویر معتبری نیست")
    if fmt not in ALLOWED_FORMATS:
        raise ValueError("فقط تصاویر JPG، PNG، GIF، WEBP و BMP مجازند")
    img = ImageOps.exif_transpose(img)           # honour camera rotation; re-encoding drops all EXIF/GPS
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    return img.convert("RGBA" if has_alpha else "RGB")


def _webp(img, quality):
    buf = BytesIO()
    img.save(buf, "WEBP", quality=quality, method=6)
    return buf.getvalue()


def process_upload(uploaded):
    """-> (main_webp_bytes, thumb_webp_bytes, width, height). Raises ValueError with a Persian message."""
    img = _open(uploaded)
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    main = _webp(img, 82)
    w, h = img.size
    th = img.copy()
    th.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
    return main, _webp(th, 74), w, h


def process_avatar(uploaded, side=256):
    img = ImageOps.fit(_open(uploaded), (side, side), Image.LANCZOS)
    return _webp(img, 80)
