from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class PageCreator(models.Model):
    
    class StatusType(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        PUBLISHED = "published", "منتشرشده"
        UNPUBLISHED = "unpublished", "منتشرنشده"  
    
    title = models.CharField(max_length=300, db_index=True)    
    status = models.CharField(max_length=20, choices=StatusType.choices, default=StatusType.DRAFT, db_index=True)
    
    html_path = models.CharField(max_length=300, db_index=True)
    slug = models.SlugField(unique=True)
    schema_code = models.JSONField()
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_pages")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class MetaTag(models.Model):
    page = models.ForeignKey(PageCreator, on_delete=models.CASCADE, related_name="meta_tags")
    key = models.CharField(max_length=150)
    value = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)    
    
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['page', 'key'],
                name="unique_meta_key_per_page",
            )
        ]
