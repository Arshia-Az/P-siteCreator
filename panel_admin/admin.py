from django.contrib import admin

from .models import (BuilderPage, LayoutTemplate, MediaAsset, MediaGroup, PageCategory, PageComment, SiteSettings)

admin.site.register([PageCategory, MediaGroup, SiteSettings, LayoutTemplate])


@admin.register(BuilderPage)
class BuilderPageAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "status", "category", "updated_at")
    list_filter = ("status", "category")
    search_fields = ("title", "slug")
    exclude = ("rendered_html",)


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = ("title", "group", "width", "height", "size_bytes", "created_at")
    list_filter = ("group",)


@admin.register(PageComment)
class PageCommentAdmin(admin.ModelAdmin):
    list_display = ("author_name", "page", "status", "is_read", "created_at")
    list_filter = ("status", "is_read")
    search_fields = ("author_name", "body")
