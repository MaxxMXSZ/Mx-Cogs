from .thelist import TheList


async def setup(bot):
    await bot.add_cog(TheList(bot))
