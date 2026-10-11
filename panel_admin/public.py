import base64
import hashlib
import hmac
import re
import secrets
import time
from io import BytesIO

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404, HttpResponse, HttpResponsePermanentRedirect, JsonResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .models import BuilderPage, PageComment, SiteSettings, SlugRedirect
from .paths import normalize_path
from .permissions import jbody

HEADER_RE = re.compile(r"<!--PB:HEADER-->.*?<!--/PB:HEADER-->", re.S)
FOOTER_RE = re.compile(r"<!--PB:FOOTER-->.*?<!--/PB:FOOTER-->", re.S)
COLORS_RE = re.compile(r"/\*PB:COLORS\*/.*?/\*/PB:COLORS\*/", re.S)
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def build_colors_css(c):
    """Site-wide colour CSS, built ONLY from validated fields (hex colours / small integers) -- never from raw text.
    Mirrors buildColorsCss() in the builder so the preview matches what visitors get."""
    hx = lambda k: c.get(k) if isinstance(c.get(k), str) and HEX.match(c.get(k)) else None
    root, rules = [], []
    if hx("accent") and hx("contrast"):
        root += [f"--site-accent:{c['accent']}", f"--site-accent-contrast:{c['contrast']}"]
    if c.get("btnUnify"):
        r = c.get("btnRadius")
        r = int(r) if isinstance(r, (int, float)) and not isinstance(r, bool) and 0 <= r <= 40 else 8
        bb, bc = hx("btnBg") or "#1c1f26", hx("btnColor") or "#ffffff"
        root += [f"--btn-bg:{bb}", f"--btn-color:{bc}", f"--btn-radius:{r}px"]
        rules.append(".pb-gbtn{background:var(--btn-bg) !important;color:var(--btn-color) !important;border-radius:var(--btn-radius) !important;}")
    if c.get("textOn"):
        if hx("textColor"):
            rules.append(f".pb-main{{color:{c['textColor']};}}")
        if hx("headingColor"):
            rules.append(f".pb-main h1,.pb-main h2,.pb-main h3,.pb-main h4,.pb-main h5,.pb-main h6{{color:{c['headingColor']};}}")
        if hx("linkColor"):
            rules.append(f".pb-main a:not(.pb-gbtn){{color:{c['linkColor']};}}")
    out = (":root{" + ";".join(root) + ";}" if root else "") + "".join(rules)
    return out


def compose_public_html(page):
    """
    Stored pages carry marker comments around the parts that are site-wide:
        <!--PB:HEADER-->...<!--/PB:HEADER-->   <!--PB:FOOTER-->...<!--/PB:FOOTER-->
        /*PB:COLORS*/ :root{...} /*/PB:COLORS*/
    At serve time the CURRENT global values replace them, so a change in Appearance reaches every
    published page immediately -- without re-saving each one. Pages that define their own header/footer
    override are left alone for that part.
    """
    html = (page.rendered_html or "").replace("__PB_ID__", str(page.pk))
    s = SiteSettings.load()
    own = page.content_json or {}
    for key, rx, override in (("header_json", HEADER_RE, "headerOverride"), ("footer_json", FOOTER_RE, "footerOverride")):
        data = getattr(s, key)
        if data and data.get("html") and not own.get(override):
            tag = "HEADER" if key == "header_json" else "FOOTER"
            html = rx.sub(lambda m: f"<!--PB:{tag}-->{data['html']}<!--/PB:{tag}-->", html, count=1)
    css = build_colors_css(s.colors or {})
    if css:
        html = COLORS_RE.sub(lambda m: f"/*PB:COLORS*/{css}/*/PB:COLORS*/", html, count=1)
    return html


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "SAMEORIGIN",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _html(content, status=200):
    resp = HttpResponse(content, status=status)
    for k, v in SECURITY_HEADERS.items():
        resp.setdefault(k, v)
    return resp


def _serve(page):
    return _html(compose_public_html(page) or "<p>این صفحه هنوز محتوایی ندارد.</p>")


def _public_url(path):
    prefix = getattr(settings, "BUILDER_PUBLIC_PREFIX", "").strip("/")      # default: pages live at the site root
    return "/" + (prefix + "/" if prefix else "") + path + "/"


@require_GET
def public_page_view(request, slug):
    path = normalize_path(slug)
    page = BuilderPage.objects.filter(slug=path, status="published").first()
    if page:
        if slug.strip("/") != path:                    # canonical form (case / spaces) -> one URL per page
            return HttpResponsePermanentRedirect(_public_url(path))
        return _serve(page)
    r = SlugRedirect.objects.select_related("page").filter(old_path=path, page__status="published").first()
    if r:                                              # the page was moved -> permanent redirect keeps SEO and old links alive
        return HttpResponsePermanentRedirect(_public_url(r.page.slug))
    raise Http404("صفحه پیدا نشد")


