from discord.ext import commands
from discord import app_commands
import discord
import time

class Ping(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _ping_logic(self):
        return round(self.bot.latency * 1000)

    # ===== Prefix =====
    @commands.command(name="ping")
    async def ping(self, ctx):
        latency = await self._ping_logic()
        await ctx.reply(f"🏓 Pong! `{latency}ms`")

    # ===== Slash =====
    @app_commands.command(name="ping", description="Botの応答速度を表示します")
    async def ping_slash(self, interaction: discord.Interaction):
        latency = await self._ping_logic()
        await interaction.response.send_message(
            f"🏓 Pong! `{latency}ms`",
            ephemeral=False
        )

async def setup(bot):
    await bot.add_cog(Ping(bot))
    print("ping.py cog loaded")