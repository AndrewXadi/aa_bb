from solo.admin import SingletonModelAdmin

from django.contrib import admin

from allianceauth.services.hooks import get_extension_logger

from .models import (
    BigBrotherConfig
)

@admin.register(BigBrotherConfig)
#class BB_ConfigAdmin(SingletonModelAdmin):
class BB_ConfigAdmin(SingletonModelAdmin):
    readonly_fields = ('main_corporation','main_alliance', 'main_corporation_id','main_alliance_id', 'is_active',)
    
    def has_add_permission(self, request):
        # Prevent adding new config if one already exists
        if BigBrotherConfig.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        # Prevent deleting the singleton instance
        return True