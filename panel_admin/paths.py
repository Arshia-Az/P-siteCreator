"""Page URL paths: one place that decides what a valid, unique page address is."""
import unicodedata

from django.utils.text import slugify

# first path segment that must never be used by a page (system / other apps)
RESERVED_FIRST = {"api", "admin", "media", "static", "assets", "login", "logout", "accounts",
                  "builder", "panel", "favicon.ico", "robots.txt", "sitemap.xml"}
MAX_LEN, MAX_DEPTH = 190, 5


def reserved_first():
    """System names a page address may never start with: the static list + settings.BUILDER_RESERVED_PATHS +
    every top-level route of the project URLconf + MEDIA_URL/STATIC_URL. Pages are served from the site root,
    so this is what keeps /admin/, /api/, /media/ ... safe from being shadowed by a page."""
    from django.conf import settings
    names = set(RESERVED_FIRST) | {str(x).lower() for x in getattr(settings, "BUILDER_RESERVED_PATHS", [])}
    try:
        from django.urls import get_resolver
        for p in get_resolver().url_patterns:
            route = str(getattr(p.pattern, "_route", "") or getattr(p.pattern, "_regex", "") or "").lstrip("^")
            seg = route.split("/")[0]
            if seg and not seg.startswith(("<", "(", "[")):
                names.add(seg.lower())
    except Exception:
        pass
    for u in (getattr(settings, "MEDIA_URL", ""), getattr(settings, "STATIC_URL", "")):
        seg = (u or "").strip("/").split("/")[0]
        if seg and "://" not in seg:
            names.add(seg.lower())
    return names


def normalize_path(raw):
    """'  Services / Web Design ' -> 'services/web-design' (unicode/Persian letters are kept)."""
    raw = unicodedata.normalize("NFKC", str(raw or "")).strip().lower().replace("\\", "/")
    segs = [slugify(seg, allow_unicode=True) for seg in raw.split("/")]
    return "/".join(s for s in segs if s)


def validate_path(path, exclude_pk=None):
    """Returns a Persian error message, or None when the path is usable."""
    from .models import BuilderPage
    if not path:
        return "آدرس نمی‌تواند خالی باشد"
    if len(path) > MAX_LEN:
        return "آدرس خیلی طولانی است"
    segs = path.split("/")
    if len(segs) > MAX_DEPTH:
        return "حداکثر ۵ سطح مسیر مجاز است"
    if segs[0] in reserved_first():
        return f"«{segs[0]}» مسیر رزرو سیستم است؛ نام دیگری انتخاب کنید"
    qs = BuilderPage.objects.filter(slug__iexact=path)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    other = qs.first()
    if other:
        return f"این آدرس قبلاً برای صفحهٔ «{other.title}» استفاده شده است"
    return None


def suggest_path(path, exclude_pk=None):
    for i in range(2, 500):
        cand = f"{path}-{i}"
        if not validate_path(cand, exclude_pk):
            return cand
    return f"{path}-{abs(hash(path)) % 10000}"


def unique_path(base, exclude_pk=None):
    p = normalize_path(base) or "page"
    if p.split("/")[0] in reserved_first():
        p = "p-" + p
    return p if not validate_path(p, exclude_pk) else suggest_path(p, exclude_pk)
