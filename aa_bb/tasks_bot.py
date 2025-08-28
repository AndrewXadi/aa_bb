# example/bot_task.py
import discord
from django.utils import timezone
from .modelss import TicketToolConfig
from .models import ComplianceTicket
from .app_settings import get_user_model
from allianceauth.authentication.models import UserProfile
from django.db import transaction
from discord.commands import SlashCommandGroup
from discord.ext import commands
from aadiscordbot.cogs.utils.decorators import sender_is_admin
from discord.commands import slash_command

def get_staff_roles():
    cfg = TicketToolConfig.get_solo()
    if not cfg.staff_roles:
        return []
    return [int(r.strip()) for r in cfg.staff_roles.split(",") if r.strip().isdigit()]

async def create_compliance_ticket(bot, user_id, discord_user_id: int, reason: str, message: str):
    category_id = TicketToolConfig.get_solo().Category_ID
    guild = bot.guilds[0]  # or use a known guild_id if multi-guild
    category = guild.get_channel(category_id)
    member = guild.get_member(discord_user_id) or await guild.fetch_member(discord_user_id)
    User = get_user_model()
    user = User.objects.get(id=user_id)
    profile = UserProfile.objects.get(user=user)

    staff_roles = get_staff_roles()

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }

    for rid in staff_roles:
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

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

class CharRemovedCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @slash_command(
        name="resolve-char-removed",
        description="Mark this channel's 'char_removed' ticket as resolved (no channel/DB deletion)."
    )
    @sender_is_admin()
    async def resolve_char_removed(self, ctx: discord.ApplicationContext):
        channel = ctx.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await ctx.respond("Use this in a ticket text channel.", ephemeral=True)
            return

        ticket = ComplianceTicket.objects.filter(
            discord_channel_id=channel.id,
            is_resolved=False,
        ).first()

        if not ticket:
            await ctx.respond("No open ticket found for this channel.", ephemeral=True)
            return

        if ticket.reason != "char_removed":
            await ctx.respond("This command only works for 'char_removed' tickets.", ephemeral=True)
            return

        ticket.is_resolved = True
        ticket.save(update_fields=["is_resolved"])

        await ctx.respond(
            f"✅ Ticket for <@{ticket.discord_user_id}> marked resolved by <@{ctx.author.id}>.",
            ephemeral=True
        )

def setup(bot):
    bot.add_cog(CharRemovedCommands(bot))