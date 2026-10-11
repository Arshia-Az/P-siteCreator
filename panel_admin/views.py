from django.shortcuts import render
from django.views import View


class PanelView(View):
    def get(self, request):
        return render(request, 'fa/admin/panel/page-builder.html')


import json
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import BuilderPage, PageCategory, SlugRedirect
from .paths import normalize_path, suggest_path, unique_path, validate_path
from .permissions import api_role, at_least, contains_html_widget, rank_of
from .public import public_page_view as _public_page_view


# ------------------------------------------------------------------ helpers
def staff_api_required(view):
    """Kept for backwards compatibility: any staff member. New code uses api_role(<minimum role>)."""
    return api_role("author")(view)


def page_to_dict(p, full=True):
    """
    full=False -> lightweight (used by the pages list; NO blocks, because blocks can
                  contain big base64 images and the list would become huge).
    full=True  -> everything the builder needs to open the page for editing.
    """
    content = p.content_json or {}
    data = {
        "id": p.id,
        "title": p.title,
        "slug": p.slug,
        "status": p.status,
        "seoTitle": p.seo_title,
        "metaDescription": p.meta_description,
        "metaKeywords": p.meta_keywords,
        "ogImage": p.og_image,
        "robots": p.robots,
        "categoryId": p.category_id,
        "allowComments": p.allow_comments,
        "schemaType": p.schema_type,
        "schemaJson": p.schema_json,
        "blocksCount": len(content.get("blocks", [])),
        "createdBy": p.created_by_id,
        # published page whose stored HTML has no site-wide markers yet -> needs one re-save to follow global header/footer/colours
        "needsResave": p.status == "published" and "<!--PB:HEADER-->" not in (p.rendered_html or ""),
        "createdAt": p.created_at.timestamp() * 1000,
        "updatedAt": p.updated_at.timestamp() * 1000,
    }
    if full:
        data["blocks"] = content.get("blocks", [])
        data["headerOverride"] = content.get("headerOverride")
        data["footerOverride"] = content.get("footerOverride")
    return data


def _is_privileged(user):
    return at_least(user, "editor")


def can_edit(user, page):
    """Editors/admins edit any page; authors only the pages they created."""
    return _is_privileged(user) or (page.created_by_id is not None and page.created_by_id == user.id)


def clean_payload(user, page, data):
    """
    Applies the role rules to an incoming payload and returns (data, error_response).
      author : can write the content of their OWN pages as drafts. They cannot publish, change the stored public
               HTML, categories, comment switch, or the address of a published page, and cannot add custom-code
               (HTML) widgets -- that code would run on the public site.
      editor/admin: everything.
    """
    if _is_privileged(user):
        return data, None
    data = dict(data)
    for key in ("status", "renderedHtml", "categoryId", "allowComments"):
        data.pop(key, None)
    if page is not None and page.status == "published":
        data.pop("slug", None)
    if contains_html_widget([data.get(k) for k in ("blocks", "headerOverride", "footerOverride")]):
        return None, JsonResponse({"error": "افزودن کد HTML سفارشی فقط برای ویراستار و مدیر مجاز است"}, status=403)
    return data, None


def apply_fields_from_payload(page, data):
    """Shared by create/update -- pulls every editable field out of the JSON body."""
    simple_map = {
        "title": "title", "status": "status",
        "seoTitle": "seo_title", "metaDescription": "meta_description",
        "metaKeywords": "meta_keywords", "ogImage": "og_image", "robots": "robots",
        "schemaType": "schema_type", "schemaJson": "schema_json",
        "renderedHtml": "rendered_html",
    }
    for key, field in simple_map.items():
        if key in data and data[key] is not None:
            setattr(page, field, data[key])

    if "categoryId" in data:
        page.category = PageCategory.objects.filter(pk=data["categoryId"]).first() if data["categoryId"] else None
    if "allowComments" in data:
        page.allow_comments = bool(data["allowComments"])

    if page.status not in ("draft", "published"):
        page.status = "draft"

    if any(k in data for k in ("blocks", "headerOverride", "footerOverride")):
        content = page.content_json or {}
        for key in ("blocks", "headerOverride", "footerOverride"):
            if key in data:
                content[key] = data[key]
        page.content_json = content


def slug_error_response(path, err, exclude_pk=None):
    taken = "قبلاً" in err
    return JsonResponse({"error": err, "suggestion": suggest_path(path, exclude_pk) if taken and path else None},
                        status=409 if taken else 400)


