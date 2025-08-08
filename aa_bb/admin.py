from solo.admin import SingletonModelAdmin

from django.contrib import admin

from allianceauth.services.hooks import get_extension_logger

from .models import (
    BigBrotherConfig,Messages,OptMessages1,OptMessages2,OptMessages3,OptMessages4,OptMessages5,
    UserStatus,LeaveRequest,
)

@admin.register(BigBrotherConfig)
#class BB_ConfigAdmin(SingletonModelAdmin):
class BB_ConfigAdmin(SingletonModelAdmin):
    readonly_fields = ('main_corporation','main_alliance', 'main_corporation_id','main_alliance_id', 'is_active',)
    filter_horizontal = (
        "pingrole1_messages",
        "pingrole2_messages",
        "here_messages",
        "everyone_messages",
    )
    
    def has_add_permission(self, request):
        # Prevent adding new config if one already exists
        if BigBrotherConfig.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        # Prevent deleting the singleton instance
        return True
    
@admin.register(Messages)
class DailyMessageConfig(admin.ModelAdmin):
    search_fields = ['text']
    list_display = ['text', 'sent_in_cycle']
    
@admin.register(OptMessages1)
class OptMessage1Config(admin.ModelAdmin):
    search_fields = ['text']
    list_display = ['text', 'sent_in_cycle']
    
@admin.register(OptMessages2)
class OptMessage2Config(admin.ModelAdmin):
    search_fields = ['text']
    list_display = ['text', 'sent_in_cycle']
    
@admin.register(OptMessages3)
class OptMessage3Config(admin.ModelAdmin):
    search_fields = ['text']
    list_display = ['text', 'sent_in_cycle']
    
@admin.register(OptMessages4)
class OptMessage4Config(admin.ModelAdmin):
    search_fields = ['text']
    list_display = ['text', 'sent_in_cycle']
    
@admin.register(OptMessages5)
class OptMessage5Config(admin.ModelAdmin):
    search_fields = ['text']
    list_display = ['text', 'sent_in_cycle']

@admin.register(UserStatus)
class UserStatusConfig(admin.ModelAdmin):
    list_display = ['user', 'updated']

@admin.register(LeaveRequest)
class LeaveRequestConfig(admin.ModelAdmin):
    list_display = ['main_character', 'start_date', 'end_date', 'reason', 'status']