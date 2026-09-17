from django.urls import path 
from .views import (
    AdminLogin,
    admin_panel_test
)


urlpatterns = [
    path('admin-login/', AdminLogin.as_view(), name='admin_log'),
    path('panel-admin/', admin_panel_test, name='panel')
]
