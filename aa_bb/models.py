"""
App Models
Create your models in here
"""

# Django
from django.db import models
from django.core.exceptions import ValidationError
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
            ("can_blacklist_characters", "Can add characters to blacklist"),
            )
        
class UserStatus(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    has_awox_kills = models.BooleanField(default=False)
    awox_kill_links = JSONField(default=dict, blank=True)
    has_cyno = models.BooleanField(default=False)
    cyno = JSONField(default=dict, blank=True)
    has_skills = models.BooleanField(default=False)
    skills = JSONField(default=dict, blank=True)
    has_hostile_assets = models.BooleanField(default=False)
    hostile_assets = JSONField(default=dict, blank=True)
    has_hostile_clones = models.BooleanField(default=False)
    hostile_clones = JSONField(default=dict, blank=True)
    has_imp_blacklist = models.BooleanField(default=False)
    has_lawn_blacklist = models.BooleanField(default=False)
    has_game_time_notifications = models.BooleanField(default=False)
    has_skill_injected = models.BooleanField(default=False)
    has_sus_contacts = models.BooleanField(default=False)
    sus_contacts = JSONField(default=dict, blank=True)
    has_sus_contracts = models.BooleanField(default=False)
    sus_contracts = JSONField(default=dict, blank=True)
    has_sus_mails = models.BooleanField(default=False)
    sus_mails = JSONField(default=dict, blank=True)
    has_sus_trans = models.BooleanField(default=False)
    sus_trans = JSONField(default=dict, blank=True)
    last_updated = models.DateTimeField(auto_now=True)

class Messages(models.Model):
    text = models.TextField(max_length=2000)
    sent_in_cycle = models.BooleanField(default=False)
    def __str__(self):
        return self.text


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
        default=0,
        help_text="Input the role ID you want pinged when people need to investigate"
    )

    hostile_alliances = models.TextField(
        default="1900696668,741557221,154104258,99013231,495729389,99002685,99001317,99012770,99010281,99009977,498125261,99007203,99003581,99005338,1042504553,1727758877,386292982,99011983,99012617,917526329,99009927,99006941,1411711376,99003557,99006411,98718891,99011312,99010877,99007887,99010735,99000285,99007629,1988009451,1220922756,99011990,99011416,99011268,933731581,99005874",
        blank=True,
        null=True,
        help_text="List of alliance IDs considered hostile, separated by ','"
    )

    hostile_corporations = models.TextField(
        blank=True,
        null=True,
        help_text="List of corporation IDs considered hostile, separated by ','"
    )

    whitelist_alliances = models.TextField(
        default="",
        blank=True,
        null=True,
        help_text="List of alliance IDs considered whitelisted, separated by ','"
    )

    whitelist_corporations = models.TextField(
        blank=True,
        null=True,
        help_text="List of corporation IDs considered whitelisted, separated by ','"
    )

    webhook = models.URLField(
        blank=True,
        null=True,
        help_text="Discord webhook for sending BB notifications"
    )

    dailywebhook = models.URLField(
        blank=True,
        null=True,
        help_text="Discord webhook for sending daily messages"
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

    are_daily_messages_active = models.BooleanField(
        default=False,
        editable=True,
        help_text="are daily messages activated/deactivated?"
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
    
class Corporation_names(models.Model):
    """
    Permanent store of corporation names resolved via ESI.
    """
    id = models.BigIntegerField(
        primary_key=True,
        help_text="EVE Corporation ID"
    )
    name = models.CharField(
        max_length=255,
        help_text="Resolved corporation name"
    )
    created = models.DateTimeField(
        auto_now_add=True,
        help_text="When this record was first saved"
    )
    updated = models.DateTimeField(
        auto_now=True,
        help_text="When this record was last refreshed"
    )

    class Meta:
        db_table = 'aa_bb_corporations'
        verbose_name = 'Corporation Name'
        verbose_name_plural = 'Corporation Names'

    def __str__(self):
        return f"{self.id}: {self.name}"
    
class Alliance_names(models.Model):
    """
    Permanent store of alliance/faction names resolved via ESI.
    """
    id = models.BigIntegerField(
        primary_key=True,
        help_text="EVE Alliance or Faction ID"
    )
    name = models.CharField(
        max_length=255,
        help_text="Resolved alliance/faction name"
    )
    created = models.DateTimeField(
        auto_now_add=True,
        help_text="When this record was first saved"
    )
    updated = models.DateTimeField(
        auto_now=True,
        help_text="When this record was last refreshed"
    )

    class Meta:
        db_table = 'aa_bb_alliances'
        verbose_name = 'Alliance Name'
        verbose_name_plural = 'Alliance Names'

    def __str__(self):
        return f"{self.id}: {self.name}"
    
class Character_names(models.Model):
    """
    Permanent store of Character names resolved via ESI.
    """
    id = models.BigIntegerField(
        primary_key=True,
        help_text="EVE Character ID"
    )
    name = models.CharField(
        max_length=255,
        help_text="Resolved Character name"
    )
    created = models.DateTimeField(
        auto_now_add=True,
        help_text="When this record was first saved"
    )
    updated = models.DateTimeField(
        auto_now=True,
        help_text="When this record was last refreshed"
    )

    class Meta:
        db_table = 'aa_bb_characters'
        verbose_name = 'Character Name'
        verbose_name_plural = 'Character Names'

    def __str__(self):
        return f"{self.id}: {self.name}"
    

class id_types(models.Model):
    """
    Permanent store of Character names resolved via ESI.
    """
    id = models.BigIntegerField(
        primary_key=True,
        help_text="EVE ID"
    )
    name = models.CharField(
        max_length=255,
        help_text="Resolved ID Type"
    )
    created = models.DateTimeField(
        auto_now_add=True,
        help_text="When this record was first saved"
    )
    updated = models.DateTimeField(
        auto_now=True,
        help_text="When this record was last refreshed"
    )

    class Meta:
        db_table = 'aa_bb_ids'
        verbose_name = 'ID Type'
        verbose_name_plural = 'ID Types'

    def __str__(self):
        return f"{self.id}: {self.name}"
    

class ProcessedMail(models.Model):
    """
    Tracks which MailMessage IDs we've already generated notes for.
    """
    mail_id = models.BigIntegerField(
        primary_key=True,
        help_text="The MailMessage.id_key that we've processed"
    )
    processed_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When we first processed this mail"
    )

    class Meta:
        db_table = "aa_bb_processed_mails"
        verbose_name = "Processed Mail"
        verbose_name_plural = "Processed Mails"

    def __str__(self):
        return f"ProcessedMail {self.mail_id} @ {self.processed_at}"


class SusMailNote(models.Model):
    """
    Stores the summary line (flags) generated for each hostile mail.
    """
    mail = models.OneToOneField(
        ProcessedMail,
        on_delete=models.CASCADE,
        help_text="The mail this note refers to"
    )
    user_id = models.BigIntegerField(
        help_text="The AllianceAuth user ID who owns these characters"
    )
    note = models.TextField(
        help_text="The summary string of flags for this mail"
    )
    created = models.DateTimeField(
        auto_now_add=True,
        help_text="When this note was created"
    )
    updated = models.DateTimeField(
        auto_now=True,
        help_text="When this note was last updated"
    )

    class Meta:
        db_table = "aa_bb_sus_mail_notes"
        verbose_name = "Suspicious Mail Note"
        verbose_name_plural = "Suspicious Mail Notes"

    def __str__(self):
        return f"Mail {self.mail.mail_id} note for user {self.user_id}"


class ProcessedContract(models.Model):
    """
    Tracks which Contract IDs we've already generated notes for.
    """
    contract_id = models.BigIntegerField(
        primary_key=True,
        help_text="The Contract.contract_id that we've processed"
    )
    processed_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When we first processed this contract"
    )

    class Meta:
        db_table = "aa_bb_processed_contracts"
        verbose_name = "Processed Contract"
        verbose_name_plural = "Processed Contracts"

    def __str__(self):
        return f"ProcessedContract {self.contract_id} @ {self.processed_at}"


