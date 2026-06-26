import asyncio
import json
import random
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
import sympy as sp
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application,
)

try:
    import config
except ModuleNotFoundError:
    import sys
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    import config
from utils.pretty import to_sympy_input

ITEMS_PER_PAGE = 10
MENU_TIMEOUT_SECONDS = 15
QUIZ_TIMEOUT_SECONDS = 120
BASE_DIR = Path(__file__).resolve().parents[2]
QUIZ_DIR = BASE_DIR / "data" / "quiz"


class Quiz(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    class _PseudoCtx:
        def __init__(self, user, channel, guild, prefix, interaction):
            self.author = user
            self.channel = channel
            self.guild = guild
            self.prefix = prefix
            self._interaction = interaction
            self.send_blocked = False

        async def reply(self, *args, **kwargs):
            try:
                return await self.channel.send(*args, **kwargs)
            except discord.Forbidden:
                self.send_blocked = True
                return None
            except Exception:
                self.send_blocked = True
                return None

        async def send(self, *args, **kwargs):
            try:
                return await self.channel.send(*args, **kwargs)
            except discord.Forbidden:
                self.send_blocked = True
                return None
            except Exception:
                self.send_blocked = True
                return None

    # ====================
    # Prefix c!quiz / c!q
    # ====================
    @commands.command(name="quiz", aliases=["q"])
    async def quiz_cmd(self, ctx):
        mode_map = self._load_json(QUIZ_DIR / "mode.json", {})
        if not mode_map:
            await ctx.reply("quiz/mode.json が空です。")
            return

        mode_key = await self._select_from_menu(ctx, "Quiz: モード選択", mode_map)
        if not mode_key:
            return

        mode_dir = QUIZ_DIR / mode_map[mode_key]
        setting_map = self._load_json(mode_dir / "setting.json", {})
        if not setting_map:
            await ctx.reply("setting.json が空です。")
            return

        setting_key = await self._select_from_menu(ctx, "Quiz: セット選択", setting_map)
        if not setting_key:
            return

        quiz_timeout = await self._select_quiz_timeout(ctx)
        if quiz_timeout is None:
            return

        main_path = mode_dir / setting_map[setting_key] / "main.json"
        questions = self._load_json(main_path, {})
        if not questions:
            await ctx.reply("main.json が空です。")
            return

        await self._run_quiz(ctx, questions, quiz_timeout)

    # ====================
    # Slash /quiz
    # ====================
    @app_commands.command(name="quiz", description="クイズを始めます")
    async def quiz_slash(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception:
            pass

        if interaction.channel is None:
            try:
                await interaction.followup.send("この場所では実行できません。", ephemeral=True)
            except Exception:
                pass
            return

        if interaction.guild and interaction.guild.me:
            perms = interaction.channel.permissions_for(interaction.guild.me)
            if not perms.send_messages:
                try:
                    await interaction.followup.send(
                        "このチャンネルでメッセージを送信できません。",
                        ephemeral=True
                    )
                except Exception:
                    pass
                return

        ctx = self._PseudoCtx(
            user=interaction.user,
            channel=interaction.channel,
            guild=interaction.guild,
            prefix="c!",
            interaction=interaction,
        )
        await self.quiz_cmd(ctx)
        if ctx.send_blocked:
            try:
                await interaction.followup.send(
                    "このチャンネルでメッセージを送信できません。",
                    ephemeral=True
                )
            except Exception:
                pass

    # ====================
    # メニュー表示
    # ====================
    def _load_json(self, path: Path, default: dict) -> dict:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except FileNotFoundError:
            return default
        except Exception:
            return default
        return default

    def _sorted_items(self, mapping: dict) -> list:
        def key_fn(item):
            key, _ = item
            return int(key) if str(key).isdigit() else str(key)

        return sorted(mapping.items(), key=key_fn)

    def _chunk_items(self, items: list) -> list:
        return [items[i:i + ITEMS_PER_PAGE] for i in range(0, len(items), ITEMS_PER_PAGE)] or [[]]

    def _build_menu_embed(self, title: str, items: list, page: int, total: int) -> discord.Embed:
        if not items:
            body = "データがありません"
        else:
            body = "\n".join(f"{k}: {v}" for k, v in items)

        embed = discord.Embed(title=title, description=body, color=discord.Color.blue())
        embed.set_footer(text=f"xで処理を停止        {page}/{total} pages")
        return embed

    async def _clear_menu_reactions(self, msg: discord.Message) -> None:
        try:
            await msg.clear_reactions()
        except Exception:
            for emoji in (
                config.FIRST_EMOJI,
                config.PREV_EMOJI,
                config.NEXT_EMOJI,
                config.LAST_EMOJI,
            ):
                try:
                    await msg.clear_reaction(emoji)
                except Exception:
                    pass

    async def _mark_menu_stopped(self, msg: discord.Message) -> None:
        await self._clear_menu_reactions(msg)
        try:
            await msg.add_reaction("❌")
        except Exception:
            pass

    async def _select_from_menu(self, ctx, title: str, mapping: dict) -> str | None:
        items = self._sorted_items(mapping)
        pages = self._chunk_items(items)
        total = len(pages)
        index = 0

        embed = self._build_menu_embed(title, pages[index], index + 1, total)
        msg = await ctx.reply(embed=embed)
        if msg is None:
            return None

        if total > 1:
            await msg.add_reaction(config.FIRST_EMOJI)
            await msg.add_reaction(config.PREV_EMOJI)
            await msg.add_reaction(config.NEXT_EMOJI)
            await msg.add_reaction(config.LAST_EMOJI)

        def reaction_check(r, u):
            return (
                u == ctx.author
                and r.message.id == msg.id
                and str(r.emoji) in (
                    config.FIRST_EMOJI,
                    config.PREV_EMOJI,
                    config.NEXT_EMOJI,
                    config.LAST_EMOJI,
                )
            )

        def message_check(m: discord.Message) -> bool:
            return m.author == ctx.author and m.channel == ctx.channel

        while True:
            embed = self._build_menu_embed(title, pages[index], index + 1, total)
            await msg.edit(embed=embed)

            tasks = [asyncio.create_task(self.bot.wait_for("message", check=message_check))]
            if total > 1:
                tasks.append(asyncio.create_task(self.bot.wait_for("reaction_add", check=reaction_check)))

            done, pending = await asyncio.wait(
                tasks, timeout=MENU_TIMEOUT_SECONDS, return_when=asyncio.FIRST_COMPLETED
            )

            for task in pending:
                task.cancel()

            if not done:
                await self._clear_menu_reactions(msg)
                return None

            finished = done.pop()
            try:
                result = finished.result()
            except Exception:
                await self._clear_menu_reactions(msg)
                return None

            if isinstance(result, tuple):
                reaction, user = result
                try:
                    await msg.remove_reaction(reaction, user)
                except Exception:
                    pass

                if str(reaction.emoji) == config.FIRST_EMOJI:
                    index = 0
                elif str(reaction.emoji) == config.LAST_EMOJI:
                    index = total - 1
                elif str(reaction.emoji) == config.NEXT_EMOJI:
                    index = (index + 1) % total
                else:
                    index = (index - 1) % total
                continue

            content = result.content.strip()
            if content.lower() == "x":
                await self._mark_menu_stopped(msg)
                return None

            if content in mapping:
                await self._clear_menu_reactions(msg)
                return content

    async def _select_quiz_timeout(self, ctx) -> int | None:
        prompt = "botの連続実行時間<s>を指定してください(10以上1200以下)"
        msg = await ctx.reply(prompt)
        if msg is None:
            return None

        def message_check(m: discord.Message) -> bool:
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            reply = await self.bot.wait_for(
                "message",
                timeout=MENU_TIMEOUT_SECONDS,
                check=message_check,
            )
        except TimeoutError:
            return None
        except Exception:
            return None

        try:
            seconds = int(reply.content.strip())
        except Exception:
            seconds = 10

        if seconds < 10:
            seconds = 10
        if seconds > 1200:
            seconds = 1200
        return seconds

    # ====================
    # クイズ本体
    # ====================
    def _score_text(self, ctx, scores: dict) -> str:
        if not scores:
            return "まだ正解がありません"

        entries = []
        for user_id, count in scores.items():
            member = ctx.guild.get_member(user_id) if ctx.guild else None
            name = member.display_name if member else str(user_id)
            entries.append(f"{name}: {count}")

        lines = []
        for i in range(0, len(entries), 3):
            lines.append(", ".join(entries[i:i + 3]))
        return "\n".join(lines)

    def _quiz_embed(
        self,
        ctx,
        scores: dict,
        prev_answer: str,
        question: str,
        judge_text: str,
    ) -> discord.Embed:
        scores_text = self._score_text(ctx, scores)
        description = (
            "正答数\n"
            f"{scores_text}\n\n"
            "前の問題の答え\n"
            f"{prev_answer}\n\n"
            "正誤判定\n"
            f"{judge_text}\n\n"
            "問題\n"
            f"{question}"
        )
        prefix = ctx.prefix or "c!"
        embed = discord.Embed(description=description, color=discord.Color.blue())
        embed.set_footer(text=f"{prefix}q stopで停止")
        return embed

    async def _build_ranking_text(self, ctx, scores: dict) -> str:
        if not scores:
            return "正解者がいませんでした"

        items = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        lines = []
        rank = 1
        for user_id, count in items:
            member = ctx.guild.get_member(user_id) if ctx.guild else None
            if member:
                name = member.display_name
            else:
                user = self.bot.get_user(user_id)
                if user is None:
                    try:
                        user = await self.bot.fetch_user(user_id)
                    except Exception:
                        user = None
                name = user.name if user else str(user_id)
            lines.append(f"{rank}. {name}: {count}")
            rank += 1
        return "\n".join(lines)

    def _latex_to_sympy_text(self, text: str) -> str:
        s = text
        s = s.replace(r"\sin", "sin")
        s = s.replace(r"\cos", "cos")
        s = s.replace(r"\tan", "tan")
        s = s.replace(r"\sqrt", "sqrt")
        s = s.replace(r"\log", "log")
        s = s.replace("{", "(").replace("}", ")")
        s = s.replace("^", "**")
        return s

    def _parse_answer(self, text: str):
        src = to_sympy_input(text)
        src = src.replace(" ", "")
        src = self._latex_to_sympy_text(src)
        try:
            transformations = standard_transformations + (implicit_multiplication_application,)
            return parse_expr(src, transformations=transformations, evaluate=False)
        except Exception:
            return None

    def _is_disallowed(self, expr) -> bool:
        return expr.has(sp.Integral, sp.Derivative)

    def _canonicalize(self, expr):
        if expr is None:
            return None

        if expr.is_Atom:
            return expr

        if expr.is_Add or expr.is_Mul:
            args = [self._canonicalize(a) for a in expr.args]
            args_sorted = sorted(args, key=lambda a: sp.srepr(a))
            if expr.is_Add:
                return sp.Add(*args_sorted, evaluate=False)
            return sp.Mul(*args_sorted, evaluate=False)

        if expr.is_Pow:
            base = self._canonicalize(expr.base)
            exp = self._canonicalize(expr.exp)
            return sp.Pow(base, exp, evaluate=False)

        args = [self._canonicalize(a) for a in expr.args]
        try:
            return expr.func(*args)
        except Exception:
            return expr

    def _is_equivalent(self, answer: str, expected: str) -> bool:
        a = self._parse_answer(answer)
        b = self._parse_answer(expected)
        if a is None or b is None:
            return False

        if self._is_disallowed(a) or self._is_disallowed(b):
            return sp.srepr(a) == sp.srepr(b)

        ca = self._canonicalize(a)
        cb = self._canonicalize(b)
        return sp.srepr(ca) == sp.srepr(cb)

    def _is_stop(self, ctx, message: discord.Message) -> bool:
        if message.author != ctx.author:
            return False
        content = message.content.strip().lower()
        prefix = (ctx.prefix or "c!").lower()
        return content in (f"{prefix}q stop", f"{prefix}quiz stop")

    async def _run_quiz(self, ctx, questions: dict, quiz_timeout: int):
        items = list(questions.values())
        random.shuffle(items)

        scores = {}
        prev_answer = "なし"
        current_msg = None

        for item in items:
            question = item.get("q")
            answer = item.get("a")
            if not question or not answer:
                continue

            judge_text = ""
            embed = self._quiz_embed(ctx, scores, prev_answer, question, judge_text)
            if current_msg:
                try:
                    await current_msg.edit(embed=embed)
                except Exception:
                    current_msg = await ctx.send(embed=embed)
            else:
                current_msg = await ctx.send(embed=embed)

            while True:
                try:
                    msg = await self.bot.wait_for(
                        "message",
                        timeout=quiz_timeout,
                        check=lambda m: m.channel == ctx.channel and not m.author.bot,
                    )
                except TimeoutError:
                    if current_msg:
                        try:
                            await current_msg.add_reaction("❌")
                        except Exception:
                            pass
                    return
                except Exception:
                    return

                if self._is_stop(ctx, msg):
                    if current_msg:
                        try:
                            await current_msg.add_reaction("❌")
                        except Exception:
                            pass
                    return

                is_correct = self._is_equivalent(msg.content, answer)
                judge_text = f"{msg.content} : {'○' if is_correct else '×'}"
                if current_msg:
                    try:
                        await current_msg.edit(
                            embed=self._quiz_embed(ctx, scores, prev_answer, question, judge_text)
                        )
                    except Exception:
                        pass
                try:
                    await msg.delete()
                except Exception:
                    pass
                if is_correct:
                    scores[msg.author.id] = scores.get(msg.author.id, 0) + 1
                    prev_answer = answer
                    await asyncio.sleep(0.7)
                    break

        if current_msg:
            try:
                await current_msg.delete()
            except Exception:
                pass

        ranking = await self._build_ranking_text(ctx, scores)
        await ctx.send(f"```py\n順位\n{ranking}\n```")


async def setup(bot):
    await bot.add_cog(Quiz(bot))
    print("quiz.py cog loaded")
