# example/bot_task.py
import discord
from django.utils import timezone
from .models import ComplianceTicket, TicketToolConfig
from .app_settings import get_user_model
from allianceauth.authentication.models import UserProfile
from django.db import transaction

async def create_compliance_ticket(bot, user_id, discord_user_id: int, username: str, reason: str, message: str):
    category_id = TicketToolConfig.get_solo().Category_ID
    role_id = TicketToolConfig.get_solo().Role_ID
    guild = bot.guilds[0]  # or use a known guild_id if multi-guild
    category = guild.get_channel(category_id)
    member = guild.get_member(discord_user_id) or await guild.fetch_member(discord_user_id)
    staff_role = guild.get_role(role_id)
    User = get_user_model()
    user = User.objects.get(id=user_id)
    profile = UserProfile.objects.get(user=user)
    if username != "":
        display_name = username
    else:
        display_name = member.display_name

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        staff_role: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }

    ticket_number = get_next_ticket_number()

    channel = await guild.create_text_channel(
        name=f"ticket-{ticket_number}",
        category=category,
        overwrites=overwrites,
        topic=f"Compliance ticket for {profile.main_character} [{reason}]",
    )

    await channel.send(message)

    ComplianceTicket.objects.create(
        user=user,
        discord_user_id=member.id,
        discord_channel_id=channel.id,
        reason=reason,
    )


async def send_ticket_reminder(bot, channel_id: int, user_id: int, message: str):
    channel = bot.get_channel(channel_id)
    member = channel.guild.get_member(user_id)
    if channel and member:
        await channel.send(message)

async def close_ticket_channel(bot, channel_id: int):
    channel = bot.get_channel(channel_id)
    if channel:
        await channel.delete(reason="Compliance issue resolved")

def get_next_ticket_number():
    """
    Returns the next ticket number as a zero-padded string (0000–9999),
    increments and wraps the counter in TicketToolConfig.
    """
    with transaction.atomic():
        cfg = TicketToolConfig.get_solo()
        num = cfg.ticket_counter or 0
        formatted = f"{num:04d}"  # zero-padded to 4 digits
        # increment & wrap
        cfg.ticket_counter = (num + 1) % 10000
        cfg.save(update_fields=["ticket_counter"])
    return formatted