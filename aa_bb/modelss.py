from django.db import models
from django.utils import timezone
from allianceauth.authentication.models import UserProfile
from charlink.models import ComplianceFilter
from solo.models import SingletonModel
from django.contrib.auth.models import User
from datetime import timedelta
from django.db.models import JSONField


class PapCompliance(models.Model):
    user_profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="pap_compliances",
        help_text="The UserProfile this PAP compliance record belongs to",
    )
    pap_compliant = models.IntegerField(
        default=0,
        help_text="Integer flag or score indicating PAP compliance status"
    )


class TicketToolConfig(SingletonModel):
    compliance_filter = models.ForeignKey(
        ComplianceFilter,
        related_name="compliance_filter",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        help_text="Select your compliance filter"
    )

    ticket_counter = models.PositiveIntegerField(default=0, help_text="Rolling counter for ticket channel names", editable=False)

    max_months_without_pap_compliance = models.PositiveIntegerField(
        default=1,  
        help_text="How many months can a person be in corp w/o meeting the pap requirements? (this is a maximum points a user can get, 1 compliant month = plus 1 point, 1 non compliant = minus 1 point. If user has 0 points they get a ticket)"
    )

    starting_pap_compliance = models.PositiveIntegerField(
        default=1,  
        help_text="How many buffer months does a new user get? (starter value of the above)"
    )

    char_removed_enabled = models.BooleanField(
        default=False,
        editable=True,
        help_text="Do you want to check for removed characters?"
    )

    awox_monitor_enabled = models.BooleanField(
        default=False,
        editable=True,
        help_text="Do you want to check for awox kills?"
    )

    corp_check_enabled = models.BooleanField(
        default=False,
        editable=True,
        help_text="Do you want to check for corp auth compliance?"
    )

    corp_check = models.PositiveIntegerField(
        default=30, 
        help_text="How many days can a user be non compliant on Corp Auth before he should get kicked?"
    )

    corp_check_frequency = models.PositiveIntegerField(
        default=1, 
        help_text="How often should a user be reminded (in days)"
    )

    corp_check_reason = models.TextField(
        default="# <@&{role}>,<@{namee}>\nSome of your characters are missing a valid token on corp auth, go fix it",
        blank=True,
        null=True,
        help_text="Message to send with {role} and {namee} variables"
    )

    corp_check_reminder = models.TextField(
        default="<@&{role}>,<@{namee}>, your compliance issue is still unresolved, you have {days} day(s) to fix it or you'll be kicked out.",
        blank=True,
        null=True,
        help_text="Message to send with {role}, {namee} and {days} variables"
    )

    lawn_check_enabled = models.BooleanField(
        default=False,
        editable=True,
        help_text="Do you want to check for lawn auth compliance?"
    )

    lawn_check = models.PositiveIntegerField(
        default=30, 
        help_text="How many days can a user be non compliant on Lawn Auth before he should get kicked?"
    )

    lawn_check_frequency = models.PositiveIntegerField(
        default=1, 
        help_text="How often should a user be reminded (in days)"
    )

    lawn_check_reason = models.TextField(
        default="<@&{role}>,<@{namee}>\nSome of your characters are missing a valid token on lawn auth, go fix it",
        blank=True,
        null=True,
        help_text="Message to send with {role} and {namee} variables"
    )

    lawn_check_reminder = models.TextField(
        default="<@&{role}>,<@{namee}>, your compliance issue is still unresolved, you have {days} day(s) to fix it or you'll be kicked out.",
        blank=True,
        null=True,
        help_text="Message to send with {role}, {namee} and {days} variables"
    )

    paps_check_enabled = models.BooleanField(
        default=False,
        editable=True,
        help_text="Do you want to check for pap requirement compliance?"
    )

    paps_check = models.PositiveIntegerField(
        default=45, 
        help_text="How many days can a user not meet the PAP requirements before he should get kicked?"
    )

    paps_check_frequency = models.PositiveIntegerField(
        default=1, 
        help_text="How often should a user be reminded (in days)"
    )

    paps_check_reason = models.TextField(
        default="<@&{role}>,<@{namee}>, You have fallen below the threshold of months you get to be without meeting the pap requirements, fix it.",
        blank=True,
        null=True,
        help_text="Message to send with {role} and {namee} variables"
    )

    paps_check_reminder = models.TextField(
        default="Reminder that if you don't meet the PAP quota this month, you will be kicked out, you have {days} day(s) to fix it.",
        blank=True,
        null=True,
        help_text="Message to send with {days} variable"
    )

    afk_check_enabled = models.BooleanField(
        default=False,
        editable=True,
        help_text="Do you want to check if the user logs into the game??"
    )

    Max_Afk_Days = models.PositiveIntegerField(
        default=7, 
        help_text="How many days can a user not login to game before he should get a ticket?"
    )

    afk_check = models.PositiveIntegerField(
        default=7, 
        help_text="How many days can a user not login to game after getting a ticket before he should get a ticket?"
    )

    afk_check_frequency = models.PositiveIntegerField(
        default=1, 
        help_text="How often should a user be reminded (in days)"
    )

    afk_check_reason = models.TextField(
        default="<@&{role}>,<@{namee}>, you have been inactive for over {days} day(s) without a LoA request, please fix it or submit a LoA request.",
        blank=True,
        null=True,
        help_text="Message to send with {role}, {namee} and {days} variables"
    )

    afk_check_reminder = models.TextField(
        default="<@&{role}>,<@{namee}>, your compliance issue is still unresolved, you have {days} day(s) to fix it or you'll be kicked out.",
        blank=True,
        null=True,
        help_text="Message to send with {role}, {namee} and {days} variables"
    )

    discord_check_enabled = models.BooleanField(
        default=False,
        editable=True,
        help_text="Do you want to check for discord activity?"
    )

    discord_check = models.PositiveIntegerField(
        default=2, 
        help_text="How many days can a user not be on corp discord before he should get kicked?"
    )

    discord_check_frequency = models.PositiveIntegerField(
        default=1, 
        help_text="How often should a user be reminded (in days)"
    )

    discord_check_reason = models.TextField(
        default="<@&{role}>,<@{namee}>, doesn't have their discord linked on corp auth, try to contact them and if unable, kick them out",
        blank=True,
        null=True,
        help_text="Message to send with {role} and {namee} variables"
    )

    discord_check_reminder = models.TextField(
        default="<@&{role}>,<@{namee}>'s compliance issue is still unresolved, try to contact them and if unable within {days} day(s) kick them out.",
        blank=True,
        null=True,
        help_text="Message to send with {role}, {namee} and {days} variables"
    )

    Category_ID = models.PositiveBigIntegerField(
        default=0, 
        null=True,
        blank=True,
        help_text="Category ID to create the tickets in"
    )

    staff_roles = models.TextField(
        blank=True,
        help_text="Comma-separated list of staff role IDs allowed on tickets"
    )

    Role_ID = models.PositiveBigIntegerField(
        default=0, 
        null=True,
        blank=True,
        help_text="Role ID to get pinged alongside the non compliant user"
    )

    excluded_users = models.ManyToManyField(
        User,
        related_name="excluded_users",
        blank=True,
        help_text="List of users to ignore when checking for compliance"
    )


