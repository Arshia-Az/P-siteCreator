from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend

User = get_user_model()


class MobileBackend(BaseBackend):
    
    def authenticate(self, request, phone_number, otp=None, password=None, **kwargs):
        if bool(otp) != bool(password):
            
            try:
                user = User.objects.get(phone_number=phone_number)
            except:
                return None
            
            if otp:
                # todo: check otp code from the redis
                if False:
                    return None
                return User
            
            elif password:
                if user.check_password(password):
                    return user
                return None
            else:
                raise ValueError('باید یکی از حالت های شناسایی پرشود !')
                