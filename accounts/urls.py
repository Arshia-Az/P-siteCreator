from django.urls import path 
from .views import (
    AdminLogin,
)


urlpatterns = [
    path('admin-login/', AdminLogin.as_view(), name='admin_log'),
]
