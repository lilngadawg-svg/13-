"""
Server management + access control for Android 13.

- Only people with an allowed role can use Android 13 at all (chat AND slash commands).
- Add/remove roles, change nicknames, create channels.
- The AI can only PROPOSE changes; the requester must press Confirm.

.env:
    ALLOWED_ROLES=Members, 123456789012345678    (names or IDs; blank = everyone)

Bot needs: Manage Roles, Manage Nicknames, Manage Channels. Its own role must sit
ABOVE any role it hands out and above any member whose nickname/roles it changes.
"""
import os
import re
from typing import Literal

import discord
from discord import app_commands

from jarvis_moderation import Confirm, log

ALLOWED = [a.strip().lower() for a in os.getenv("ALLOWED_ROLES", "").split(",") if a.strip()]
NO_PINGS = discord.AllowedMentions.none()

PERM_FOR = {
    "add_role": "manage_roles",
    "remove_role": "manage_roles",
    "nickname": "manage_nicknames",
    "create_channel": "manage_channels",
}


# ---------------------------------------------------------------- access gate
def is_allowed(user) -> bool:
    """True if this person may use Android 13. The server owner always can."""
    if not ALLOWED:
        return True
    if not isinstance(user, discord.Member):  # DMs: we can't verify a role
        return False
    if user.id == user.guild.owner_id:
        return True
    return any(str(r.id) in ALLOWED or r.name.lower() in ALLOWED for r in user.roles)


class GatedTree(app_commands.CommandTree):
    """Slash-command tree that blocks everyone without an allowed role."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if is_allowed(interaction.user):
            return True
        await interaction.response.send_message("You don't have clearance. Access denied.", ephemeral=True)
        return False


# ---------------------------------------------------------------- validation
def _find_role(guild: discord.Guild, text: str) -> discord.Role | None:
    text = (text or "").strip()
    m = re.fullmatch(r"<@&(\d+)>", text)
    if m:
        text = m.group(1)
    if text.isdigit():
        return guild.get_role(int(text))
    return discord.utils.find(lambda r: r.name.lower() == text.lower(), guild.roles)


def _find_category(guild: discord.Guild, name: str) -> discord.CategoryChannel | None:
    return discord.utils.find(lambda c: c.name.lower() == name.strip().lower(), guild.categories)


def _err_member(guild, actor: discord.Member, target: discord.Member) -> str | None:
    if target.id == guild.owner_id:
        return "I can't edit the server owner."
    if target.id != actor.id and actor.id != guild.owner_id and target.top_role >= actor.top_role:
        return "that member's top role is equal to or above yours."
    if target.id != guild.me.id and target.top_role >= guild.me.top_role:
        return "that member's top role is above mine. Move my role higher."
    return None


def _err_role(guild, actor: discord.Member, role: discord.Role) -> str | None:
    if role.is_default():
        return "you can't assign @everyone."
    if role.managed:
        return "that role belongs to a bot/integration and can't be assigned by hand."
    if role >= guild.me.top_role:
        return "that role is above my highest role. Move my role higher."
    if actor.id != guild.owner_id:
        if role >= actor.top_role:
            return "that role is equal to or above your highest role."
        if role.permissions.value & ~actor.guild_permissions.value:
            return "that role has permissions you don't have yourself."
    return None


def _check(guild, actor, action, target, role, name, category) -> str | None:
    """Return a reason the action must not happen, or None if it's fine."""
    perm = PERM_FOR[action]
    if not getattr(actor.guild_permissions, perm):
        return f"you lack the {perm.replace('_', ' ')} permission."
    if action in ("add_role", "remove_role"):
        return _err_member(guild, actor, target) or _err_role(guild, actor, role)
    if action == "nickname":
        return _err_member(guild, actor, target)
    if action == "create_channel":
        if not (name or "").strip():
            return "a channel needs a name."
        if category and not _find_category(guild, category):
            return f"there's no category named '{category}'."
    return None


# ---------------------------------------------------------------- the only place that changes things
async def run_action(
    guild, actor, action, *, target=None, role=None, nick=None,
    name=None, kind="text", category=None, reason="requested via Android 13",
) -> str:
    if action not in PERM_FOR:
        return "Refused: unknown action."
    err = _check(guild, actor, action, target, role, name, category)  # re-check at execution time
    if err:
        return f"Refused: {err}"
    why = f"{actor} ({actor.id}): {reason}"[:480]
    try:
        if action == "add_role":
            await target.add_roles(role, reason=why)
            out = f"Gave **{role.name}** to {target.display_name}."
        elif action == "remove_role":
            await target.remove_roles(role, reason=why)
            out = f"Removed **{role.name}** from {target.display_name}."
        elif action == "nickname":
            new = (nick or "").strip()[:32] or None
            await target.edit(nick=new, reason=why)
            out = f"Nickname for {target.name} is now **{new}**." if new else f"Reset nickname for {target.name}."
        else:  # create_channel
            cat = _find_category(guild, category) if category else None
            clean = name.strip()[:100]
            if kind == "voice":
                ch = await guild.create_voice_channel(clean, category=cat, reason=why)
            else:
                ch = await guild.create_text_channel(clean, category=cat, reason=why)
            out = f"Created {ch.mention}."
    except discord.Forbidden:
        return "Failed: Discord says I'm missing permission. Check my role's permissions and that it's high enough."
    except discord.HTTPException as e:
        return f"Failed: {e}"
    await log(guild, f"**{action}** | by {actor} | {out}")
    return out