class SusContractNote(models.Model):
    """
    Stores the summary line (flags) generated for each hostile contract.
    """
    contract = models.OneToOneField(
        ProcessedContract,
        on_delete=models.CASCADE,
        help_text="The contract this note refers to"
    )
    user_id = models.BigIntegerField(
        help_text="The AllianceAuth user ID who owns these characters"
    )
    note = models.TextField(
        help_text="The summary string of flags for this contract"
    )
    created = models.DateTimeField(
        auto_now_add=True,
        help_text="When this note was created"
    )
    updated = models.DateTimeField(
        auto_now=True,
        help_text="When this note was last updated"
    )

    class Meta:
        db_table = "aa_bb_sus_contract_notes"
        verbose_name = "Suspicious Contract Note"
        verbose_name_plural = "Suspicious Contract Notes"

    def __str__(self):
        return f"Contract {self.contract.contract_id} note for user {self.user_id}"
    

    from django.db import models

class ProcessedTransaction(models.Model):
    """
    Tracks which WalletJournalEntry IDs we've already generated notes for.
    """
    entry_id = models.BigIntegerField(
        primary_key=True,
        help_text="The WalletJournalEntry.entry_id that we've processed"
    )
    processed_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When we first processed this transaction"
    )

    class Meta:
        db_table = "aa_bb_processed_transactions"
        verbose_name = "Processed Transaction"
        verbose_name_plural = "Processed Transactions"

    def __str__(self):
        return f"ProcessedTransaction {self.entry_id} @ {self.processed_at}"


class SusTransactionNote(models.Model):
    """
    Stores the summary line (flags) generated for each hostile transaction.
    """
    transaction = models.OneToOneField(
        ProcessedTransaction,
        on_delete=models.CASCADE,
        help_text="The transaction this note refers to"
    )
    user_id = models.BigIntegerField(
        help_text="The AllianceAuth user ID who owns these characters"
    )
    note = models.TextField(
        help_text="The summary string of flags for this transaction"
    )
    created = models.DateTimeField(
        auto_now_add=True,
        help_text="When this note was created"
    )
    updated = models.DateTimeField(
        auto_now=True,
        help_text="When this note was last updated"
    )

    class Meta:
        db_table = "aa_bb_sus_transaction_notes"
        verbose_name = "Suspicious Transaction Note"
        verbose_name_plural = "Suspicious Transaction Notes"

    def __str__(self):
        return f"Transaction {self.transaction.entry_id} note for user {self.user_id}"


class WarmProgress(models.Model):
    user_main = models.CharField(max_length=100, unique=True)
    current   = models.PositiveIntegerField()
    total     = models.PositiveIntegerField()
    updated   = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user_main}: {self.current}/{self.total}"
    

class EntityInfoCache(models.Model):
    entity_id  = models.IntegerField()
    as_of      = models.DateTimeField()
    data       = JSONField()
    updated    = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("entity_id", "as_of")
        indexes = [
            models.Index(fields=["entity_id", "as_of"]),
            models.Index(fields=["updated"]),
        ]