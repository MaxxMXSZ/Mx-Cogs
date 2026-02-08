from .my_line import MyLine


async def setup(bot):
    await bot.add_cog(MyLine(bot))
