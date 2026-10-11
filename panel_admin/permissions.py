import json
from functools import wraps

from django.http import JsonResponse

RANK = {"user": 0, "author": 1, "editor": 2, "admin": 3}


def rank_of(u):
    if u.is_superuser:
        return 3
    role = getattr(u, "role", "user")
    if role == "user" and u.is_staff:      # legacy staff accounts created before roles existed
        return 2
    return RANK.get(role, 0)


def api_role(min_role="author"):
    """Staff-only JSON endpoint with a minimum role (author < editor < admin)."""
    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            u = request.user
            if not (u.is_authenticated and u.is_active and u.is_staff):
                return JsonResponse({"error": "unauthorized"}, status=403)
            if rank_of(u) < RANK[min_role]:
                return JsonResponse({"error": "شما دسترسی لازم برای این عملیات را ندارید"}, status=403)
            return view(request, *args, **kwargs)
        return wrapper
    return deco


def jbody(request):
    """Parsed JSON object, or None when the body is not valid JSON."""
    try:
        d = json.loads(request.body or "{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return d if isinstance(d, dict) else None


def contains_html_widget(obj):
    """True when a builder document holds a custom-code ("html") widget/element. Authors may not add those:
    custom code runs on the public site, so it is limited to editors/admins."""
    if isinstance(obj, dict):
        if obj.get("type") == "html":
            return True
        return any(contains_html_widget(v) for v in obj.values())
    if isinstance(obj, list):
        return any(contains_html_widget(v) for v in obj)
    return False


def at_least(user, role):
    return rank_of(user) >= RANK[role]
