from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin


class CustumerManger(BaseUserManager):
    
    def create_user(self, phone_number, password=None, **extra_fields):
        
        if not phone_number:
            raise ValueError('شماره موبایل الزامی است!')
           
        extra_fields["is_staff"] = False
        extra_fields["is_superuser"] = False
        extra_fields.setdefault("is_active", True)   
        
        new_user = self.model(
            phone_number =phone_number,
            **extra_fields
        )
                
        if password:
            new_user.set_password(password)
        else:
            new_user.set_unusable_password()
        
        
        new_user.save()
        
        return new_user
    
    
    def create_staffuser(self, phone_number, password, **extra_fields):
        if not phone_number:
            raise ValueError('شماره موبایل الزامی است!')
        
        extra_fields["is_superuser"] = False
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_active", True)
        
        if extra_fields.get("is_staff") is not True:
            raise ValueError("staffuser must have is_staff=True")
        
        new_staff = self.model(
            phone_number=phone_number,
            **extra_fields
        )
        
        if password:
            new_staff.set_password(password)
        else:
            new_staff.set_unusable_password()
                
        new_staff.save()
        
        return new_staff
        
    
    def create_superuser(self, phone_number, password, **extra_fields):
        if not phone_number:
            raise ValueError('شماره موبایل الزامی است!')
        
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")

        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")
        
        new_superuser = self.model(
            phone_number=phone_number,
            **extra_fields
        )
        
        new_superuser.set_password(password)
        new_superuser.save()
        
        return new_superuser
        

class User(AbstractBaseUser, PermissionsMixin):
    first_name = models.CharField(max_length=40)
    last_name = models.CharField(max_length=40)
    avatar = models.ImageField(upload_to='Client/Avatar', null=True, blank=True)
    
    phone_number = models.CharField(max_length=11, unique=True)
    
    is_superuser = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    REQUIRED_FIELDS = ['first_name']
    USERNAME_FIELD = 'phone_number'
    
    objects = CustumerManger()
    
    def get_fullName(self):
        return f'{self.first_name} {self.last_name}'
    
    def __str__(self):
        return f"{self.phone_number} | is_active: {self.is_active}" 
    
    