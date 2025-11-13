from solo.admin import SingletonModelAdmin

from django.contrib import admin

from .models import (
    BigBrotherConfig, Messages, OptMessages1, OptMessages2, OptMessages3, OptMessages4,
    OptMessages5, UserStatus, WarmProgress, PapsConfig
)
from .modelss import (
    TicketToolConfig,
    PapCompliance,
    LeaveRequest,
    ComplianceTicket,
    BigBrotherRedditSettings,
    BigBrotherRedditMessage,
)
from .reddit import is_reddit_module_visible
from django.core.exceptions import ObjectDoesNotExist


class DLCVisibilityMixin:
    """Hide admin entries when the related DLC flag is disabled."""

    dlc_attr = None

    def _allowed(self) -> bool:
        if not self.dlc_attr:
            return True
        try:
            cfg = BigBrotherConfig.get_solo()
        except ObjectDoesNotExist:
            return False
        return bool(getattr(cfg, self.dlc_attr, False))

    def has_module_permission(self, request):
        return self._allowed() and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._allowed() and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return self._allowed() and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._allowed() and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._allowed() and super().has_delete_permission(request, obj)


class PapModuleVisibilityMixin(DLCVisibilityMixin):
    dlc_attr = "dlc_pap_active"


class TicketModuleVisibilityMixin(DLCVisibilityMixin):
    dlc_attr = "dlc_tickets_active"


class LoaModuleVisibilityMixin(DLCVisibilityMixin):
    dlc_attr = "dlc_loa_active"


class DailyMessagesVisibilityMixin(DLCVisibilityMixin):
    dlc_attr = "dlc_daily_messages_active"


@admin.register(BigBrotherConfig)
#class BB_ConfigAdmin(SingletonModelAdmin):
class BB_ConfigAdmin(SingletonModelAdmin):
    readonly_fields = (
        'main_corporation',
        'main_alliance',
        'main_corporation_id',
        'main_alliance_id',
        'is_active',
        'dlc_corp_brother_active',
        'dlc_loa_active',
        'dlc_pap_active',
        'dlc_tickets_active',
        'dlc_reddit_active',
        'dlc_daily_messages_active',
    )
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
class PapsConfigAdmin(PapModuleVisibilityMixin, SingletonModelAdmin):
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
class TicketToolConfigAdmin(TicketModuleVisibilityMixin, SingletonModelAdmin):
    filter_horizontal = (
        "excluded_users",
    )
    def has_add_permission(self, request):
        # Prevent adding new config if one already exists
        if PapsConfig.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        # Prevent deleting the singleton instance
        return True


class RedditAdminVisibilityMixin(DLCVisibilityMixin):
    dlc_attr = "dlc_reddit_active"

    def _allowed(self) -> bool:
        return super()._allowed() and is_reddit_module_visible()


@admin.register(BigBrotherRedditSettings)
class BigBrotherRedditSettingsAdmin(RedditAdminVisibilityMixin, SingletonModelAdmin):
    exclude = (
        "reddit_access_token",
        "reddit_refresh_token",
        "reddit_token_type",
        "last_submission_id",
        "last_submission_permalink",
        "reddit_account_name",
    )
    readonly_fields = ("reddit_token_obtained", "last_submission_at", "last_reply_checked_at", "reddit_account_name")

    def has_add_permission(self, request):
        if BigBrotherRedditSettings.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BigBrotherRedditMessage)
class BigBrotherRedditMessageAdmin(RedditAdminVisibilityMixin, admin.ModelAdmin):
    list_display = ("title", "used_in_cycle", "created")
    list_filter = ("used_in_cycle",)
    search_fields = ("title", "content")
    
@admin.register(Messages)
class DailyMessageConfig(DailyMessagesVisibilityMixin, admin.ModelAdmin):
    search_fields = ['text']
    list_display = ['text', 'sent_in_cycle']
    
@admin.register(OptMessages1)
class OptMessage1Config(DailyMessagesVisibilityMixin, admin.ModelAdmin):
    search_fields = ['text']
    list_display = ['text', 'sent_in_cycle']
    
@admin.register(OptMessages2)
class OptMessage2Config(DailyMessagesVisibilityMixin, admin.ModelAdmin):
    search_fields = ['text']
    list_display = ['text', 'sent_in_cycle']
    
@admin.register(OptMessages3)
class OptMessage3Config(DailyMessagesVisibilityMixin, admin.ModelAdmin):
    search_fields = ['text']
    list_display = ['text', 'sent_in_cycle']
    
@admin.register(OptMessages4)
class OptMessage4Config(DailyMessagesVisibilityMixin, admin.ModelAdmin):
    search_fields = ['text']
    list_display = ['text', 'sent_in_cycle']
    
@admin.register(OptMessages5)
class OptMessage5Config(DailyMessagesVisibilityMixin, admin.ModelAdmin):
    search_fields = ['text']
    list_display = ['text', 'sent_in_cycle']

@admin.register(WarmProgress)
class WarmProgressConfig(admin.ModelAdmin):
    list_display = ['user_main', 'updated']

@admin.register(UserStatus)
class UserStatusConfig(admin.ModelAdmin):
    list_display = ['user', 'updated']

@admin.register(ComplianceTicket)
class ComplianceTicketConfig(TicketModuleVisibilityMixin, admin.ModelAdmin):
    list_display = ['user', 'ticket_id', 'reason']

@admin.register(LeaveRequest)
class LeaveRequestConfig(LoaModuleVisibilityMixin, admin.ModelAdmin):
    list_display = ['main_character', 'start_date', 'end_date', 'reason', 'status']
    
@admin.register(PapCompliance)
class PapComplianceConfig(PapModuleVisibilityMixin, admin.ModelAdmin):
    search_fields = ['user_profile']
    list_display = ['user_profile', 'pap_compliant']
