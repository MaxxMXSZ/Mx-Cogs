from .the_list import TheList


async def setup(bot):
    await bot.add_cog(TheList(bot))
