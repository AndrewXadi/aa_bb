from solo.admin import SingletonModelAdmin

from django.contrib import admin

from allianceauth.services.hooks import get_extension_logger

from .models import (
    BigBrotherConfig,Messages,OptMessages1,OptMessages2,OptMessages3,OptMessages4,OptMessages5,
    UserStatus,LeaveRequest,WarmProgress,PapsConfig,ComplianceTicket,TicketToolConfig
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
        "bb_guest_states",
        "bb_member_states",
    )
    
    def has_add_permission(self, request):
        # Prevent adding new config if one already exists
        if BigBrotherConfig.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        # Prevent deleting the singleton instance
        return True

@admin.register(PapsConfig)
class PapsConfigAdmin(SingletonModelAdmin):
    filter_horizontal = (
        "group_paps",
        "excluded_groups",
        "excluded_users",
        "excluded_users_paps",
    )
    def has_add_permission(self, request):
        # Prevent adding new config if one already exists
        if PapsConfig.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        # Prevent deleting the singleton instance
        return True
    
@admin.register(TicketToolConfig)
class TicketToolConfigAdmin(SingletonModelAdmin):
    def has_add_permission(self, request):
        # Prevent adding new config if one already exists
        if PapsConfig.objects.exists():
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

@admin.register(WarmProgress)
class WarmProgressConfig(admin.ModelAdmin):
    list_display = ['user_main', 'updated']

@admin.register(UserStatus)
class UserStatusConfig(admin.ModelAdmin):
    list_display = ['user', 'updated']

@admin.register(ComplianceTicket)
class ComplianceTicketConfig(admin.ModelAdmin):
    list_display = ['user', 'is_resolved', 'reason']

@admin.register(LeaveRequest)
class LeaveRequestConfig(admin.ModelAdmin):
    list_display = ['main_character', 'start_date', 'end_date', 'reason', 'status']