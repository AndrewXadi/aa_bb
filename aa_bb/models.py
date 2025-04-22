"""
App Models
Create your models in here
"""

# Django
from django.db import models
from django.core.exceptions import ValidationError
from .app_settings import get_main_corp_id
from solo.models import SingletonModel
from django.contrib.auth.models import User
from django.db.models import JSONField



class General(models.Model):
    """Meta model for app permissions"""

    class Meta:
        """Meta definitions"""

        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", "Can access this app"),
            ("full_access", "Can view all main characters"),
            ("recruiter_access", "Can view guest main characters only"),
            )
        
class UserStatus(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    has_awox_kills = models.BooleanField(default=False)
    awox_kill_links = JSONField(default=list, blank=True)
    has_cyno = models.BooleanField(default=False)
    has_hostile_assets = models.BooleanField(default=False)
    hostile_assets = JSONField(default=list, blank=True)
    has_hostile_clones = models.BooleanField(default=False)
    hostile_clones = JSONField(default=list, blank=True)
    has_imp_blacklist = models.BooleanField(default=False)
    has_lawn_blacklist = models.BooleanField(default=False)
    has_game_time_notifications = models.BooleanField(default=False)
    has_skill_injected = models.BooleanField(default=False)
    has_sus_contacts = models.BooleanField(default=False)
    has_sus_contracts = models.BooleanField(default=False)
    has_sus_mails = models.BooleanField(default=False)
    has_sus_trans = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True)


class BigBrotherConfig(SingletonModel):
    token = models.CharField(
        max_length=255,
        blank=False,
        help_text="Input the token you were provided to install this app"
    )
    
    pingroleID = models.CharField(
        max_length=255,
        null=True,
        blank=False,
        default=None,
        help_text="Input the role ID you want pinged when people need to investigate"
    )

    hostile_alliances = models.TextField(
        blank=True,
        null=True,
        help_text="List of alliance IDs considered hostile, separated by ','"
    )

    hostile_corporations = models.TextField(
        blank=True,
        null=True,
        help_text="List of corporation IDs considered hostile, separated by ','"
    )

    webhook = models.URLField(
        blank=True,
        null=True,
        help_text="Discord webhook for sending notifications"
    )

    main_corporation_id = models.BigIntegerField(
        default=0,  # Replace with your actual corp ID
        editable=False,
        help_text="Your Corporation Id"
    )

    main_corporation = models.TextField(
        default=0,  # Replace with your actual corp ID
        editable=False,
        help_text="Your Corporation"
    )

    main_alliance_id = models.PositiveIntegerField(
        default=123456789,  # Replace with your actual corp ID
        editable=False,
        help_text="Your alliance ID"
    )

    main_alliance = models.TextField(
        default=123456789,  # Replace with your actual corp ID
        editable=False,
        help_text="Your alliance"
    )

    is_active = models.BooleanField(
        default=False,
        editable=False,
        help_text="has the plugin been activated/deactivated?"
    )

    def __str__(self):
        return "BigBrother Configuration"

    def save(self, *args, **kwargs):
        if not self.pk and BigBrotherConfig.objects.exists():
            raise ValidationError(
                'Only one BigBrotherConfig instance is allowed!'
            )
        #self.pk = self.id = 1  # Enforce singleton
        return super().save(*args, **kwargs)