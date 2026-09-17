from django.conf import settings

R = settings.REDIS_CLIENT

def user_format(id):
    return f"USER:{id}"

def cache_user(user_id, phone_number, is_superuser, is_staff, full_name=None):
    R.hset(
        f'{user_format(user_id)}',
        mapping = {
            'full_name': full_name,
            "phone_number":phone_number,
            'is_superuser':is_superuser,
            'is_staff':is_staff
        }
    )
    
def get_cache_user(id):
    return R.hgetall(user_format(id))

def delete_cache_user(id):
    return R.delete(user_format(id))