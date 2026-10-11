import logging
import os
import re
from functools import wraps

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.conf import settings
from django.core.exceptions import FieldError
from django.db import OperationalError, ProgrammingError
from django.db.models import Count, Max, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from .media_utils import process_avatar, process_upload
from .models import (BuilderPage, LayoutTemplate, MediaAsset, MediaGroup, PageCategory, PageComment, SiteSettings)
from .paths import normalize_path, suggest_path, validate_path
from .permissions import api_role, jbody, rank_of

User = get_user_model()
ms = lambda dt: int(dt.timestamp() * 1000)
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PHONE_RE = re.compile(r"^09\d{9}$")
ROLE_LABELS = {"admin": "مدیر", "editor": "ویراستار", "author": "نویسنده", "user": "کاربر عادی"}


def bad(msg, status=400):
    return JsonResponse({"error": msg}, status=status)


logger = logging.getLogger("builder.api")


def _safe(view):
    """Unexpected errors become a JSON message (and a log entry) instead of an HTML 500 page the UI cannot read."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except Http404:
            raise
        except Exception as exc:                                    # noqa: BLE001
            logger.exception("builder api error in %s", view.__name__)
            detail = f" ({type(exc).__name__}: {exc})" if settings.DEBUG else ""
            hint = " — اگر تازه کد را جایگزین کرده‌اید، makemigrations و migrate را اجرا کنید." if isinstance(exc, (TypeError, ProgrammingError, OperationalError, FieldError)) else ""
            return JsonResponse({"error": "خطای داخلی سرور" + hint + detail}, status=500)
    return wrapper


def endpoint(role, *methods):
    def deco(view):
        return require_http_methods(list(methods))(csrf_protect(api_role(role)(_safe(view))))
    return deco


def paginate(request, qs, default=20, mx=100):
    try:
        size = max(1, min(int(request.GET.get("size", default)), mx))
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        size, page = default, 1
    total = qs.count()
    return list(qs[(page - 1) * size: page * size]), {"total": total, "page": page, "size": size}


def unique_slug_for(model, base, pk=None):
    base = slugify(base or "", allow_unicode=True) or "item"
    slug, i = base, 2
    qs = model.objects.exclude(pk=pk) if pk else model.objects.all()
    while qs.filter(slug=slug).exists():
        slug, i = f"{base}-{i}", i + 1
    return slug


# ============================================================ categories
def ser_cat(c, n=None):
    return {"id": c.id, "name": c.name, "slug": c.slug, "description": c.description, "color": c.color,
            "parentId": c.parent_id, "order": c.order, "pagesCount": c.pages.count() if n is None else n}


def _cat_parent(d, me=None):
    pid = d.get("parentId")
    if not pid:
        return None, None
    p = PageCategory.objects.filter(pk=pid).first()
    if not p:
        return None, "دسته‌بندی والد پیدا نشد"
    node = p
    while node:                                   # forbid cycles
        if me and node.pk == me.pk:
            return None, "دسته‌بندی نمی‌تواند زیرمجموعهٔ خودش باشد"
        node = node.parent
    return p, None


@endpoint("author", "GET")
def categories_list(request):
    qs = PageCategory.objects.annotate(n=Count("pages")).order_by("order", "name")
    return JsonResponse({"results": [ser_cat(c, c.n) for c in qs],
                         "uncategorized": BuilderPage.objects.filter(category__isnull=True).count()})


@endpoint("editor", "POST")
def category_create(request):
    d = jbody(request)
    if d is None:
        return bad("invalid JSON")
    name = (d.get("name") or "").strip()[:80]
    if len(name) < 2:
        return bad("نام دسته‌بندی حداقل ۲ حرف باشد")
    if PageCategory.objects.filter(name=name).exists():
        return bad("این دسته‌بندی قبلاً وجود دارد", 409)
    parent, err = _cat_parent(d)
    if err:
        return bad(err)
    color = d.get("color") if COLOR_RE.match(str(d.get("color") or "")) else "#f2a63a"
    c = PageCategory.objects.create(
        name=name, slug=unique_slug_for(PageCategory, d.get("slug") or name), description=(d.get("description") or "")[:300],
        color=color, parent=parent, order=(PageCategory.objects.aggregate(m=Max("order"))["m"] or 0) + 1)
    return JsonResponse(ser_cat(c, 0), status=201)


@endpoint("editor", "PUT", "PATCH")
def category_update(request, pk):
    c = get_object_or_404(PageCategory, pk=pk)
    d = jbody(request)
    if d is None:
        return bad("invalid JSON")
    if "name" in d:
        name = (d["name"] or "").strip()[:80]
        if len(name) < 2:
            return bad("نام دسته‌بندی حداقل ۲ حرف باشد")
        if PageCategory.objects.filter(name=name).exclude(pk=c.pk).exists():
            return bad("این دسته‌بندی قبلاً وجود دارد", 409)
        c.name = name
    if "description" in d:
        c.description = (d["description"] or "")[:300]
    if "color" in d and COLOR_RE.match(str(d["color"] or "")):
        c.color = d["color"]
    if "parentId" in d:
        parent, err = _cat_parent(d, me=c)
        if err:
            return bad(err)
        c.parent = parent
    c.save()
    return JsonResponse(ser_cat(c))


@endpoint("editor", "DELETE")
def category_delete(request, pk):
    get_object_or_404(PageCategory, pk=pk).delete()     # pages become "بدون دسته‌بندی" (SET_NULL)
    return JsonResponse({"deleted": True})


@endpoint("editor", "POST")
def category_assign(request):
    d = jbody(request)
    if d is None or not isinstance(d.get("pageIds"), list):
        return bad("pageIds لازم است")
    cat = None
    if d.get("categoryId"):
        cat = PageCategory.objects.filter(pk=d["categoryId"]).first()
        if not cat:
            return bad("دسته‌بندی پیدا نشد", 404)
    n = BuilderPage.objects.filter(pk__in=[i for i in d["pageIds"] if isinstance(i, int)]).update(category=cat)
    return JsonResponse({"updated": n})


@endpoint("editor", "POST")
def category_reorder(request):
    d = jbody(request)
    if d is None or not isinstance(d.get("ids"), list):
        return bad("ids لازم است")
    for i, pk in enumerate(d["ids"]):
        PageCategory.objects.filter(pk=pk).update(order=i)
    return JsonResponse({"ok": True})


# ============================================================ media
def ser_media(m):
    url = m.file.url
    thumb = m.thumb.url if m.thumb else url
    return {"id": m.id, "title": m.title, "alt": m.alt, "url": url, "thumbUrl": thumb, "width": m.width,
            "height": m.height, "size": m.size_bytes, "groupId": m.group_id,
            "groupName": m.group.name if m.group_id else "بی‌گروه",
            "srcset": f"{thumb} 480w, {url} {m.width}w", "createdAt": ms(m.created_at)}


def _used_in(m):
    return list(BuilderPage.objects.filter(rendered_html__contains=m.file.url).values("id", "title")[:10])


@endpoint("author", "GET")
def media_groups(request):
    groups = MediaGroup.objects.annotate(n=Count("assets"))
    return JsonResponse({"results": [{"id": g.id, "name": g.name, "count": g.n} for g in groups],
                         "ungrouped": MediaAsset.objects.filter(group__isnull=True).count(),
                         "total": MediaAsset.objects.count()})


@endpoint("author", "POST")
def media_group_create(request):
    d = jbody(request)
    name = ((d or {}).get("name") or "").strip()[:80]
    if len(name) < 2:
        return bad("نام گروه حداقل ۲ حرف باشد")
    if name == "بی‌گروه" or MediaGroup.objects.filter(name=name).exists():
        return bad("این گروه قبلاً وجود دارد", 409)
    g = MediaGroup.objects.create(name=name)
    return JsonResponse({"id": g.id, "name": g.name, "count": 0}, status=201)


@endpoint("editor", "PUT", "PATCH")
def media_group_update(request, pk):
    g = get_object_or_404(MediaGroup, pk=pk)
    name = ((jbody(request) or {}).get("name") or "").strip()[:80]
    if len(name) < 2 or name == "بی‌گروه" or MediaGroup.objects.filter(name=name).exclude(pk=pk).exists():
        return bad("نام گروه نامعتبر یا تکراری است", 409)
    g.name = name
    g.save()
    return JsonResponse({"id": g.id, "name": g.name})


@endpoint("editor", "DELETE")
def media_group_delete(request, pk):
    get_object_or_404(MediaGroup, pk=pk).delete()      # its images move to "بی‌گروه"
    return JsonResponse({"deleted": True})


@endpoint("author", "GET")
def media_list(request):
    qs = MediaAsset.objects.select_related("group")
    g = request.GET.get("group")
    if g == "none":
        qs = qs.filter(group__isnull=True)
    elif g and g.isdigit():
        qs = qs.filter(group_id=int(g))
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(alt__icontains=q) | Q(original_name__icontains=q))
    items, meta = paginate(request, qs, default=40, mx=100)
    return JsonResponse({"results": [ser_media(m) for m in items], **meta})


@endpoint("author", "POST")
def media_upload(request):
    files = request.FILES.getlist("files")[:10]
    if not files:
        return bad("فایلی ارسال نشد")
    group = None
    gid = request.POST.get("group_id")
    if gid and gid != "none":
        group = MediaGroup.objects.filter(pk=gid).first()
        if not group:
            return bad("گروه پیدا نشد", 404)
    out, errors = [], []
    for f in files:
        try:
            main, thumb, w, h = process_upload(f)
        except ValueError as e:
            errors.append({"name": f.name, "error": str(e)})
            continue
        stem = re.sub(r"[-_]+", " ", os.path.splitext(os.path.basename(f.name))[0]).strip()[:150]
        title = (request.POST.get("title") or stem)[:150]
        m = MediaAsset(group=group, title=title, alt=(request.POST.get("alt") or title)[:200], width=w, height=h,
                       size_bytes=len(main), original_name=os.path.basename(f.name)[:200], created_by=request.user)
        m.file.save("x.webp", ContentFile(main), save=False)
        m.thumb.save("t.webp", ContentFile(thumb), save=False)
        m.save()
        out.append(ser_media(m))
    return JsonResponse({"results": out, "errors": errors}, status=201 if out else 400)


@endpoint("author", "PUT", "PATCH")
def media_update(request, pk):
    m = get_object_or_404(MediaAsset, pk=pk)
    d = jbody(request)
    if d is None:
        return bad("invalid JSON")
    if "title" in d:
        m.title = (d["title"] or "")[:150]
    if "alt" in d:
        m.alt = (d["alt"] or "")[:200]
    if "groupId" in d:
        m.group = MediaGroup.objects.filter(pk=d["groupId"]).first() if d["groupId"] else None
    m.save(update_fields=["title", "alt", "group"])
    return JsonResponse(ser_media(m))


@endpoint("editor", "DELETE")
def media_delete(request, pk):
    m = get_object_or_404(MediaAsset, pk=pk)
    used = _used_in(m)
    if used and request.GET.get("force") != "1":
        return JsonResponse({"error": "این تصویر در صفحات زیر استفاده شده است", "usedIn": used}, status=409)
    m.delete()
    return JsonResponse({"deleted": True})


@endpoint("editor", "POST")
def media_bulk(request):
    d = jbody(request)
    if d is None or not isinstance(d.get("ids"), list):
        return bad("ids لازم است")
    qs = MediaAsset.objects.filter(pk__in=[i for i in d["ids"] if isinstance(i, int)])
    if d.get("action") == "move":
        g = MediaGroup.objects.filter(pk=d.get("groupId")).first() if d.get("groupId") else None
        return JsonResponse({"moved": qs.update(group=g)})
    if d.get("action") == "delete":
        deleted, skipped = 0, []
        for m in qs:
            if _used_in(m) and not d.get("force"):
                skipped.append(m.id)
                continue
            m.delete()
            deleted += 1
        return JsonResponse({"deleted": deleted, "skippedInUse": skipped})
    return bad("action نامعتبر است")


# ============================================================ comments (moderation)
def ser_comment(c, with_replies=True):
    data = {"id": c.id, "pageId": c.page_id, "pageTitle": c.page.title, "pageSlug": c.page.slug,
            "categoryId": c.page.category_id, "parentId": c.parent_id, "name": c.author_name, "email": c.author_email,
            "body": c.body, "status": c.status, "isRead": c.is_read, "isAdmin": c.is_admin_reply, "createdAt": ms(c.created_at)}
    if with_replies:
        data["replies"] = [ser_comment(r, False) for r in c.replies.all().order_by("created_at")]
    return data


@endpoint("editor", "GET")
def comments_list(request):
    base = PageComment.objects.filter(parent__isnull=True)
    meta = {"total": base.count(), "unread": base.filter(is_read=False).count()}
    for s, _ in PageComment.STATUS:
        meta[s] = base.filter(status=s).count()
    qs = base.select_related("page").prefetch_related("replies")
    g = request.GET
    if g.get("status") in dict(PageComment.STATUS):
        qs = qs.filter(status=g["status"])
    if g.get("unread") == "1":
        qs = qs.filter(is_read=False)
    if (g.get("page_id") or "").isdigit():
        qs = qs.filter(page_id=int(g["page_id"]))
    if (g.get("category") or "").isdigit():
        qs = qs.filter(page__category_id=int(g["category"]))
    elif g.get("category") == "none":
        qs = qs.filter(page__category__isnull=True)
    if (g.get("q") or "").strip():
        q = g["q"].strip()
        qs = qs.filter(Q(body__icontains=q) | Q(author_name__icontains=q) | Q(author_email__icontains=q))
    qs = qs.order_by("created_at" if g.get("order") == "old" else "-created_at")
    items, pg = paginate(request, qs, default=20, mx=100)
    return JsonResponse({"results": [ser_comment(c) for c in items], "counts": meta, **pg})


@endpoint("editor", "GET")
def comments_unread_count(request):
    return JsonResponse({"unread": PageComment.objects.filter(parent__isnull=True, is_read=False).count()})


@endpoint("editor", "POST")
def comment_action(request, pk):
    c = get_object_or_404(PageComment, pk=pk)
    d = jbody(request)
    if d is None:
        return bad("invalid JSON")
    act = d.get("action")
    if act in dict(PageComment.STATUS):
        c.status, c.is_read = act, True
        c.save(update_fields=["status", "is_read"])
    elif act == "read":
        c.is_read = True
        c.save(update_fields=["is_read"])
    elif act == "reply":
        body = (d.get("body") or "").strip()[:2000]
        if len(body) < 2:
            return bad("متن پاسخ خیلی کوتاه است")
        root = c.parent or c
        PageComment.objects.create(page=root.page, parent=root, author_name=request.user.get_fullName().strip() or "مدیر سایت",
                                   body=body, status="approved", is_read=True, is_admin_reply=True, user=request.user)
        if root.status == "pending":
            root.status = "approved"          # answering a comment publishes it together with the reply
        root.is_read = True
        root.save(update_fields=["status", "is_read"])
        c = root
    else:
        return bad("action نامعتبر است")
    return JsonResponse(ser_comment(PageComment.objects.select_related("page").prefetch_related("replies").get(pk=c.pk)))


@endpoint("editor", "DELETE")
def comment_delete(request, pk):
    get_object_or_404(PageComment, pk=pk).delete()
    return JsonResponse({"deleted": True})


@endpoint("editor", "POST")
def comments_bulk(request):
    d = jbody(request)
    if d is None or not isinstance(d.get("ids"), list):
        return bad("ids لازم است")
    qs = PageComment.objects.filter(pk__in=[i for i in d["ids"] if isinstance(i, int)])
    act = d.get("action")
    if act in dict(PageComment.STATUS):
        return JsonResponse({"updated": qs.update(status=act, is_read=True)})
    if act == "read":
        return JsonResponse({"updated": qs.update(is_read=True)})
    if act == "delete":
        return JsonResponse({"deleted": qs.delete()[0]})
    return bad("action نامعتبر است")


# ============================================================ users + profile
def ser_user(u):
    return {"id": u.id, "firstName": u.first_name, "lastName": u.last_name, "fullName": u.get_fullName().strip(),
            "phone": u.phone_number, "email": getattr(u, "email", ""), "bio": getattr(u, "bio", ""),
            "role": "admin" if u.is_superuser else getattr(u, "role", "user"),
            "roleLabel": ROLE_LABELS["admin" if u.is_superuser else getattr(u, "role", "user")],
            "isActive": u.is_active, "avatar": u.avatar.url if u.avatar else "", "createdAt": ms(u.created_at)}


def _other_admins(user):
    return User.objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(role="admin")).exclude(pk=user.pk)


@endpoint("admin", "GET")
def users_list(request):
    qs = User.objects.all().order_by("-created_at")
    g = request.GET
    if g.get("role") in ROLE_LABELS:
        qs = qs.filter(Q(role=g["role"]) | (Q(is_superuser=True) if g["role"] == "admin" else Q(pk__in=[])))
    if g.get("active") in ("0", "1"):
        qs = qs.filter(is_active=g["active"] == "1")
    q = (g.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(phone_number__icontains=q))
    items, pg = paginate(request, qs, default=20, mx=100)
    counts = {r: User.objects.filter(role=r).count() for r in ROLE_LABELS}
    counts["total"], counts["inactive"] = User.objects.count(), User.objects.filter(is_active=False).count()
    return JsonResponse({"results": [ser_user(u) for u in items], "counts": counts, **pg})


def _clean_email(v):
    v = (v or "").strip()
    if not v:
        return ""
    try:
        validate_email(v)
    except ValidationError:
        raise ValueError("ایمیل نامعتبر است")
    return v[:254]


@endpoint("admin", "POST")
def user_create(request):
    if not hasattr(User, "role"):
        return bad("مدل User هنوز فیلد role ندارد: accounts/models.py را جایگزین و makemigrations + migrate را اجرا کنید", 500)
    d = jbody(request)
    if d is None:
        return bad("invalid JSON")
    phone = str(d.get("phone") or "").strip()
    if not PHONE_RE.match(phone):
        return bad("شمارهٔ موبایل باید ۱۱ رقم و با ۰۹ شروع شود")
    if User.objects.filter(phone_number=phone).exists():
        return bad("این شماره قبلاً ثبت شده است", 409)
    role = d.get("role", "user")
    if role not in ROLE_LABELS:
        return bad("نقش نامعتبر است")
    pw = d.get("password") or ""
    if len(pw) < 8:
        return bad("رمز عبور حداقل ۸ کاراکتر باشد")
    try:
        email = _clean_email(d.get("email"))
    except ValueError as e:
        return bad(str(e))
    u = User.objects.create_user(phone, pw, first_name=(d.get("firstName") or "").strip()[:40] or "کاربر",
                                 last_name=(d.get("lastName") or "").strip()[:40], role=role, email=email,
                                 is_active=bool(d.get("isActive", True)))
    return JsonResponse(ser_user(u), status=201)


def _protect_superuser(request, target):
    """Only a superuser may change or delete a superuser (an admin could otherwise reset their password and take over)."""
    if target.is_superuser and not request.user.is_superuser:
        return JsonResponse({"error": "فقط سوپریوزر می‌تواند حساب سوپریوزر را تغییر دهد"}, status=403)
    return None


@endpoint("admin", "PUT", "PATCH")
def user_update(request, pk):
    u = get_object_or_404(User, pk=pk)
    blocked = _protect_superuser(request, u)
    if blocked:
        return blocked
    d = jbody(request)
    if d is None:
        return bad("invalid JSON")
    new_role = d.get("role", u.role)
    new_active = bool(d.get("isActive", u.is_active))
    if new_role not in ROLE_LABELS:
        return bad("نقش نامعتبر است")
    if u.pk == request.user.pk and (new_role != u.role or not new_active):
        return bad("نمی‌توانی نقش یا وضعیت حساب خودت را تغییر بدهی", 409)
    was_admin = u.is_superuser or u.role == "admin"
    if was_admin and (new_role != "admin" or not new_active) and not u.is_superuser and not _other_admins(u).exists():
        return bad("حداقل یک مدیر فعال باید در سیستم بماند", 409)
    if "firstName" in d:
        u.first_name = (d["firstName"] or "").strip()[:40] or u.first_name
    if "lastName" in d:
        u.last_name = (d["lastName"] or "").strip()[:40]
    if "bio" in d:
        u.bio = (d["bio"] or "")[:300]
    if "email" in d:
        try:
            u.email = _clean_email(d["email"])
        except ValueError as e:
            return bad(str(e))
    if d.get("password"):
        if len(d["password"]) < 8:
            return bad("رمز عبور حداقل ۸ کاراکتر باشد")
        u.set_password(d["password"])
    if not u.is_superuser:
        u.role = new_role
    u.is_active = new_active
    u.save()
    return JsonResponse(ser_user(u))


@endpoint("admin", "DELETE")
def user_delete(request, pk):
    u = get_object_or_404(User, pk=pk)
    blocked = _protect_superuser(request, u)
    if blocked:
        return blocked
    if u.pk == request.user.pk:
        return bad("نمی‌توانی حساب خودت را حذف کنی", 409)
    if (u.is_superuser or u.role == "admin") and not _other_admins(u).exists():
        return bad("حداقل یک مدیر فعال باید در سیستم بماند", 409)
    if u.avatar:
        u.avatar.delete(save=False)
    u.delete()
    return JsonResponse({"deleted": True})


@endpoint("author", "GET", "PUT", "PATCH")
def me(request):
    u = request.user
    if request.method != "GET":
        d = jbody(request)
        if d is None:
            return bad("invalid JSON")
        if "firstName" in d:
            u.first_name = (d["firstName"] or "").strip()[:40] or u.first_name
        if "lastName" in d:
            u.last_name = (d["lastName"] or "").strip()[:40]
        if "bio" in d:
            u.bio = (d["bio"] or "")[:300]
        if "email" in d:
            try:
                u.email = _clean_email(d["email"])
            except ValueError as e:
                return bad(str(e))
        if d.get("newPassword"):
            if not u.check_password(d.get("currentPassword") or ""):
                return bad("رمز فعلی درست نیست", 403)
            if len(d["newPassword"]) < 8:
                return bad("رمز جدید حداقل ۸ کاراکتر باشد")
            u.set_password(d["newPassword"])
        u.save()
        if d.get("newPassword"):
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, u)
    return JsonResponse(ser_user(u))


@endpoint("author", "POST", "DELETE")
def me_avatar(request):
    u = request.user
    if u.avatar:
        u.avatar.delete(save=False)
        u.avatar = None
    if request.method == "POST":
        f = request.FILES.get("avatar")
        if not f:
            return bad("فایلی ارسال نشد")
        try:
            data = process_avatar(f)
        except ValueError as e:
            return bad(str(e))
        u.avatar.save("a.webp", ContentFile(data), save=False)
    u.save()
    return JsonResponse(ser_user(u))


# ============================================================ site settings + layout templates
MAX_JSON = 600_000


def _size_ok(v):
    import json
    return len(json.dumps(v, ensure_ascii=False)) <= MAX_JSON


@endpoint("author", "GET")
def site_get(request):
    s = SiteSettings.load()
    return JsonResponse({"colors": s.colors, "header": s.header_json, "footer": s.footer_json, "homePageId": s.home_page_id})


@endpoint("editor", "PUT", "PATCH")
def site_put(request):
    d = jbody(request)
    if d is None:
        return bad("invalid JSON")
    s = SiteSettings.load()
    if ("header" in d or "footer" in d or "homePageId" in d) and rank_of(request.user) < 3:
        return bad("تغییر هدر/فوتر سراسری و صفحهٔ اصلی فقط برای مدیر مجاز است", 403)   # site-wide markup runs on every public page
    if "colors" in d:
        c = d["colors"]
        if not isinstance(c, dict):
            return bad("رنگ‌ها نامعتبرند")
        hexkeys = ("accent", "contrast", "btnBg", "btnColor", "textColor", "headingColor", "linkColor")
        if any(c.get(k) and not COLOR_RE.match(str(c[k])) for k in hexkeys):
            return bad("رنگ‌ها نامعتبرند")
        clean = {k: c[k] for k in ("preset", "accent", "contrast") + hexkeys if c.get(k) and k != "preset"}
        if c.get("preset"):
            clean["preset"] = str(c["preset"])[:30]
        for flag in ("btnUnify", "textOn"):
            clean[flag] = bool(c.get(flag))
        try:
            clean["btnRadius"] = max(0, min(40, int(c.get("btnRadius", 8))))
        except (TypeError, ValueError):
            clean["btnRadius"] = 8
        s.colors = clean
    for key, field in (("header", "header_json"), ("footer", "footer_json")):
        if key in d:
            if d[key] is not None and (not isinstance(d[key], dict) or not _size_ok(d[key])):
                return bad(f"{key} نامعتبر یا بیش از حد بزرگ است")
            setattr(s, field, d[key])
    if "homePageId" in d:
        if d["homePageId"] and not BuilderPage.objects.filter(pk=d["homePageId"]).exists():
            return bad("صفحه پیدا نشد", 404)
        s.home_page_id = d["homePageId"] or None
    s.save()
    return JsonResponse({"colors": s.colors, "header": s.header_json, "footer": s.footer_json, "homePageId": s.home_page_id})


def ser_tpl(t, full=False):
    data = {"id": t.id, "kind": t.kind, "name": t.name, "createdAt": ms(t.created_at)}
    if full:
        data["doc"], data["render"] = t.doc, t.render
    return data


@endpoint("author", "GET")
def templates_list(request):
    qs = LayoutTemplate.objects.all()
    if request.GET.get("kind") in ("header", "footer"):
        qs = qs.filter(kind=request.GET["kind"])
    full = request.GET.get("full") == "1"
    return JsonResponse({"results": [ser_tpl(t, full) for t in qs]})


@endpoint("author", "GET")
def template_get(request, pk):
    return JsonResponse(ser_tpl(get_object_or_404(LayoutTemplate, pk=pk), True))


@endpoint("editor", "POST")
def template_create(request):
    d = jbody(request)
    if d is None or d.get("kind") not in ("header", "footer") or not isinstance(d.get("doc"), dict):
        return bad("kind و doc لازم است")
    name = (d.get("name") or "").strip()[:80]
    if len(name) < 2:
        return bad("نام قالب حداقل ۲ حرف باشد")
    render = d.get("render") if isinstance(d.get("render"), dict) else {}
    if not _size_ok(d["doc"]) or not _size_ok(render):
        return bad("قالب بیش از حد بزرگ است")
    if LayoutTemplate.objects.filter(kind=d["kind"], name=name).exists():
        return bad("قالبی با این نام وجود دارد", 409)
    t = LayoutTemplate.objects.create(kind=d["kind"], name=name, doc=d["doc"], render=render, created_by=request.user)
    return JsonResponse(ser_tpl(t, True), status=201)


@endpoint("editor", "DELETE")
def template_delete(request, pk):
    get_object_or_404(LayoutTemplate, pk=pk).delete()
    return JsonResponse({"deleted": True})


# ============================================================ page address (live validation for the builder UI)
@endpoint("author", "GET")
def slug_check(request):
    path = normalize_path(request.GET.get("slug"))
    ex = request.GET.get("exclude")
    ex = int(ex) if (ex or "").isdigit() else None
    err = validate_path(path, exclude_pk=ex)
    return JsonResponse({"ok": err is None, "normalized": path, "error": err,
                         "suggestion": suggest_path(path, ex) if err and path and "قبلاً" in err else None})
