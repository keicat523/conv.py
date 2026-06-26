import asyncio
import os
import time
import traceback
　
import discord
from discord import app_commands
from discord.ext import commands

import config
from admin_ids import ADMIN_IDS
from utils.ignore_mode import is_ignore_enabled
from utils.pretty import to_sympy_input
from utils.prefix_manager import load_prefix
from utils.timeout_manager import get_timeout_seconds
from flask import Flask
import threading

# Bot基本設定 
intents = discord.Intents.default()
intents.message_content = True

TOKEN = config.DISCORD_TOKEN
if TOKEN is None:
    raise RuntimeError("DISCORD_TOKEN が設定されていません")

# Prefix / タイムアウト設定
DEFAULT_PREFIX = "c!"
if not hasattr(config, "TIMEOUT_EXEMPT_COMMANDS"):
    print("設定: TIMEOUT_EXEMPT_COMMANDS が不足しています")
    raise RuntimeError("TIMEOUT_EXEMPT_COMMANDS が config.py に定義されていません")

TIMEOUT_EXEMPT_COMMANDS = {
    str(name).lower()
    for name in config.TIMEOUT_EXEMPT_COMMANDS
    if isinstance(name, str) and name
}


def is_admin_user(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_admin_cog_module(module_name: str | None) -> bool:
    return bool(module_name) and module_name.startswith("cogs.admin")


def is_math_cog_module(module_name: str | None) -> bool:
    return bool(module_name) and module_name.startswith("cogs.math")


def get_prefix(bot, message):
    if message.author.bot:
        return DEFAULT_PREFIX

    user_prefix = load_prefix(message.author.id)
    return commands.when_mentioned_or(DEFAULT_PREFIX, user_prefix)(bot, message)


def is_timeout_exempt_command(command_name: str | None) -> bool:
    if command_name and command_name.lower() in {"execute", "exe"}:
        return True
    return bool(command_name) and command_name.lower() in TIMEOUT_EXEMPT_COMMANDS


# mathコマンド用の入力整形
def _normalize_math_value(value):
    if isinstance(value, str):
        return to_sympy_input(value)
    return value


def normalize_math_ctx_args(ctx: commands.Context) -> None:
    if not ctx.command or not ctx.command.cog:
        return
    module_name = ctx.command.cog.__module__
    if not is_math_cog_module(module_name):
        return

    ctx.args = tuple(_normalize_math_value(v) for v in ctx.args)
    ctx.kwargs = {k: _normalize_math_value(v) for k, v in ctx.kwargs.items()}


def normalize_math_interaction_args(interaction: discord.Interaction) -> None:
    try:
        module_name = interaction.command.callback.__module__ if interaction.command else None
    except Exception:
        module_name = None

    if not is_math_cog_module(module_name):
        return

    namespace = getattr(interaction, "namespace", None)
    if namespace is None:
        return

    for key, value in vars(namespace).items():
        if isinstance(value, str):
            setattr(namespace, key, to_sympy_input(value))


# 安全送信ヘルパー
async def safe_send_interaction(interaction: discord.Interaction, msg: str, *, ephemeral: bool = True):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(msg, ephemeral=ephemeral)
    except discord.errors.NotFound:
        pass


async def safe_send_ctx(ctx: commands.Context, msg: str):
    try:
        await ctx.reply(msg)
    except Exception:
        try:
            await ctx.send(msg)
        except Exception:
            pass


class TimeoutCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and is_admin_user(interaction.user.id):
            return True

        try:
            module_name = interaction.command.callback.__module__ if interaction.command else None
        except Exception:
            module_name = None

        if is_admin_cog_module(module_name):
            await safe_send_interaction(interaction, "管理者権限がありません", ephemeral=True)
            return False

        if is_ignore_enabled():
            await safe_send_interaction(interaction, "現在管理者以外使用できません", ephemeral=True)
            return False

        return True

    async def _call(self, interaction: discord.Interaction):
        normalize_math_interaction_args(interaction)

        command_name = interaction.command.name if interaction.command else None
        if is_timeout_exempt_command(command_name):
            return await super()._call(interaction)

        timeout_seconds = get_timeout_seconds()
        task = asyncio.create_task(super()._call(interaction))
        done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
        if task in done:
            return await task

        task.cancel()
        try:
            await task
        except Exception:
            pass
        except BaseException:
            pass

        if not task.done() or task.cancelled():
            await safe_send_interaction(
                interaction,
                f"実行時間制限({timeout_seconds:g}秒)を超過しました",
                ephemeral=True,
            )


class MyBot(commands.Bot):
    async def invoke(self, ctx: commands.Context):
        normalize_math_ctx_args(ctx)

        command_name = ctx.command.name if ctx.command else None
        if is_timeout_exempt_command(command_name):
            return await super().invoke(ctx)

        timeout_seconds = get_timeout_seconds()
        task = asyncio.create_task(super().invoke(ctx))
        done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
        if task in done:
            return await task

        task.cancel()
        try:
            await task
        except Exception:
            pass
        except BaseException:
            pass

        await safe_send_ctx(ctx, f"実行時間制限({timeout_seconds:g}秒)を超過しました")
        return

    async def setup_hook(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cogs_dir = os.path.join(base_dir, "cogs")

        self.tree.allowed_contexts = app_commands.AppCommandContext(
            guild=True,
            dm_channel=True,
            private_channel=True,
        )

        for root, _, files in os.walk(cogs_dir):
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                if filename.startswith("_"):
                    continue

                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, base_dir)
                module = rel_path.replace(os.sep, ".")[:-3]
                await self.load_extension(module)

        guild_id = getattr(config, "GUILD_ID", None)
        if guild_id:
            try:
                guild_obj = discord.Object(id=int(guild_id))
            except Exception:
                guild_obj = None
            if guild_obj is not None:
                self.tree.copy_global_to(guild=guild_obj)
                await self.tree.sync(guild=guild_obj)
                return

        await self.tree.sync()


bot = MyBot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
    tree_cls=TimeoutCommandTree,
)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