@require_GET
def home_view(request):
    """The site root (what visitors see when they type the bare domain)."""
    page = SiteSettings.load().home_page
    if page and page.status == "published":
        return _serve(page)
    return _html("<!doctype html><meta charset='utf-8'><title>به‌زودی</title><p style='font-family:sans-serif;text-align:center;margin-top:20vh'>سایت به‌زودی راه‌اندازی می‌شود.</p>")


# ------------------------------------------------------------ public comments
def _client_ip(request):
    # Behind a reverse proxy set BUILDER_TRUST_PROXY=True and make sure the proxy overwrites X-Forwarded-For.
    if getattr(settings, "BUILDER_TRUST_PROXY", False):
        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if xff:
            return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


@require_GET
def comments_public(request, pk):
    page = BuilderPage.objects.filter(pk=pk, status="published").first()
    if not page or not page.allow_comments:
        return JsonResponse({"enabled": False, "results": []})
    top = (PageComment.objects.filter(page=page, parent__isnull=True, status="approved")
           .prefetch_related("replies").order_by("-created_at")[:100])
    out = []
    for c in top:
        out.append({"name": c.author_name, "body": c.body, "date": c.created_at.isoformat(),
                    "replies": [{"name": r.author_name, "body": r.body, "date": r.created_at.isoformat(), "isAdmin": r.is_admin_reply}
                                for r in c.replies.filter(status="approved").order_by("created_at")]})
    return JsonResponse({"enabled": True, "results": out})


# ---- self-hosted image captcha (no third-party service, works in Iran) ----
CAPTCHA_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"        # no 0/O/1/I/L confusion
_rng = secrets.SystemRandom()


def _captcha_hash(code, nonce):
    return hashlib.sha256(f"{code.strip().upper()}|{nonce}|{settings.SECRET_KEY}".encode()).hexdigest()


