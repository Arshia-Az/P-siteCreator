from django.urls import path

from . import api, public, views

urlpatterns = [
    path('panel-admin/', views.PanelView.as_view(), name='admin_panel'),
    # pages (existing)
    path("", views.list_pages),
    path("create/", views.create_page),
    path("slug-check/", api.slug_check),
    path("<int:pk>/", views.get_page),
    path("<int:pk>/update/", views.update_page),
    path("<int:pk>/delete/", views.delete_page),
    # categories
    path("categories/", api.categories_list),
    path("categories/create/", api.category_create),
    path("categories/assign/", api.category_assign),
    path("categories/reorder/", api.category_reorder),
    path("categories/<int:pk>/update/", api.category_update),
    path("categories/<int:pk>/delete/", api.category_delete),
    # media
    path("media/", api.media_list),
    path("media/upload/", api.media_upload),
    path("media/bulk/", api.media_bulk),
    path("media/groups/", api.media_groups),
    path("media/groups/create/", api.media_group_create),
    path("media/groups/<int:pk>/update/", api.media_group_update),
    path("media/groups/<int:pk>/delete/", api.media_group_delete),
    path("media/<int:pk>/update/", api.media_update),
    path("media/<int:pk>/delete/", api.media_delete),
    # comments (moderation)
    path("comments/", api.comments_list),
    path("comments/unread-count/", api.comments_unread_count),
    path("comments/bulk/", api.comments_bulk),
    path("comments/<int:pk>/action/", api.comment_action),
    path("comments/<int:pk>/delete/", api.comment_delete),
    # users + own profile
    path("users/", api.users_list),
    path("users/create/", api.user_create),
    path("users/<int:pk>/update/", api.user_update),
    path("users/<int:pk>/delete/", api.user_delete),
    path("me/", api.me),
    path("me/avatar/", api.me_avatar),
    # site-wide settings and header/footer template library
    path("site/", api.site_get),
    path("site/update/", api.site_put),
    path("templates/", api.templates_list),
    path("templates/create/", api.template_create),
    path("templates/<int:pk>/", api.template_get),
    path("templates/<int:pk>/delete/", api.template_delete),
]

comments_urlpatterns = [
    path("widget.js", public.widget_js),
    path("submit/", public.comment_submit),
    path("captcha/", public.captcha_new),
    path("page/<int:pk>/", public.comments_public),
]

home_view_pattern = public.home_view
public_page_pattern = public.public_page_view
