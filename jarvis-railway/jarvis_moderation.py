"""
Moderation for Android 13. Drop next to jarvis_bot.py.

Optional .env:  MOD_LOG_CHANNEL_ID=123456789   (channel where every action is logged)

Bot needs: Kick Members, Ban Members, Moderate Members, Manage Messages,
and its role must be ABOVE the roles it moderates.
"""
import os
import sqlite3
import time
from datetime import timedelta

import discord
from discord import app_commands

LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID", "0"))
WARN_TIMEOUT_AT = 3          # every 3rd warning auto-times-out the user
WARN_TIMEOUT_MINUTES = 60
MAX_TIMEOUT_MINUTES = 40320  # Discord's 28-day limit

db = sqlite3.connect(os.getenv("DB_PATH", "jarvis.db"))  # set DB_PATH to a volume path on Railway
db.execute(
    "CREATE TABLE IF NOT EXISTS warns ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INT, user_id INT, "
    "mod_id INT, reason TEXT, ts INT)"
)
db.commit()

PERMS = {
    "kick": "kick_members",
    "ban": "ban_members",
    "timeout": "moderate_members",
    "untimeout": "moderate_members",
    "warn": "moderate_members",
}


# ---------------------------------------------------------------- helpers
async def log(guild: discord.Guild, text: str):
    ch = guild.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
    if ch:
        await ch.send(text, allowed_mentions=discord.AllowedMentions.none())


def check_hierarchy(guild, actor: discord.Member, target: discord.Member) -> str | None:
    """Return an error string if the action must not happen, else None."""
    if target.id == actor.id:
        return "You can't moderate yourself."
    if target.id == guild.owner_id:
        return "You can't moderate the server owner."
    if target.id == guild.me.id:
        return "Nice try."
    if actor.id != guild.owner_id and target.top_role >= actor.top_role:
        return "That member's top role is equal to or above yours."
    if target.top_role >= guild.me.top_role:
        return "My role isn't high enough to act on that member."
    return None


def add_warn(guild_id, user_id, mod_id, reason) -> int:
    db.execute(
        "INSERT INTO warns (guild_id, user_id, mod_id, reason, ts) VALUES (?,?,?,?,?)",
        (guild_id, user_id, mod_id, reason, int(time.time())),
    )
    db.commit()
    return db.execute(
        "SELECT COUNT(*) FROM warns WHERE guild_id=? AND user_id=?", (guild_id, user_id)
    ).fetchone()[0]


async def do_action(guild, actor, target, action, reason, minutes=10, delete_days=0) -> str:
    """The ONLY place that actually moderates. Callers must have checked permissions."""
    why = f"{actor} ({actor.id}): {reason}"[:480]  # shows in the server audit log
    if action == "kick":
        await guild.kick(target, reason=why)
        out = f"Kicked {target}."
    elif action == "ban":
        await guild.ban(target, reason=why, delete_message_seconds=delete_days * 86400)
        out = f"Banned {target}."
    elif action == "timeout":
        await target.timeout(timedelta(minutes=minutes), reason=why)
        out = f"Timed out {target} for {minutes} min."
    elif action == "untimeout":
        await target.timeout(None, reason=why)
        out = f"Removed timeout from {target}."
    elif action == "warn":
        n = add_warn(guild.id, target.id, actor.id, reason)
        out = f"Warned {target} ({n} total)."
        if n % WARN_TIMEOUT_AT == 0:
            await target.timeout(timedelta(minutes=WARN_TIMEOUT_MINUTES), reason=f"{n} warnings")
            out += f" Auto-timed out for {WARN_TIMEOUT_MINUTES} min."
    else:
        return "Unknown action."
    await log(guild, f"**{action}** | {target} ({target.id}) | by {actor} | {reason} | {out}")
    return out


class Confirm(discord.ui.View):
    """Only the person who asked can press it, and `run` re-checks nothing, so
    callers must verify permissions BEFORE building this."""

    def __init__(self, actor_id: int, run):
        super().__init__(timeout=60)
        self.actor_id, self.run = actor_id, run

    async def interaction_check(self, inter: discord.Interaction) -> bool:
        if inter.user.id != self.actor_id:
            await inter.response.send_message("This confirmation isn't yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def yes(self, inter: discord.Interaction, button: discord.ui.Button):
        self.stop()
        try:
            result = await self.run()
        except discord.HTTPException as e:
            result = f"Failed: {e}"
        await inter.response.edit_message(content=result, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, inter: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await inter.response.edit_message(content="Cancelled.", view=None)


# ---------------------------------------------------------------- LLM tool (propose only)
TOOL_SCHEMA = {
    "name": "propose_moderation",
    "description": (
        "Propose a moderation action (kick, ban, timeout, warn) against a member. "
        "This does NOT execute it: it posts a Confirm button the requesting moderator "
        "must press. Only use when the user explicitly asks for moderation. "
        "Get the target's numeric ID from the <@id> mention in the message."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["kick", "ban", "timeout", "warn"]},
            "user_id": {"type": "string", "description": "Target's numeric Discord ID."},
            "reason": {"type": "string"},
            "minutes": {"type": "number", "description": "Timeout length (timeout only)."},
        },
        "required": ["action", "user_id", "reason"],
    },
}