# グローバルチェック / エラーハンドリング
@bot.check
async def ignore_check(ctx):
    if is_admin_user(ctx.author.id):
        return True

    if ctx.command and ctx.command.cog:
        module_name = ctx.command.cog.__module__
        if is_admin_cog_module(module_name):
            await safe_send_ctx(ctx, "管理者権限がありません")
            return False

    if is_ignore_enabled():
        await safe_send_ctx(ctx, "現在管理者以外使用できません")
        return False

    return True


@bot.event
async def on_command_error(ctx, error):
    if hasattr(ctx.command, "on_error"):
        return

    if isinstance(error, commands.CheckFailure):
        return

    if isinstance(error, commands.CommandNotFound):
        return

    original = getattr(error, "original", error)

    if isinstance(original, (asyncio.TimeoutError, asyncio.CancelledError)):
        await safe_send_ctx(ctx, f"実行時間制限({get_timeout_seconds():g}秒)を超過しました")
        return

    tb = "".join(
        traceback.format_exception(
            type(original),
            original,
            original.__traceback__,
        )
    )

    if len(tb) > 1900:
        tb = tb[-1900:]

    await safe_send_ctx(
        ctx,
        "⚠️ **エラーが発生しました**\n"
        f"```py\n{tb}\n```",
    )

    print(tb)


if __name__ == "__main__":
    while True:
        try:
            app = Flask(__name__)
            @app.route("/")
            def home():
                return "Bot is running"
            
            def run_web():
                port = int(os.environ.get("PORT", 10000))
                app.run(host="0.0.0.0", port=port)
            
            threading.Thread(target=run_web, daemon=True).start()
            
            bot.run(TOKEN)
            break
        except AttributeError as exc:
            if "'NoneType' object has no attribute 'sequence'" not in str(exc):
                raise
            print("Discord gateway returned an invalid startup response. Retrying in 5 seconds...")
            time.sleep(5)
