from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union

import discord
from redbot.core import commands, Config


@dataclass
class ListEntry:
    user_id: int
    covered: bool = False
    plus_one_used: bool = False


class ListPaginator(discord.ui.View):
    def __init__(self, author_id: int, embeds: List[discord.Embed]):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.embeds = embeds
        self.page_index = 0
        self.message: Optional[discord.Message] = None
        self._update_button_states()

    def _update_button_states(self) -> None:
        first_page = self.page_index == 0
        last_page = self.page_index == len(self.embeds) - 1
        self.first_page.disabled = first_page
        self.back_page.disabled = first_page
        self.next_page.disabled = last_page
        self.last_page.disabled = last_page

    async def _show_page(self, interaction: discord.Interaction) -> None:
        self._update_button_states()
        await interaction.response.edit_message(embed=self.embeds[self.page_index], view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This menu isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="<<", style=discord.ButtonStyle.secondary)
    async def first_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.page_index = 0
        await self._show_page(interaction)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.primary)
    async def back_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.page_index > 0:
            self.page_index -= 1
        await self._show_page(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.page_index < len(self.embeds) - 1:
            self.page_index += 1
        await self._show_page(interaction)

    @discord.ui.button(label=">>", style=discord.ButtonStyle.secondary)
    async def last_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.page_index = len(self.embeds) - 1
        await self._show_page(interaction)

    @discord.ui.button(label="Quit", style=discord.ButtonStyle.danger)
    async def stop_view(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


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

    def _resolve_role(self, guild: discord.Guild, role_input: str) -> Optional[discord.Role]:
        role_input = role_input.strip()
        if role_input.startswith("<@&") and role_input.endswith(">"):
            role_id = role_input[3:-1]
            if role_id.isdigit():
                return guild.get_role(int(role_id))

        if role_input.isdigit():
            return guild.get_role(int(role_input))

        if role_input.startswith("@"):
            role_input = role_input[1:]

        role = discord.utils.get(guild.roles, name=role_input)
        if role:
            return role

        lowered = role_input.lower()
        for role in guild.roles:
            if role.name.lower() == lowered:
                return role

        return None

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
                if member:
                    display = f"||{member.mention}||"
                else:
                    display = f"||{base_name}||"

            lines.append(f"{index}. {display}")

        page_size = 20
        total_pages = (len(lines) + page_size - 1) // page_size
        embeds = []
        for page_index in range(total_pages):
            start = page_index * page_size
            chunk = lines[start : start + page_size]
            title = "THE list"
            if total_pages > 1:
                title = f"THE list (Page {page_index + 1}/{total_pages})"
            embeds.append(discord.Embed(title=title, description="\n".join(chunk)))

        if total_pages == 1:
            await ctx.send(embed=embeds[0])
            return

        view = ListPaginator(ctx.author.id, embeds)
        message = await ctx.send(embed=embeds[0], view=view)
        view.message = message

    @list_group.command(name="setrole")
    @commands.guild_only()
    async def list_setrole(self, ctx: commands.Context, *, role: str) -> None:
        """Set The Department of Justice"""
        perms = ctx.author.guild_permissions
        if not (perms.manage_guild or perms.administrator):
            await ctx.send("You need Manage Server or Administrator to set the list role.")
            return

        resolved_role = self._resolve_role(ctx.guild, role)
        if resolved_role is None:
            await ctx.send("I could not find that role. Try mentioning it or using the role ID.")
            return

        await self.config.guild(ctx.guild).role_id.set(resolved_role.id)
        await ctx.send(f"Justice Department has been chosen: {resolved_role.mention}.")

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

    @list_group.command(name="plusone")
    @commands.guild_only()
    async def list_plusone(
        self,
        ctx: commands.Context,
        user: Union[discord.Member, int],
    ) -> None:
        """Invite a plus one fam"""
        target_id = user.id if isinstance(user, discord.Member) else int(user)
        if target_id == 718365766671663144:
            await ctx.send("You thought you could add me LMAO")
            return

        entries = await self._get_entries(ctx.guild)
        inviter_entry = next(
            (entry for entry in entries if entry.user_id == ctx.author.id),
            None,
        )
        if inviter_entry is None:
            await ctx.send("Lil bro you aint even on THE list.")
            return
        if inviter_entry.plus_one_used:
            await ctx.send("You already invited a plus one bro")
            return
        if any(entry.user_id == target_id for entry in entries):
            await ctx.send("Lil bro the guy is already on THE list.")
            return

        entries.append(ListEntry(user_id=target_id, covered=False))
        inviter_entry.plus_one_used = True
        await self._set_entries(ctx.guild, entries)
        await ctx.send(f"Plus one added: <@{target_id}>.")

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