class BBUpdateState(SingletonModel):
    """Singleton to persist BB update check timing/version across restarts."""
    update_check_time = models.DateTimeField(null=True, blank=True)
    latest_version = models.CharField(max_length=50, null=True, blank=True)

    def __str__(self):
        ts = self.update_check_time.isoformat() if self.update_check_time else "None"
        ver = self.latest_version or "None"
        return f"BBUpdateState(time={ts}, version={ver})"


class CharacterEmploymentCache(models.Model):
    """Cache of character employment timeline (intended 4h TTL)."""
    char_id = models.BigIntegerField(primary_key=True)
    data = models.JSONField()
    updated = models.DateTimeField(auto_now=True)
    last_accessed = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "character_employment_cache"
        indexes = [
            models.Index(fields=["updated"]),
            models.Index(fields=["last_accessed"]),
        ]


class FrequentCorpChangesCache(models.Model):
    """Cache of pre-rendered frequent corp changes HTML per user (intended 4h TTL)."""
    user_id = models.BigIntegerField(primary_key=True)
    html = models.TextField()
    updated = models.DateTimeField(auto_now=True)
    last_accessed = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "frequent_corp_changes_cache"
        indexes = [
            models.Index(fields=["updated"]),
            models.Index(fields=["last_accessed"]),
        ]