async def propose(args: dict, channel, user_id: int, guild) -> str:
    if guild is None:
        return "Moderation only works inside a server."
    action = args.get("action")
    perm = PERMS.get(action)
    if not perm:
        return "Error: unknown action."
    try:
        actor = await guild.fetch_member(user_id)
        target = await guild.fetch_member(int(args["user_id"]))
    except (KeyError, ValueError, discord.NotFound):
        return "Error: couldn't find that member. Ask the user to @mention them."
    if not getattr(actor.guild_permissions, perm):
        return f"Refused: the requester lacks the {perm} permission."
    err = check_hierarchy(guild, actor, target)
    if err:
        return f"Refused: {err}"

    reason = str(args.get("reason") or "No reason given")[:300]
    minutes = max(1, min(int(args.get("minutes") or 10), MAX_TIMEOUT_MINUTES))

    async def run():
        return await do_action(guild, actor, target, action, reason, minutes)

    detail = f" for {minutes} min" if action == "timeout" else ""
    await channel.send(
        f"{actor.mention} Android 13 proposes: **{action}** {target.mention}{detail}. "
        f"Reason: {reason}\nPress Confirm within 60s.",
        view=Confirm(actor.id, run),
        allowed_mentions=discord.AllowedMentions(users=[actor]),
    )
    return "Confirmation prompt posted. NOTHING has been executed yet; tell the user to press Confirm."


# ---------------------------------------------------------------- slash commands
def setup(tree: app_commands.CommandTree):
    async def guarded(inter, target, action, reason, confirm=False, **kw):
        err = check_hierarchy(inter.guild, inter.user, target)
        if err:
            return await inter.response.send_message(err, ephemeral=True)

        async def run():
            return await do_action(inter.guild, inter.user, target, action, reason, **kw)

        if confirm:
            return await inter.response.send_message(
                f"Really **{action}** {target.mention}? Reason: {reason}",
                view=Confirm(inter.user.id, run),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        try:
            msg = await run()
        except discord.HTTPException as e:
            msg = f"Failed: {e}"
        await inter.response.send_message(msg, allowed_mentions=discord.AllowedMentions.none())

    @tree.command(name="kick", description="Kick a member")
    @app_commands.guild_only()
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(inter: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
        await guarded(inter, member, "kick", reason)

    @tree.command(name="ban", description="Ban a member (asks for confirmation)")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(
        inter: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason given",
        delete_days: app_commands.Range[int, 0, 7] = 0,
    ):
        await guarded(inter, member, "ban", reason, confirm=True, delete_days=delete_days)

    @tree.command(name="timeout", description="Time a member out")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(
        inter: discord.Interaction,
        member: discord.Member,
        minutes: app_commands.Range[int, 1, MAX_TIMEOUT_MINUTES],
        reason: str = "No reason given",
    ):
        await guarded(inter, member, "timeout", reason, minutes=minutes)

    @tree.command(name="untimeout", description="Remove a member's timeout")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(inter: discord.Interaction, member: discord.Member):
        await guarded(inter, member, "untimeout", "Timeout removed")

    @tree.command(name="warn", description="Warn a member (auto-timeout every 3rd)")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(inter: discord.Interaction, member: discord.Member, reason: str):
        await guarded(inter, member, "warn", reason)

    @tree.command(name="warnings", description="Show a member's recent warnings")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(inter: discord.Interaction, member: discord.Member):
        rows = db.execute(
            "SELECT reason, mod_id, ts FROM warns WHERE guild_id=? AND user_id=? "
            "ORDER BY ts DESC LIMIT 10",
            (inter.guild.id, member.id),
        ).fetchall()
        if not rows:
            return await inter.response.send_message(f"{member} has no warnings.", ephemeral=True)
        lines = [f"<t:{ts}:d> by <@{m}>: {r}" for r, m, ts in rows]
        await inter.response.send_message(
            f"**{member}** warnings:\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @tree.command(name="purge", description="Bulk-delete recent messages")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(inter: discord.Interaction, count: app_commands.Range[int, 1, 100]):
        await inter.response.defer(ephemeral=True)
        deleted = await inter.channel.purge(limit=count)
        await log(inter.guild, f"**purge** | {len(deleted)} msgs in {inter.channel.mention} by {inter.user}")
        await inter.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)

    @tree.error
    async def on_error(inter: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure) and not isinstance(error, app_commands.MissingPermissions):
            return  # access gate already answered
        if isinstance(error, app_commands.MissingPermissions):
            msg = "You don't have permission to do that."
        else:
            msg = f"Something went wrong: {error}"
        if inter.response.is_done():
            await inter.followup.send(msg, ephemeral=True)
        else:
            await inter.response.send_message(msg, ephemeral=True)
