from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from panel_admin import urls as p_d

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('accounts.urls')),
    path("api/comments/", include(p_d.comments_urlpatterns)),
    path("api/pages/",    include(p_d.urlpatterns)),   
    path("", p_d.home_view_pattern),                           # صفحهٔ اصلی سایت
    path("p/<str:slug>/", p_d.public_page_pattern),

]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)