class CurrentStintCache(models.Model):
    """Cache of current stint days per (char, corp) (intended 4h TTL)."""
    char_id = models.BigIntegerField()
    corp_id = models.BigIntegerField()
    days = models.IntegerField(default=0)
    updated = models.DateTimeField(auto_now=True)
    last_accessed = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "current_stint_cache"
        unique_together = ("char_id", "corp_id")
        indexes = [
            models.Index(fields=["char_id", "corp_id"]),
            models.Index(fields=["updated"]),
            models.Index(fields=["last_accessed"]),
        ]


class AwoxKillsCache(models.Model):
    """Indefinite cache of AWOX kills per user; pruned by last_accessed (60d)."""
    user_id = models.BigIntegerField(primary_key=True)
    data = models.JSONField()
    updated = models.DateTimeField(auto_now=True)
    last_accessed = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "awox_kills_cache"
        indexes = [
            models.Index(fields=["updated"]),
            models.Index(fields=["last_accessed"]),
        ]
class LeaveRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ("in_progress","In Progress"),
        ("finished",   "Finished"),
        ('denied', 'Denied'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='leave_requests')
    main_character = models.CharField(
        max_length=100,
        blank=True,
        help_text="The user's primary character when they made the request"
    )
    start_date = models.DateField()
    end_date   = models.DateField()
    reason     = models.TextField()
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username}: {self.start_date} → {self.end_date} ({self.status})"
    

class CorporationInfoCache(models.Model):
    corp_id = models.BigIntegerField(primary_key=True)
    name = models.CharField(max_length=255)
    member_count = models.IntegerField(default=0)
    updated = models.DateTimeField(auto_now=True)  # auto-updated on save

    class Meta:
        db_table = "corporation_info_cache"
        indexes = [
            models.Index(fields=["updated"]),
        ]

    @property
    def is_fresh(self):
        """Check if cache entry is still valid (24h TTL)."""
        return timezone.now() - self.updated < timedelta(hours=24)
    

class AllianceHistoryCache(models.Model):
    corp_id = models.BigIntegerField(primary_key=True)
    history = JSONField()  # store list of {alliance_id, start_date}
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "alliance_history_cache"
        indexes = [
            models.Index(fields=["updated"]),
        ]

    @property
    def is_fresh(self):
        """Check if data is still within TTL."""
        return timezone.now() - self.updated < timedelta(hours=24)
    

class SovereigntyMapCache(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)  # single row
    data = models.JSONField()
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sovereignty_map_cache"

    @property
    def is_fresh(self):
        return timezone.now() - self.updated < timedelta(hours=24)
    
class CharacterAccountState(models.Model):
    ALPHA = "alpha"
    OMEGA = "omega"
    UNKNOWN = "unknown"

    STATE_CHOICES = [
        (ALPHA, "Alpha"),
        (OMEGA, "Omega"),
        (UNKNOWN, "Unknown"),
    ]

    char_id = models.BigIntegerField(primary_key=True)
    skill_used = models.BigIntegerField(blank=True, null=True)
    state = models.CharField(max_length=10, choices=STATE_CHOICES)

    def __str__(self):
        return f"{self.char_id} - {self.state}"
    

class ComplianceTicket(models.Model):
    REASONS = [
        ("corp_check", "Corp Compliance"),
        ("lawn_check", "LAWN Compliance"),
        ("paps_check", "PAP Requirements"),
        ("afk_check", "Inactivity"),
        ("discord_check", "User is not on discord"),
        {"char_removed", "Character removed"},
        {"awox_kill", "AWOX kill found"},
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    discord_user_id = models.BigIntegerField()
    discord_channel_id = models.BigIntegerField(null=True, blank=True)
    ticket_id = models.BigIntegerField(null=True, blank=True)

    reason = models.CharField(max_length=20, choices=REASONS)
    created_at = models.DateTimeField(auto_now_add=True)
    last_reminder_sent = models.IntegerField(default=0)

    is_resolved = models.BooleanField(default=False)

    def __str__(self):
        return f"Ticket for {self.user} ({self.reason})"