# ------------------------------------------------------------------ builder shell
@ensure_csrf_cookie
def builder_shell(request):
    """
    Serves the static page-builder.html. Two important things happen here:
      1. @ensure_csrf_cookie guarantees the `csrftoken` cookie exists, so the JS
         fetch() calls (POST/PUT/DELETE) can send it back as X-CSRFToken.
      2. Only active staff can open the builder at all.
    The file is returned as-is (NOT rendered with the Django template engine, because it
    contains JS template literals that Django's {{ }} syntax would choke on).
    """
    u = request.user
    if not (u.is_authenticated and u.is_active and u.is_staff):
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    html_file = Path(settings.BASE_DIR) / "builder_static" / "page-builder.html"
    resp = HttpResponse(html_file.read_text(encoding="utf-8"))
    resp["X-Frame-Options"] = "SAMEORIGIN"
    resp["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------ JSON API (role based)
@api_role("author")
@require_http_methods(["GET"])
def list_pages(request):
    qs = BuilderPage.objects.all()
    if not _is_privileged(request.user):               # authors only ever see their own pages
        qs = qs.filter(created_by=request.user)
    return JsonResponse({"results": [page_to_dict(p, full=False) for p in qs]})


@api_role("author")
@csrf_protect
@require_http_methods(["POST"])
def create_page(request):
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("invalid JSON")
    if not isinstance(data, dict):
        return HttpResponseBadRequest("invalid JSON")
    data, err_resp = clean_payload(request.user, None, data)
    if err_resp:
        return err_resp

    page = BuilderPage(title=str(data.get("title") or "صفحه جدید")[:200], created_by=request.user)
    apply_fields_from_payload(page, data)
    if not _is_privileged(request.user):
        page.status = "draft"
    wanted = data.get("slug")
    if wanted:                                  # the user chose an address -> it must be valid and free, never silently altered
        path = normalize_path(wanted)
        err = validate_path(path)
        if err:
            return slug_error_response(path, err)
        page.slug = path
    else:                                       # no address given -> generate a unique one from the title
        page.slug = unique_path(page.title)
    try:
        with transaction.atomic():
            page.save()
    except IntegrityError:                      # two requests raced for the same address
        return JsonResponse({"error": "این آدرس همین الان برای صفحهٔ دیگری ثبت شد؛ آدرس دیگری انتخاب کنید"}, status=409)
    SlugRedirect.objects.filter(old_path=page.slug).delete()
    return JsonResponse(page_to_dict(page), status=201)


@api_role("author")
@require_http_methods(["GET"])
def get_page(request, pk):
    page = get_object_or_404(BuilderPage, pk=pk)
    if not can_edit(request.user, page):
        return JsonResponse({"error": "این صفحه متعلق به شما نیست"}, status=403)
    return JsonResponse(page_to_dict(page))


@api_role("author")
@csrf_protect
@require_http_methods(["PUT", "PATCH"])
def update_page(request, pk):
    page = get_object_or_404(BuilderPage, pk=pk)
    if not can_edit(request.user, page):
        return JsonResponse({"error": "این صفحه متعلق به شما نیست"}, status=403)
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("invalid JSON")
    if not isinstance(data, dict):
        return HttpResponseBadRequest("invalid JSON")
    data, err_resp = clean_payload(request.user, page, data)
    if err_resp:
        return err_resp

    old_slug = page.slug
    new_slug = normalize_path(data.get("slug")) if data.get("slug") else ""
    if new_slug and new_slug != old_slug:
        err = validate_path(new_slug, exclude_pk=page.pk)
        if err:
            return slug_error_response(new_slug, err, exclude_pk=page.pk)

    apply_fields_from_payload(page, data)
    if new_slug:
        page.slug = new_slug
    try:
        with transaction.atomic():
            page.save()
            if new_slug and new_slug != old_slug:
                SlugRedirect.objects.filter(old_path=new_slug).delete()
                SlugRedirect.objects.update_or_create(old_path=old_slug, defaults={"page": page})   # old link -> 301 to new
    except IntegrityError:
        return JsonResponse({"error": "این آدرس همین الان برای صفحهٔ دیگری ثبت شد؛ آدرس دیگری انتخاب کنید"}, status=409)
    return JsonResponse(page_to_dict(page))


@api_role("author")
@csrf_protect
@require_http_methods(["DELETE"])
def delete_page(request, pk):
    page = get_object_or_404(BuilderPage, pk=pk)
    own_draft = page.created_by_id == request.user.id and page.status == "draft"
    if not (_is_privileged(request.user) or own_draft):
        return JsonResponse({"error": "حذف این صفحه فقط برای ویراستار و مدیر مجاز است"}, status=403)
    page.delete()
    return JsonResponse({"deleted": True})


# ------------------------------------------------------------------ public page (anyone)
public_page_view = _public_page_view
