from django.db import models
from allianceauth.authentication.models import UserProfile
from charlink.models import ComplianceFilter
from solo.models import SingletonModel


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

    corp_check = models.PositiveIntegerField(
        default=30, 
        help_text="How many days can a user be non compliant on Corp Auth before he should get kicked?"
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

    lawn_check = models.PositiveIntegerField(
        default=30, 
        help_text="How many days can a user be non compliant on Lawn Auth before he should get kicked?"
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

    paps_check = models.PositiveIntegerField(
        default=45, 
        help_text="How many days can a user not meet the PAP requirements before he should get kicked?"
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

    Max_Afk_Days = models.PositiveIntegerField(
        default=7, 
        help_text="How many days can a user not login to game before he should get a ticket?"
    )

    afk_check = models.PositiveIntegerField(
        default=7, 
        help_text="How many days can a user not login to game after getting a ticket before he should get a ticket?"
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

    discord_check = models.PositiveIntegerField(
        default=2, 
        help_text="How many days can a user not be on corp discord before he should get kicked?"
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

    Role_ID = models.PositiveBigIntegerField(
        default=0, 
        null=True,
        blank=True,
        help_text="Role ID to get pinged alongside the non compliant user"
    )