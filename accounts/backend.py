from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend

User = get_user_model()


class MobileBackend(BaseBackend):
    
    def authenticate(self, request, phone_number=None, password=None, otp=None, is_active=None, **kwargs):
        if not phone_number:
            return None

        try:
            user = User.objects.get(phone_number=phone_number,)
        except User.DoesNotExist:
            return None

        # --------------------
        # Password Login
        # --------------------
        if password is not None:
            if user.check_password(password) and user.is_active:
                return user

            return None

        # --------------------
        # OTP Login
        # --------------------
        if otp is not None:
            # TODO:
            # otp را از Redis بخوان
            # بررسی کن معتبر است
            # expire را بررسی کن
            # و ...

            return None

        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
                