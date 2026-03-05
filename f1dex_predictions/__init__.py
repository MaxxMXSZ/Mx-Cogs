from .f1dex_predictions import F1DexPredictions


async def setup(bot):
    await bot.add_cog(F1DexPredictions(bot))