def _captcha_png(code):
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    W, H = 160, 56
    img = Image.new("RGB", (W, H), (245, 246, 248))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=30)
    except TypeError:
        font = ImageFont.load_default()
    for _ in range(6):
        g = _rng.randint(120, 200)
        d.line([(_rng.randint(0, W), _rng.randint(0, H)), (_rng.randint(0, W), _rng.randint(0, H))], fill=(g, g, g), width=_rng.randint(1, 2))
    x = 12
    for ch in code:
        tile = Image.new("RGBA", (40, 46), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((6, 4), ch, font=font, fill=(_rng.randint(20, 90), _rng.randint(20, 90), _rng.randint(20, 90), 255))
        tile = tile.rotate(_rng.randint(-25, 25), expand=True, resample=Image.BICUBIC)
        img.paste(tile, (x, _rng.randint(0, 8)), tile)
        x += 28
    for _ in range(140):
        g = _rng.randint(90, 200)
        d.point((_rng.randint(0, W - 1), _rng.randint(0, H - 1)), fill=(g, g, g))
    img = img.filter(ImageFilter.SMOOTH)
    buf = BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def make_captcha(code=None):
    code = code or "".join(_rng.choice(CAPTCHA_CHARS) for _ in range(5))
    nonce = secrets.token_hex(8)
    token = signing.dumps({"n": nonce, "h": _captcha_hash(code, nonce), "t": time.time()}, salt="pb-captcha")
    return token, _captcha_png(code)


def check_captcha(token, answer):
    """Single use, expires after 10 minutes, and a human needs at least 2 s to fill the form."""
    try:
        d = signing.loads(str(token or ""), salt="pb-captcha", max_age=600)
    except signing.BadSignature:
        return False
    key = "pbcu:" + d["n"]
    if not cache.add(key, 1, 700):                      # already used -> replay
        return False
    age = time.time() - d["t"]
    if age < 2 or age > 600:
        return False
    return hmac.compare_digest(d["h"], _captcha_hash(str(answer or ""), d["n"]))


@require_GET
def captcha_new(request):
    key = "pbcap:" + hashlib.sha256((_client_ip(request) + settings.SECRET_KEY).encode()).hexdigest()[:24]
    cache.add(key, 0, 600)
    try:
        if cache.incr(key) > 40:                       # Pillow rendering is not free -> cap per IP
            return JsonResponse({"error": "درخواست‌ها بیش از حد مجاز است"}, status=429)
    except ValueError:
        pass
    token, png = make_captcha()
    resp = JsonResponse({"token": token, "image": png})
    resp["Cache-Control"] = "no-store"
    return resp


# Anonymous visitors post here. CSRF is intentionally not required: the endpoint only creates a *pending*
# comment (no session-bound side effects) and is protected by captcha, a honeypot, strict validation and a
# per-IP rate limit instead -- which also keeps it working on fully cached public pages.
@csrf_exempt
@require_http_methods(["POST"])
def comment_submit(request):
    d = jbody(request)
    if d is None:
        return JsonResponse({"error": "invalid JSON"}, status=400)
    if d.get("website"):                                   # honeypot: real users never fill this field
        return JsonResponse({"ok": True})
    ip = _client_ip(request)
    key = "pbc:" + hashlib.sha256((ip + settings.SECRET_KEY).encode()).hexdigest()[:24]
    cache.add(key, 0, 600)
    try:
        if cache.incr(key) > 5:
            return JsonResponse({"error": "تعداد ارسال بیش از حد مجاز است؛ چند دقیقه بعد دوباره تلاش کنید."}, status=429)
    except ValueError:
        pass
    if not check_captcha(d.get("captchaToken"), d.get("captchaAnswer")):
        return JsonResponse({"error": "کد امنیتی اشتباه یا منقضی شده است.", "captcha": True}, status=400)
    try:
        pid = int(d.get("pid"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "صفحه نامعتبر است."}, status=400)
    page = BuilderPage.objects.filter(pk=pid, status="published", allow_comments=True).first()
    if not page:
        return JsonResponse({"error": "امکان ثبت نظر برای این صفحه وجود ندارد."}, status=404)
    name, body, email = (str(d.get("name") or "").strip(), str(d.get("body") or "").strip(), str(d.get("email") or "").strip())
    if not (2 <= len(name) <= 60):
        return JsonResponse({"error": "نام باید بین ۲ تا ۶۰ حرف باشد."}, status=400)
    if not (3 <= len(body) <= 2000):
        return JsonResponse({"error": "متن نظر باید بین ۳ تا ۲۰۰۰ حرف باشد."}, status=400)
    if email:
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({"error": "ایمیل نامعتبر است."}, status=400)
    PageComment.objects.create(page=page, author_name=name, author_email=email[:254], body=body,
                               ip_hash=hashlib.sha256(ip.encode()).hexdigest(), status="pending", is_read=False)
    return JsonResponse({"ok": True, "message": "نظر شما ثبت شد و پس از تأیید مدیر نمایش داده می‌شود."}, status=201)


WIDGET_JS = r"""(function(){
if(window.__pbcLoaded)return;window.__pbcLoaded=1;
var CSS='.pb-comments{direction:rtl;font-family:inherit;max-width:760px;margin:32px auto;padding:0 16px;color:inherit}.pbc-title{font-size:20px;margin:0 0 16px}.pbc-item{border:1px solid rgba(128,128,128,.25);border-radius:12px;padding:14px 16px;margin-bottom:12px;background:rgba(128,128,128,.05)}.pbc-meta{display:flex;gap:10px;align-items:center;font-size:12.5px;opacity:.85;margin-bottom:6px}.pbc-name{font-weight:700}.pbc-body{line-height:1.9;white-space:pre-wrap;word-break:break-word}.pbc-reply{margin:10px 22px 0 0;padding:10px 14px;border-right:3px solid var(--site-accent,#f2a63a);background:rgba(128,128,128,.08);border-radius:8px}.pbc-badge{background:var(--site-accent,#f2a63a);color:var(--site-accent-contrast,#1b1305);font-size:10.5px;padding:1px 8px;border-radius:20px}.pbc-form{display:grid;gap:10px;margin-top:20px;position:relative}.pbc-form input,.pbc-form textarea{font:inherit;padding:10px 12px;border:1px solid rgba(128,128,128,.4);border-radius:10px;background:transparent;color:inherit;width:100%;box-sizing:border-box}.pbc-form button{font:inherit;font-weight:700;padding:11px;border:0;border-radius:10px;cursor:pointer;background:var(--site-accent,#f2a63a);color:var(--site-accent-contrast,#1b1305)}.pbc-form button[disabled]{opacity:.6;cursor:wait}.pbc-hp{position:absolute;left:-9999px;opacity:0;height:0;width:0}.pbc-msg{font-size:13px;min-height:18px}.pbc-empty{opacity:.7}.pbc-cap{display:flex;gap:8px;align-items:center}.pbc-cap img{height:44px;border-radius:8px;border:1px solid rgba(128,128,128,.3);background:#f5f6f8}.pbc-cap .pbc-re{flex:none;width:auto;padding:8px 12px;background:transparent;color:inherit;border:1px solid rgba(128,128,128,.4)}.pbc-cap input{direction:ltr;text-align:center;letter-spacing:3px;text-transform:uppercase}.pbc-note{font-size:12px;opacity:.7}';
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function fa(d){try{return new Date(d).toLocaleDateString('fa-IR')}catch(e){return ''}}
var st=document.createElement('style');st.textContent=CSS;document.head.appendChild(st);
function init(box){
  var pid=box.getAttribute('data-pid');box.textContent='';
  if(!pid||pid==='__PB_ID__'){var n=el('div','pbc-note','بخش نظرات پس از ذخیره و انتشار صفحه روی سرور فعال می‌شود.');box.append(n);return}
  var list=el('div','pbc-list'),form=el('form','pbc-form'),msg=el('div','pbc-msg'),token='';
  var name=el('input');name.placeholder='نام شما';name.maxLength=60;name.required=true;
  var mail=el('input');mail.type='email';mail.placeholder='ایمیل (اختیاری، نمایش داده نمی‌شود)';mail.maxLength=254;
  var body=el('textarea');body.placeholder='نظر شما...';body.rows=4;body.maxLength=2000;body.required=true;
  var hp=el('input','pbc-hp');hp.name='website';hp.tabIndex=-1;hp.autocomplete='off';
  var cap=el('div','pbc-cap'),img=el('img');img.alt='کد امنیتی';
  var ans=el('input');ans.placeholder='کد تصویر';ans.maxLength=8;ans.required=true;ans.autocomplete='off';
  var re=el('button','pbc-re','↻');re.type='button';re.title='تصویر جدید';
  cap.append(img,ans,re);
  var btn=el('button',null,'ارسال نظر');btn.type='submit';
  form.append(name,mail,body,cap,hp,btn,msg);box.append(el('h3','pbc-title','نظرات کاربران'),list,form);
  function newCap(){ans.value='';fetch('/api/comments/captcha/',{cache:'no-store'}).then(function(r){return r.json()}).then(function(j){token=j.token;img.src=j.image}).catch(function(){msg.textContent='بارگذاری کد امنیتی ناموفق بود'})}
  re.addEventListener('click',newCap);
  function render(r){
    list.textContent='';
    if(!r.enabled){box.style.display='none';return}
    if(!r.results.length){list.append(el('div','pbc-empty','هنوز نظری ثبت نشده است. اولین نفر باشید!'));return}
    r.results.forEach(function(c){
      var it=el('div','pbc-item'),m=el('div','pbc-meta');m.append(el('span','pbc-name',c.name),el('span',null,fa(c.date)));
      it.append(m,el('div','pbc-body',c.body));
      c.replies.forEach(function(x){var rp=el('div','pbc-reply'),rm=el('div','pbc-meta');rm.append(el('span','pbc-name',x.name));if(x.isAdmin)rm.append(el('span','pbc-badge','پاسخ مدیر'));rm.append(el('span',null,fa(x.date)));rp.append(rm,el('div','pbc-body',x.body));it.append(rp)});
      list.append(it)})}
  function load(){fetch('/api/comments/page/'+encodeURIComponent(pid)+'/').then(function(r){return r.json()}).then(render).catch(function(){list.append(el('div','pbc-empty','بارگذاری نظرات ناموفق بود.'))})}
  form.addEventListener('submit',function(e){e.preventDefault();btn.disabled=true;msg.textContent='';
    fetch('/api/comments/submit/',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pid:pid,name:name.value,email:mail.value,body:body.value,website:hp.value,captchaToken:token,captchaAnswer:ans.value})})
    .then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j}})})
    .then(function(x){msg.textContent=x.ok?(x.j.message||'ثبت شد'):(x.j.error||'خطا در ارسال');if(x.ok){body.value=''}if(x.ok||x.j.captcha)newCap()})
    .catch(function(){msg.textContent='خطا در ارتباط با سرور'}).then(function(){btn.disabled=false})});
  newCap();load()}
document.querySelectorAll('.pb-comments').forEach(init);
})();"""


@require_GET
def widget_js(request):
    resp = HttpResponse(WIDGET_JS, content_type="application/javascript; charset=utf-8")
    resp["Cache-Control"] = "public, max-age=3600"
    return resp
