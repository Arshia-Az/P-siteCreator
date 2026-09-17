from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib import messages
from django.views  import View
from django.http import HttpRequest, HttpResponse
from django.contrib.auth import get_user_model
from .redis_service import cache_user, get_cache_user

User = get_user_model()

class AdminLogin(View):
    
    def get(self, request:HttpRequest):
        user = request.user
        if user.is_authenticated:
            if user.is_superuser or user.is_staff:
                return redirect("panel")
            return redirect('/')
        return render(request, 'fa/admin/admin-login.html')
    
    def post(self, request:HttpRequest):
        
        phone_number = request.POST.get('phone_number')
        password = request.POST.get("password")
        
        if phone_number is None:
            messages.error(request, "شماره ای وارد نکرده اید!")
            return redirect('admin_log')
    
        if password is None:
            messages.error(request, "پسورد وارد نشده !!")
            return redirect('admin_log')
        
        user = authenticate(request, phone_number=phone_number, password=password)
            
        #todo: check the user try no more then 10 times with expire time 
        if user is None:
            messages.error(request, 'رمز عبور یا شماره مورد نظر اشتباه است !!!')
            return redirect('admin_log')
        
        if not (user.is_staff or user.is_superuser):
            messages.error(request, 'رمز عبور یا شماره مورد نظر اشتباه است !!!!')
            return redirect('admin_log')
        
        login(request, user)
        
       
        cache_user(
            user_id=user.id,
            full_name=user.get_fullName() or None,
            phone_number=user.phone_number,
            is_staff=int(user.is_staff),
            is_superuser=int(user.is_superuser),
        )
        
        return redirect('panel')
    
def admin_panel_test(request):
    return HttpResponse('HI Welcome To Admin Panel')
            
        
