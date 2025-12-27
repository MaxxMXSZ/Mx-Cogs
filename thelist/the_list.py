from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union

import discord
from redbot.core import commands, Config


@dataclass
class ListEntry:
    user_id: int
    covered: bool = False


class TheList(commands.Cog):
    """Roleplay as the Department of Justice"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=4519023184, force_registration=True)
        self.config.register_guild(role_id=None, entries=[])

    async def _get_role(self, guild: discord.Guild) -> Optional[discord.Role]:
        role_id = await self.config.guild(guild).role_id()
        if role_id is None:
            return None
        return guild.get_role(role_id)

    async def _get_entries(self, guild: discord.Guild) -> List[ListEntry]:
        raw_entries = await self.config.guild(guild).entries()
        return [ListEntry(**entry) for entry in raw_entries]

    async def _set_entries(self, guild: discord.Guild, entries: List[ListEntry]) -> None:
        raw_entries = [entry.__dict__ for entry in entries]
        await self.config.guild(guild).entries.set(raw_entries)

    @commands.group(name="list", invoke_without_command=True)
    @commands.guild_only()
    async def list_group(self, ctx: commands.Context) -> None:
        """Show the current list."""
        entries = await self._get_entries(ctx.guild)
        if not entries:
            embed = discord.Embed(title="THE list", description="The list is empty. THANK GOD BRO")
            await ctx.send(embed=embed)
            return

        lines = []
        for index, entry in enumerate(entries, start=1):
            member = ctx.guild.get_member(entry.user_id)
            base_name = None
            if member:
                base_name = member.display_name
                display = member.mention
            else:
                user = self.bot.get_user(entry.user_id)
                if user:
                    base_name = user.name
                    display = user.mention
                else:
                    base_name = f"User ID {entry.user_id}"
                    display = base_name

            if entry.covered:
                display = f"||{base_name}||"

            lines.append(f"{index}. {display}")

        page_size = 20
        total_pages = (len(lines) + page_size - 1) // page_size
        for page_index in range(total_pages):
            start = page_index * page_size
            chunk = lines[start : start + page_size]
            title = "THE list"
            if total_pages > 1:
                title = f"THE list (Page {page_index + 1}/{total_pages})"
            embed = discord.Embed(title=title, description="\n".join(chunk))
            await ctx.send(embed=embed)

    @list_group.command(name="setrole")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def list_setrole(self, ctx: commands.Context, role: discord.Role) -> None:
        """Set The Department of Justice"""
        await self.config.guild(ctx.guild).role_id.set(role.id)
        await ctx.send(f"Justice Department has been chosen: {role.mention}.")

    @list_group.command(name="add")
    @commands.guild_only()
    async def list_add(
        self,
        ctx: commands.Context,
        user: Union[discord.Member, int],
    ) -> None:
        """Add a user to the list."""
        role = await self._get_role(ctx.guild)
        if role is None:
            await ctx.send("Lil bro theres no Justice Deparment yet")
            return

        if role not in ctx.author.roles:
            await ctx.send("You aint no american justice bald eagle person bro")
            return

        user_id = user.id if isinstance(user, discord.Member) else int(user)
        entries = await self._get_entries(ctx.guild)

        if any(entry.user_id == user_id for entry in entries):
            await ctx.send("Gang bro's already on the list.")
            return

        entries.append(ListEntry(user_id=user_id, covered=False))
        await self._set_entries(ctx.guild, entries)
        await ctx.send(f"Chat <@{user_id}> has been added to THE list.💀😭")

    @list_group.command(name="cover")
    @commands.guild_only()
    async def list_cover(
        self,
        ctx: commands.Context,
        user: Union[discord.Member, int],
    ) -> None:
        """Cover it up bro."""
        role = await self._get_role(ctx.guild)
        if role is None:
            await ctx.send("Lil bro theres no Justice Deparment yet")
            return

        if role not in ctx.author.roles:
            await ctx.send("aint no american justice bald eagle person bro")
            return

        user_id = user.id if isinstance(user, discord.Member) else int(user)
        entries = await self._get_entries(ctx.guild)

        for entry in entries:
            if entry.user_id == user_id:
                if entry.covered:
                    await ctx.send("He already covered bro.")
                    return
                entry.covered = True
                await self._set_entries(ctx.guild, entries)
                await ctx.send(f"Covered ||<@{user_id}>|| on THE list.")
                return

        await ctx.send("Bro isn't even on THE list.😶")
