from __future__ import annotations

import discord
from redbot.core import commands

TARGET_USER_ID = 939401514739445760


class MyLine(commands.Cog):
    """Send h when mewis is annoyed"""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.author.id != TARGET_USER_ID:
            return

        content = message.content.lower()
        if "my line" in content or ("my" in content and "line" in content):
            try:
                await message.channel.send("h")
            except discord.HTTPException:
                pass