# ---------------------------------------------------------------- AI tool (propose only)
TOOL_SCHEMA = {
    "name": "propose_server_change",
    "description": (
        "Propose a server change: give or remove a role, change a member's nickname, or create "
        "a channel. This does NOT execute it: it posts a Confirm button the requesting user must "
        "press. Only use when the user explicitly asks. Identify members by the numeric ID in "
        "their <@id> mention. Identify roles by the ID in a <@&id> mention or by exact name."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(PERM_FOR)},
            "user_id": {"type": "string", "description": "Target member's numeric ID (roles/nickname)."},
            "role": {"type": "string", "description": "Role ID or exact name (add_role/remove_role)."},
            "nickname": {"type": "string", "description": "New nickname; empty string resets it."},
            "channel_name": {"type": "string"},
            "channel_type": {"type": "string", "enum": ["text", "voice"]},
            "category": {"type": "string", "description": "Optional existing category name."},
        },
        "required": ["action"],
    },
}


async def propose(args: dict, channel, user_id: int, guild) -> str:
    if guild is None:
        return "Server changes only work inside a server."
    action = args.get("action")
    if action not in PERM_FOR:
        return "Error: unknown action."
    try:
        actor = await guild.fetch_member(user_id)
    except discord.NotFound:
        return "Error: couldn't identify the requester."

    target = role = None
    if action != "create_channel":
        try:
            target = await guild.fetch_member(int(args["user_id"]))
        except (KeyError, ValueError, discord.NotFound):
            return "Error: couldn't find that member. Ask the user to @mention them."
    if action in ("add_role", "remove_role"):
        role = _find_role(guild, str(args.get("role", "")))
        if not role:
            return "Error: couldn't find that role. Use its exact name or @mention it."
    nick = args.get("nickname")
    if action == "nickname" and nick is None:
        return "Error: no nickname given (use an empty string to reset it)."
    name = args.get("channel_name")
    kind = args.get("channel_type") if args.get("channel_type") in ("text", "voice") else "text"
    category = args.get("category") or None

    err = _check(guild, actor, action, target, role, name, category)
    if err:
        return f"Refused: {err}"

    if action == "add_role":
        summary = f"give **{role.name}** to {target.mention}"
    elif action == "remove_role":
        summary = f"remove **{role.name}** from {target.mention}"
    elif action == "nickname":
        summary = f"set {target.mention}'s nickname to **{(nick or '').strip()[:32] or '(reset)'}**"
    else:
        summary = f"create a {kind} channel **{name.strip()[:100]}**" + (f" in **{category}**" if category else "")

    async def run():
        return await run_action(
            guild, actor, action, target=target, role=role, nick=nick,
            name=name, kind=kind, category=category,
        )

    await channel.send(
        f"{actor.mention} Android 13 proposes to {summary}.\nPress Confirm within 60s.",
        view=Confirm(actor.id, run),
        allowed_mentions=discord.AllowedMentions(users=[actor]),
    )
    return "Confirmation prompt posted. NOTHING has been executed yet; tell the user to press Confirm."


# ---------------------------------------------------------------- slash commands
def setup(tree: app_commands.CommandTree):
    async def direct(inter: discord.Interaction, action: str, **kw):
        await inter.response.defer()
        msg = await run_action(inter.guild, inter.user, action, **kw)
        await inter.followup.send(msg, allowed_mentions=NO_PINGS)

    @tree.command(name="addrole", description="Give a member a role")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def addrole(inter: discord.Interaction, member: discord.Member, role: discord.Role):
        await direct(inter, "add_role", target=member, role=role)

    @tree.command(name="removerole", description="Remove a role from a member")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    async def removerole(inter: discord.Interaction, member: discord.Member, role: discord.Role):
        await direct(inter, "remove_role", target=member, role=role)

    @tree.command(name="nick", description="Change a member's nickname (leave empty to reset)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_nicknames=True)
    async def nick(inter: discord.Interaction, member: discord.Member, nickname: str = ""):
        await direct(inter, "nickname", target=member, nick=nickname)

    @tree.command(name="makechannel", description="Create a text or voice channel")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    async def makechannel(
        inter: discord.Interaction,
        name: str,
        kind: Literal["text", "voice"] = "text",
        category: str | None = None,
    ):
        await direct(inter, "create_channel", name=name, kind=kind, category=category)
