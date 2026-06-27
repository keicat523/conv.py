import asyncio
import json
import re
import shutil
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

try:
    from PIL import Image, ImageChops
except ModuleNotFoundError:
    Image = None
    ImageChops = None

import config
from utils.timeout_manager import get_menu_timeout_seconds

ITEMS_PER_PAGE = 30
MESSAGE_LIMIT = 1900
LATEX_COMMAND_TIMEOUT = 150.0
LATEX_IMAGE_TIMEOUT = 600.0
LATEX_EDIT_TIMEOUT = 600.0
LATEX_ERROR_SNIPPET_LIMIT = 1800
BASE_DIR = Path(__file__).resolve().parents[2]
LATEX_DIR = BASE_DIR / "data" / "latex"
TEMP_DIR = BASE_DIR / "temp"
TEMP_LATEX_DIR = TEMP_DIR / "latex"
IMAGE_RENDER_DPI = 100
IMAGE_MARGIN_CM = 0.9
IMAGE_TEX_BODY_PLACEHOLDER = "__LATEX_IMAGE_BODY__"
LATEX_ACTIONS_TEXT = "list/base/edit/create/copy/delete/image/settings/output/install"
LATEX_LINEBREAK_DIMENSION_RE = re.compile(
    r"(?<!\\)\\\[\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:pt|mm|cm|in|ex|em|bp|pc|dd|cc|sp))\s*\]"
)
DEFAULT_TEX = "\n".join(
    [
        "\\documentclass{ltjsarticle}",
        "\\usepackage{tcolorbox,mathcomp,tcolorbox,amsmath,mathtools,amssymb,graphicx,ascmac,fancybox,framed,tikz,comment,calc,enumitem}",
        "\\begin{document}",
        "",
        "\\end{document}",
        "",
    ]
)
IMAGE_TEX_TEMPLATE = "\n".join(
    [
        "\\RequirePackage{plautopatch}",
        "\\documentclass[preview,border=3pt]{ltjsarticle}",
        "\\usepackage{tcolorbox,mathcomp,tcolorbox,amsmath,mathtools,amssymb,graphicx,ascmac,fancybox,framed,tikz,comment,calc,enumitem}",
        "\\tcbuselibrary{breakable, skins, theorems}",
        "\\usetikzlibrary{positioning, intersections, calc, arrows.meta,math}",
        "\\pagestyle{empty}",
        "\\begin{document}",
        IMAGE_TEX_BODY_PLACEHOLDER,
        "\\end{document}",
        "",
    ]
)

TEMP_LATEX_DIR.mkdir(parents=True, exist_ok=True)


class Latex(commands.Cog):
    class _SlashEditTargetModal(discord.ui.Modal):
        def __init__(self, cog: "Latex", project_name: str):
            super().__init__(title=f"Edit {project_name}")
            self.cog = cog
            self.project_name = project_name
            self.target = discord.ui.TextInput(
                label="編集する行",
                placeholder="1 / 2-3 / all",
                default="1",
                max_length=20,
            )
            self.add_item(self.target)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            await self.cog._handle_slash_edit_target_submit(
                interaction,
                self.project_name,
                str(self.target.value),
            )

    class _SlashEditContentModal(discord.ui.Modal):
        def __init__(self, cog: "Latex", project_name: str, start: int, end: int, default_text: str):
            super().__init__(title=f"Edit {project_name}:{start}-{end}")
            self.cog = cog
            self.project_name = project_name
            self.start = start
            self.end = end
            self.body = discord.ui.TextInput(
                label="編集後のtex",
                style=discord.TextStyle.paragraph,
                default=default_text[:4000],
                max_length=4000,
            )
            self.add_item(self.body)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            await self.cog._handle_slash_edit_content_submit(
                interaction,
                self.project_name,
                self.start,
                self.end,
                str(self.body.value),
            )

    class _SlashEditButton(discord.ui.Button):
        def __init__(self, cog: "Latex", project_name: str):
            super().__init__(label="編集範囲を指定", style=discord.ButtonStyle.primary)
            self.cog = cog
            self.project_name = project_name

        async def callback(self, interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(self.cog._SlashEditTargetModal(self.cog, self.project_name))

    class _SlashProjectSelect(discord.ui.Select):
        def __init__(self, cog: "Latex", projects: list[str]):
            self.cog = cog
            options = [
                discord.SelectOption(label=name[:100], value=name)
                for name in projects[:25]
            ]
            super().__init__(placeholder="編集するプロジェクト", options=options)

        async def callback(self, interaction: discord.Interaction) -> None:
            await self.cog._show_slash_edit_project(interaction, self.values[0])

    class _UserOnlyView(discord.ui.View):
        def __init__(self, user_id: int, *, timeout: float | None = None):
            super().__init__(timeout=timeout)
            self.user_id = user_id

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id == self.user_id:
                return True
            await interaction.response.send_message("この操作はあなた専用です", ephemeral=True)
            return False

    class _PseudoCtx:
        def __init__(self, user, channel, guild, interaction: discord.Interaction):
            self.author = user
            self.channel = channel
            self.guild = guild
            self._interaction = interaction
            self.send_blocked = False
            self.is_slash = True

        async def reply(self, *args, **kwargs):
            try:
                if not self._interaction.response.is_done():
                    await self._interaction.response.defer()
                if args and "content" not in kwargs:
                    kwargs["content"] = args[0]
                kwargs.pop("wait", None)
                file = kwargs.pop("file", None)
                files = kwargs.pop("files", None)
                if file is not None:
                    kwargs["attachments"] = [file]
                elif files is not None:
                    kwargs["attachments"] = list(files)
                return await self._interaction.edit_original_response(**kwargs)
            except discord.Forbidden:
                self.send_blocked = True
                return None
            except discord.NotFound:
                self.send_blocked = True
                return None
            except Exception:
                self.send_blocked = True
                return None

        async def send(self, *args, **kwargs):
            return await self.reply(*args, **kwargs)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="latex", aliases=["tex"])
    async def latex_cmd(
        self,
        ctx: commands.Context,
        action: str | None = None,
        *args: str,
    ) -> None:
        if action is None:
            await ctx.reply(self._latex_usage_text(ctx))
            return

        await self._dispatch_latex(ctx, action, args)

    @app_commands.command(name="latex", description="LaTeXプロジェクトを管理します")
    @app_commands.describe(
        action="実行する操作",
        name="プロジェクト名やbase名。imageではtex本文にも使えます",
        extra="copy時の複製後名、またはimage時のtex本文",
        subaction="baseやsettings用の追加操作",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="list", value="list"),
            app_commands.Choice(name="base", value="base"),
            app_commands.Choice(name="create", value="create"),
            app_commands.Choice(name="edit", value="edit"),
            app_commands.Choice(name="copy", value="copy"),
            app_commands.Choice(name="delete", value="delete"),
            app_commands.Choice(name="image", value="image"),
            app_commands.Choice(name="settings", value="settings"),
            app_commands.Choice(name="output", value="output"),
            app_commands.Choice(name="install", value="install"),
        ],
        subaction=[
            app_commands.Choice(name="create", value="create"),
            app_commands.Choice(name="edit", value="edit"),
            app_commands.Choice(name="name", value="name"),
        ],
    )
    async def latex_slash(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        name: str | None = None,
        extra: str | None = None,
        subaction: app_commands.Choice[str] | None = None,
    ) -> None:
        if interaction.channel is None:
            await self._send_interaction(interaction, "この場所では実行できません", ephemeral=True)
            return

        try:
            await interaction.response.defer()
        except Exception:
            pass

        ctx = self._PseudoCtx(
            user=interaction.user,
            channel=interaction.channel,
            guild=interaction.guild,
            interaction=interaction,
        )
        args = self._build_slash_args(action.value, name, extra, subaction.value if subaction else None)
        await self._dispatch_latex(ctx, action.value, args)
        if ctx.send_blocked:
            await self._send_interaction(interaction, "この場所ではメッセージを送信できません", ephemeral=True)

    @app_commands.command(name="tex", description="LaTeXプロジェクトを管理します")
    @app_commands.describe(
        action="実行する操作",
        name="プロジェクト名やbase名。imageではtex本文にも使えます",
        extra="copy時の複製後名、またはimage時のtex本文",
        subaction="baseやsettings用の追加操作",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="list", value="list"),
            app_commands.Choice(name="base", value="base"),
            app_commands.Choice(name="create", value="create"),
            app_commands.Choice(name="edit", value="edit"),
            app_commands.Choice(name="copy", value="copy"),
            app_commands.Choice(name="delete", value="delete"),
            app_commands.Choice(name="image", value="image"),
            app_commands.Choice(name="settings", value="settings"),
            app_commands.Choice(name="output", value="output"),
            app_commands.Choice(name="install", value="install"),
        ],
        subaction=[
            app_commands.Choice(name="create", value="create"),
            app_commands.Choice(name="edit", value="edit"),
            app_commands.Choice(name="name", value="name"),
        ],
    )
    async def tex_slash(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        name: str | None = None,
        extra: str | None = None,
        subaction: app_commands.Choice[str] | None = None,
    ) -> None:
        if interaction.channel is None:
            await self._send_interaction(interaction, "この場所では実行できません", ephemeral=True)
            return

        try:
            await interaction.response.defer()
        except Exception:
            pass

        ctx = self._PseudoCtx(
            user=interaction.user,
            channel=interaction.channel,
            guild=interaction.guild,
            interaction=interaction,
        )
        args = self._build_slash_args(action.value, name, extra, subaction.value if subaction else None)
        await self._dispatch_latex(ctx, action.value, args)
        if ctx.send_blocked:
            await self._send_interaction(interaction, "この場所ではメッセージを送信できません", ephemeral=True)

    async def _dispatch_latex(self, ctx, action: str, args: tuple[str, ...]) -> None:
        action = action.lower()

        if action == "list":
            await self._run_with_timeout(ctx, self._latex_list(ctx), LATEX_COMMAND_TIMEOUT)
            return
        if action == "base":
            await self._latex_base(ctx, args)
            return
        if action == "create":
            await self._run_with_timeout(ctx, self._latex_create(ctx, args), LATEX_COMMAND_TIMEOUT)
            return
        if action == "edit":
            if getattr(ctx, "is_slash", False):
                await self._latex_edit_slash(ctx, args)
                return
            await self._run_with_timeout(ctx, self._latex_edit(ctx, args), LATEX_EDIT_TIMEOUT)
            return
        if action == "copy":
            await self._run_with_timeout(ctx, self._latex_copy(ctx, args), LATEX_COMMAND_TIMEOUT)
            return
        if action == "delete":
            await self._run_with_timeout(ctx, self._latex_delete(ctx, args), LATEX_COMMAND_TIMEOUT)
            return
        if action == "image":
            payload = "\n".join(arg for arg in args if arg).strip()
            if not payload:
                payload = self._extract_action_payload(ctx, action)
            await self._run_with_timeout(ctx, self._latex_image(ctx, payload), LATEX_IMAGE_TIMEOUT)
            return
        if action == "settings":
            await self._run_with_timeout(ctx, self._latex_settings(ctx, args), LATEX_COMMAND_TIMEOUT)
            return
        if action == "output":
            await self._run_with_timeout(ctx, self._latex_output(ctx, args), LATEX_COMMAND_TIMEOUT)
            return
        if action == "install":
            await self._run_with_timeout(ctx, self._latex_install(ctx, args), LATEX_COMMAND_TIMEOUT)
            return

        await ctx.reply(self._latex_usage_text(ctx))

    def _latex_usage_text(self, ctx) -> str:
        prefix = getattr(ctx, "prefix", None) or "c!"
        invoked = getattr(ctx, "invoked_with", None) or "latex"
        return f"使用方法: {prefix}{invoked} <{LATEX_ACTIONS_TEXT}> ..."

    def _build_slash_args(
        self,
        action: str,
        name: str | None,
        extra: str | None,
        subaction: str | None,
    ) -> tuple[str, ...]:
        values: list[str] = []
        if action == "image":
            payload = extra or name
            return (payload,) if payload else ()

        if action == "base":
            if subaction:
                values.append(subaction)
            if name:
                values.append(name)
            return tuple(values)

        if action == "settings":
            if subaction:
                values.append(subaction)
            elif name:
                values.append(name)
            return tuple(values)

        if name:
            values.append(name)
        if extra:
            values.append(extra)
        return tuple(values)

    def _extract_action_payload(self, ctx: commands.Context, action: str) -> str:
        if not hasattr(ctx, "message"):
            return ""
        content = ctx.message.content
        prefix = ctx.prefix or ""
        invoked = ctx.invoked_with or "latex"
        head = f"{prefix}{invoked}"
        if content.startswith(head):
            content = content[len(head):]
        content = content.lstrip()
        if content.lower().startswith(action.lower()):
            content = content[len(action):]
        return content.lstrip()

    async def _send_interaction(self, interaction: discord.Interaction, message: str, *, ephemeral: bool = False):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(message, ephemeral=ephemeral)
        except Exception:
            pass

    async def _run_with_timeout(self, ctx: commands.Context, coro, timeout_seconds: float) -> None:
        try:
            await asyncio.wait_for(coro, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            await ctx.reply(f"実行時間制限({timeout_seconds:g}秒)を超過しました")

    def _project_dir(self, name: str) -> Path:
        return LATEX_DIR / name

    def _main_tex_path(self, name: str) -> Path:
        return self._project_dir(name) / "main.tex"

    def _base_tex_path(self, name: str) -> Path:
        return self._project_dir(name) / f"{name}.tex"

    def _base_json_path(self, name: str) -> Path:
        return self._project_dir(name) / "project.json"

    def _sanitize_project_name(self, raw: str | None) -> str:
        if raw is None:
            return ""
        name = raw.strip().replace("\\", "").replace("/", "")
        return name

    def _list_projects(self) -> list[str]:
        LATEX_DIR.mkdir(parents=True, exist_ok=True)
        return sorted(
            [path.name for path in LATEX_DIR.iterdir() if path.is_dir() and (path / "main.tex").exists()],
            key=str.lower,
        )

    def _list_bases(self) -> list[str]:
        LATEX_DIR.mkdir(parents=True, exist_ok=True)
        bases: list[str] = []
        for path in LATEX_DIR.iterdir():
            if not path.is_dir():
                continue
            name = path.name
            if (path / f"{name}.tex").exists() and (path / "project.json").exists():
                bases.append(name)
        return sorted(bases, key=str.lower)

    def _chunk_projects(self, items: list[str]) -> list[list[str]]:
        return [items[i:i + ITEMS_PER_PAGE] for i in range(0, len(items), ITEMS_PER_PAGE)] or [[]]

    def _build_project_embed(self, title: str, page_items: list[str], page: int, total: int) -> discord.Embed:
        if page_items:
            lines = [f"{index}: {name}" for index, name in page_items]
            description = "\n".join(lines)
        else:
            description = "プロジェクトはまだありません"

        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
        embed.set_footer(text=f"xで終了 {page}/{total} pages")
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

    async def _select_project_from_menu(
        self,
        ctx: commands.Context,
        *,
        title: str,
        allow_cancel: bool = True,
    ) -> str | None:
        return await self._select_name_from_menu(
            ctx,
            title=title,
            items=self._list_projects(),
            empty_message="プロジェクトはまだありません",
            allow_cancel=allow_cancel,
        )

    async def _select_base_from_menu(
        self,
        ctx: commands.Context,
        *,
        title: str,
        allow_cancel: bool = True,
    ) -> str | None:
        return await self._select_name_from_menu(
            ctx,
            title=title,
            items=self._list_bases(),
            empty_message="baseはまだありません",
            allow_cancel=allow_cancel,
        )

    async def _select_name_from_menu(
        self,
        ctx: commands.Context,
        *,
        title: str,
        items: list[str],
        empty_message: str,
        allow_cancel: bool = True,
    ) -> str | None:
        if not items:
            await ctx.reply(empty_message)
            return None

        indexed_items = list(enumerate(items, start=1))
        pages = self._chunk_projects(indexed_items)
        total = len(pages)
        current = 0

        msg = await ctx.reply(embed=self._build_project_embed(title, pages[current], current + 1, total))
        if total > 1:
            for emoji in (config.FIRST_EMOJI, config.PREV_EMOJI, config.NEXT_EMOJI, config.LAST_EMOJI):
                await msg.add_reaction(emoji)

        def reaction_check(reaction: discord.Reaction, user: discord.User | discord.Member) -> bool:
            return (
                user == ctx.author
                and reaction.message.id == msg.id
                and str(reaction.emoji) in (
                    config.FIRST_EMOJI,
                    config.PREV_EMOJI,
                    config.NEXT_EMOJI,
                    config.LAST_EMOJI,
                )
            )

        def message_check(message: discord.Message) -> bool:
            return message.author == ctx.author and message.channel == ctx.channel

        timeout = get_menu_timeout_seconds()
        while True:
            await msg.edit(embed=self._build_project_embed(title, pages[current], current + 1, total))

            tasks = [asyncio.create_task(self.bot.wait_for("message", check=message_check))]
            if total > 1:
                tasks.append(asyncio.create_task(self.bot.wait_for("reaction_add", check=reaction_check)))

            done, pending = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()

            if not done:
                await self._clear_menu_reactions(msg)
                return None

            task = done.pop()
            try:
                result = task.result()
            except Exception:
                await self._clear_menu_reactions(msg)
                return None

            if isinstance(result, tuple):
                reaction, user = result
                try:
                    await msg.remove_reaction(reaction, user)
                except Exception:
                    pass

                emoji = str(reaction.emoji)
                if emoji == config.FIRST_EMOJI:
                    current = 0
                elif emoji == config.LAST_EMOJI:
                    current = total - 1
                elif emoji == config.NEXT_EMOJI:
                    current = (current + 1) % total
                else:
                    current = (current - 1) % total
                continue

            content = result.content.strip()
            if allow_cancel and content.lower() == "x":
                await self._mark_menu_stopped(msg)
                return None

            if content.isdigit():
                selected = int(content)
                if 1 <= selected <= len(items):
                    await self._clear_menu_reactions(msg)
                    return items[selected - 1]

    async def _latex_list(self, ctx: commands.Context) -> None:
        await self._select_project_from_menu(ctx, title="LaTeX Project List")

    async def _latex_base(self, ctx: commands.Context, args: tuple[str, ...]) -> None:
        if not args:
            await ctx.reply("使用方法: c!tex base <create/edit> ...")
            return

        action = args[0].lower()
        if action == "create":
            await self._run_with_timeout(ctx, self._latex_base_create(ctx, args[1:]), LATEX_COMMAND_TIMEOUT)
            return
        if action == "edit":
            await self._run_with_timeout(ctx, self._latex_base_edit(ctx), LATEX_EDIT_TIMEOUT)
            return

        await ctx.reply("使用方法: c!tex base <create/edit> ...")

    async def _latex_create(self, ctx: commands.Context, args: tuple[str, ...]) -> None:
        if not args:
            await ctx.reply("使用方法: c!latex create [プロジェクト名]")
            return

        name = self._sanitize_project_name(" ".join(args))
        if not name:
            await ctx.reply("有効なプロジェクト名を指定してください")
            return

        project_dir = self._project_dir(name)
        main_tex = self._main_tex_path(name)
        if project_dir.exists():
            await ctx.reply("その名前のプロジェクトは既に存在します")
            return

        project_dir.mkdir(parents=True, exist_ok=False)
        main_tex.write_text(DEFAULT_TEX, encoding="utf-8")
        (project_dir / "output").mkdir(exist_ok=True)
        await ctx.reply(f"`{name}` を作成しました")

    async def _latex_base_create(self, ctx: commands.Context, args: tuple[str, ...]) -> None:
        if not args:
            await ctx.reply("使用方法: c!tex base create <名前>")
            return

        name = self._sanitize_project_name(" ".join(args))
        if not name:
            await ctx.reply("有効な名前を指定してください")
            return

        project_dir = self._project_dir(name)
        base_tex = self._base_tex_path(name)
        if project_dir.exists():
            await ctx.reply("その名前は既に使われています")
            return

        project_dir.mkdir(parents=True, exist_ok=False)
        base_tex.write_text(DEFAULT_TEX, encoding="utf-8")
        self._save_base_meta(name, {"targets": []})
        await ctx.reply(f"base `{name}` を作成しました")

    async def _latex_edit(self, ctx: commands.Context, args: tuple[str, ...]) -> None:
        project_name = self._sanitize_project_name(" ".join(args)) if args else ""
        if not project_name:
            project_name = await self._select_project_from_menu(ctx, title="Edit Project")
            if project_name is None:
                return

        main_tex = self._main_tex_path(project_name)
        if not main_tex.exists():
            await ctx.reply("指定されたプロジェクトが見つかりません")
            return

        lines = self._read_lines(main_tex)
        await self._send_tex_chunks(ctx, lines, project_name)
        prompt = await ctx.reply("編集する行番号を送信してください。例: `1` `2-3` `all`")

        line_message = await self._wait_for_same_user_message(ctx, timeout=LATEX_EDIT_TIMEOUT)
        if line_message is None:
            await self._mark_menu_stopped(prompt)
            return

        target = self._parse_edit_target(line_message.content.strip(), len(lines))
        if target is None:
            await prompt.edit(content="行指定が不正です")
            return

        start, end, with_numbers = target
        preview = self._build_edit_preview(lines, start, end, with_numbers)
        await prompt.edit(content=preview)

        edited_message = await self._wait_for_same_user_message(ctx, timeout=LATEX_EDIT_TIMEOUT)
        if edited_message is None:
            await self._mark_menu_stopped(prompt)
            return

        replacement_lines = self._normalize_message_to_lines(
            edited_message.content,
            strip_number_prefix=with_numbers,
        )
        new_lines = lines[: start - 1] + replacement_lines + lines[end:]
        self._write_lines(main_tex, new_lines)
        await ctx.reply(f"`{project_name}` を更新しました")

    async def _latex_edit_slash(self, ctx, args: tuple[str, ...]) -> None:
        project_name = self._sanitize_project_name(" ".join(args)) if args else ""
        interaction = ctx._interaction
        if project_name:
            await self._show_slash_edit_project(interaction, project_name)
            return

        projects = self._list_projects()
        if not projects:
            await ctx.reply("プロジェクトはまだありません")
            return

        view = self._UserOnlyView(ctx.author.id, timeout=get_menu_timeout_seconds())
        view.add_item(self._SlashProjectSelect(self, projects))
        note = "" if len(projects) <= 25 else "\n先頭25件だけ表示しています。"
        await ctx.reply(f"編集するプロジェクトを選択してください。{note}", view=view)

    async def _show_slash_edit_project(self, interaction: discord.Interaction, project_name: str) -> None:
        main_tex = self._main_tex_path(project_name)
        if not main_tex.exists():
            await self._edit_interaction_message(interaction, "指定されたプロジェクトが見つかりません", view=None)
            return

        lines = self._read_lines(main_tex)
        view = self._UserOnlyView(interaction.user.id, timeout=get_menu_timeout_seconds())
        view.add_item(self._SlashEditButton(self, project_name))
        await self._edit_interaction_message(
            interaction,
            self._build_slash_edit_text(project_name, lines),
            view=view,
        )

    async def _handle_slash_edit_target_submit(
        self,
        interaction: discord.Interaction,
        project_name: str,
        raw_target: str,
    ) -> None:
        main_tex = self._main_tex_path(project_name)
        if not main_tex.exists():
            await interaction.response.edit_message(content="指定されたプロジェクトが見つかりません", view=None)
            return

        lines = self._read_lines(main_tex)
        target = self._parse_edit_target(raw_target.strip(), len(lines))
        if target is None:
            await interaction.response.edit_message(content="行指定が不正です", view=None)
            return

        start, end, _ = target
        default_text = "\n".join(lines[start - 1:end])
        if len(default_text) > 4000:
            await interaction.response.edit_message(
                content="選択範囲が長すぎるため、slashの入力欄では編集できません。範囲を狭くしてください。",
                view=None,
            )
            return

        await interaction.response.send_modal(
            self._SlashEditContentModal(self, project_name, start, end, default_text)
        )

    async def _handle_slash_edit_content_submit(
        self,
        interaction: discord.Interaction,
        project_name: str,
        start: int,
        end: int,
        content: str,
    ) -> None:
        main_tex = self._main_tex_path(project_name)
        if not main_tex.exists():
            await interaction.response.edit_message(content="指定されたプロジェクトが見つかりません", view=None)
            return

        lines = self._read_lines(main_tex)
        replacement_lines = self._normalize_message_to_lines(content)
        new_lines = lines[: start - 1] + replacement_lines + lines[end:]
        self._write_lines(main_tex, new_lines)
        await interaction.response.edit_message(
            content=self._build_slash_edit_text(project_name, new_lines, header=f"`{project_name}` を更新しました"),
            view=None,
        )

    async def _edit_interaction_message(self, interaction: discord.Interaction, content: str, **kwargs) -> None:
        if interaction.response.is_done():
            await interaction.edit_original_response(content=content, **kwargs)
        else:
            await interaction.response.edit_message(content=content, **kwargs)

    def _build_slash_edit_text(
        self,
        project_name: str,
        lines: list[str],
        *,
        header: str | None = None,
    ) -> str:
        title = header or f"`{project_name}`"
        numbered = [f"{index}: {line}" for index, line in enumerate(lines, start=1)] or ["1: "]
        body = "\n".join(numbered)
        limit = MESSAGE_LIMIT - len(title) - len("\n```tex\n\n```") - 80
        truncated = ""
        if len(body) > limit:
            body = body[:limit].rstrip()
            truncated = "\n...(長いため一部だけ表示)"
        return f"{title}\n```tex\n{body}\n```{truncated}"

    async def _latex_base_edit(self, ctx: commands.Context) -> None:
        base_name = await self._select_base_from_menu(ctx, title="Base List")
        if base_name is None:
            return

        base_tex = self._base_tex_path(base_name)
        if not base_tex.exists():
            await ctx.reply("指定されたbaseが見つかりません")
            return

        lines = self._read_lines(base_tex)
        await self._send_tex_chunks(ctx, lines, base_name)
        prompt = await ctx.reply("編集する行番号を送信してください。例: `1` `2-3` `all`")

        line_message = await self._wait_for_same_user_message(ctx, timeout=LATEX_EDIT_TIMEOUT)
        if line_message is None:
            await self._mark_menu_stopped(prompt)
            return

        target = self._parse_edit_target(line_message.content.strip(), len(lines))
        if target is None:
            await prompt.edit(content="行指定が不正です")
            return

        start, end, with_numbers = target
        preview = self._build_edit_preview(lines, start, end, with_numbers)
        await prompt.edit(content=preview)

        edited_message = await self._wait_for_same_user_message(ctx, timeout=LATEX_EDIT_TIMEOUT)
        if edited_message is None:
            await self._mark_menu_stopped(prompt)
            return

        replacement_lines = self._normalize_message_to_lines(
            edited_message.content,
            strip_number_prefix=with_numbers,
        )
        new_lines = lines[: start - 1] + replacement_lines + lines[end:]
        self._write_lines(base_tex, new_lines)
        copied = self._sync_base_to_targets(base_name)
        await ctx.reply(f"base `{base_name}` を更新しました ({copied}件反映)")

    async def _latex_copy(self, ctx: commands.Context, args: tuple[str, ...]) -> None:
        if len(args) < 2:
            await ctx.reply("使用方法: c!latex copy [プロジェクト名] [複製後の名前]")
            return

        source_name = self._sanitize_project_name(args[0])
        dest_name = self._sanitize_project_name(" ".join(args[1:]))
        if not source_name or not dest_name:
            await ctx.reply("有効なプロジェクト名を指定してください")
            return

        source_dir = self._project_dir(source_name)
        dest_dir = self._project_dir(dest_name)
        if not source_dir.exists() or not self._main_tex_path(source_name).exists():
            await ctx.reply("元のプロジェクトが見つかりません")
            return
        if dest_dir.exists():
            await ctx.reply("複製先の名前は既に使用されています")
            return

        shutil.copytree(source_dir, dest_dir)
        await ctx.reply(f"`{source_name}` を `{dest_name}` に複製しました")

    async def _latex_delete(self, ctx: commands.Context, args: tuple[str, ...]) -> None:
        project_name = self._sanitize_project_name(" ".join(args)) if args else ""
        if not project_name:
            project_name = await self._select_project_from_menu(ctx, title="Delete Project")
            if project_name is None:
                return

        project_dir = self._project_dir(project_name)
        if not project_dir.exists():
            await ctx.reply("指定されたプロジェクトが見つかりません")
            return

        shutil.rmtree(project_dir)
        await ctx.reply(f"`{project_name}` を削除しました")

    async def _latex_image(self, ctx: commands.Context, payload: str) -> None:
        if Image is None or ImageChops is None:
            await ctx.reply("`tex image` には `pdf2image` と `Pillow` のインストールが必要です")
            return

        tex_body = self._extract_tex_body(payload)
        if not tex_body:
            prompt = await ctx.reply("tex文を入力してください(xでキャンセル)")
            message = await self._wait_for_same_user_message(ctx, timeout=LATEX_EDIT_TIMEOUT)
            if message is None:
                await self._mark_menu_stopped(prompt)
                return
            if message.content.strip().lower() == "x":
                await self._mark_menu_stopped(prompt)
                return
            tex_body = self._extract_tex_body(message.content)
            if not tex_body:
                await ctx.reply("texコードブロックがありません")
                return

        stem = "image"
        tex_path = TEMP_LATEX_DIR / f"{stem}.tex"
        pdf_path = TEMP_LATEX_DIR / f"{stem}.pdf"
        png_path = TEMP_LATEX_DIR / f"{stem}.png"
        self._cleanup_temp_latex_files(stem)

        tex_source = IMAGE_TEX_TEMPLATE.replace(IMAGE_TEX_BODY_PLACEHOLDER, tex_body)
        tex_path.write_text(tex_source, encoding="utf-8")

        status_message = await ctx.reply("画像を生成しています...")
        success, detail = await self._compile_named_pdf(TEMP_LATEX_DIR, stem)
        if not success:
            await status_message.edit(
                content=f"画像生成に失敗しました\n```text\n{self._format_error_tail(detail)}\n```"
            )
            self._cleanup_temp_latex_files(stem)
            return

        if not pdf_path.exists():
            await status_message.edit(content="画像生成に失敗しました\n`image.pdf` が見つかりません")
            self._cleanup_temp_latex_files(stem)
            return

        try:
            rendered = self._render_pdf_to_image(pdf_path, png_path)
        except Exception as exc:
            await status_message.edit(
                content=f"画像化に失敗しました\n```text\n{self._format_error_tail(str(exc))}\n```"
            )
            self._cleanup_temp_latex_files(stem)
            return

        if getattr(ctx, "is_slash", False):
            await status_message.edit(content="完了しました", attachments=[discord.File(rendered)])
        else:
            await status_message.edit(content="完了しました")
            await ctx.reply(file=discord.File(rendered))
        self._cleanup_temp_latex_files(stem)

    async def _latex_settings(self, ctx: commands.Context, args: tuple[str, ...]) -> None:
        if not args:
            await ctx.reply("使用方法: c!latex settings [name]")
            return

        option = args[0].lower()
        if option != "name":
            await ctx.reply("使用方法: c!latex settings [name]")
            return

        project_name = await self._select_project_from_menu(ctx, title="Rename Project")
        if project_name is None:
            return

        prompt = await ctx.reply("変更後のファイル名(.tex不要)を送信してください")
        reply = await self._wait_for_same_user_message(ctx)
        if reply is None:
            await self._mark_menu_stopped(prompt)
            return

        new_name = self._sanitize_project_name(reply.content.strip())
        if not new_name:
            await prompt.edit(content="有効なファイル名を指定してください")
            return
        if new_name == project_name:
            await prompt.edit(content="同じ名前です")
            return

        src_dir = self._project_dir(project_name)
        dst_dir = self._project_dir(new_name)
        if dst_dir.exists():
            await prompt.edit(content="その名前は既に使われています")
            return

        src_dir.rename(dst_dir)
        await prompt.edit(content=f"`{project_name}` を `{new_name}` に変更しました")

    async def _latex_output(self, ctx: commands.Context, args: tuple[str, ...]) -> None:
        project_name = self._sanitize_project_name(" ".join(args)) if args else ""
        if not project_name:
            project_name = await self._select_project_from_menu(ctx, title="Output Project")
            if project_name is None:
                return

        project_dir = self._project_dir(project_name)
        main_tex = self._main_tex_path(project_name)
        if not main_tex.exists():
            await ctx.reply("指定されたプロジェクトが見つかりません")
            return

        output_dir = project_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        status_message = await ctx.reply("pdfに変換しています....")
        success, detail = await self._compile_pdf(project_dir, output_dir)
        if not success:
            await status_message.edit(
                content=f"pdf変換に失敗しました\n```text\n{self._format_error_tail(detail)}\n```"
            )
            return

        pdf_path = output_dir / "main.pdf"
        if not pdf_path.exists():
            await status_message.edit(content="pdf変換に失敗しました\n`main.pdf` が見つかりません")
            return

        await status_message.edit(content="完了しました")
        await ctx.reply(file=discord.File(pdf_path))

    async def _latex_install(self, ctx: commands.Context, args: tuple[str, ...]) -> None:
        project_name = self._sanitize_project_name(" ".join(args)) if args else ""
        if not project_name:
            project_name = await self._select_project_from_menu(ctx, title="Install Project")
            if project_name is None:
                return

        main_tex = self._main_tex_path(project_name)
        if not main_tex.exists():
            await ctx.reply("指定されたプロジェクトが見つかりません")
            return

        base_name = await self._select_base_from_menu(ctx, title="Base List")
        if base_name is None:
            return

        base_tex = self._base_tex_path(base_name)
        if not base_tex.exists():
            await ctx.reply("指定されたbaseが見つかりません")
            return

        shutil.copyfile(base_tex, main_tex)
        self._register_base_target(base_name, self._project_relative_dir(project_name))
        await ctx.reply(f"base `{base_name}` を `{project_name}` に適用しました")

    def _read_lines(self, path: Path) -> list[str]:
        text = path.read_text(encoding="utf-8")
        return text.splitlines()

    def _write_lines(self, path: Path, lines: list[str]) -> None:
        text = "\n".join(lines)
        if lines:
            text += "\n"
        path.write_text(text, encoding="utf-8")

    async def _send_tex_chunks(self, ctx: commands.Context, lines: list[str], project_name: str) -> None:
        numbered = [f"{index}: {line}" for index, line in enumerate(lines, start=1)]
        if not numbered:
            numbered = ["1: "]

        chunks: list[str] = []
        current = ""
        for line in numbered:
            candidate = f"{current}\n{line}".strip("\n") if current else line
            wrapped = f"```tex\n{candidate}\n```"
            if len(wrapped) > MESSAGE_LIMIT and current:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)

        for index, chunk in enumerate(chunks, start=1):
            title = f"`{project_name}`" if len(chunks) == 1 else f"`{project_name}` ({index}/{len(chunks)})"
            await ctx.reply(f"{title}\n```tex\n{chunk}\n```")

    def _parse_edit_target(self, raw: str, total_lines: int) -> tuple[int, int, bool] | None:
        content = raw.strip().lower()
        if not content:
            return None

        if total_lines <= 0:
            total_lines = 1

        if content == "all":
            return (1, total_lines, False)

        if re.fullmatch(r"\d+", content):
            line_no = int(content)
            if 1 <= line_no <= total_lines:
                return (line_no, line_no, True)
            return None

        match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", content)
        if match is None:
            return None

        start = int(match.group(1))
        end = int(match.group(2))
        if start > end or start < 1 or end > total_lines:
            return None
        return (start, end, False)

    def _build_edit_preview(self, lines: list[str], start: int, end: int, with_numbers: bool) -> str:
        selected = lines[start - 1:end]
        if with_numbers:
            if selected == "":
                body = "\n".join("　")
            else:
                body = "\n".join(selected)
            return f"{start}行目\n```tex\n{body}\n```\n編集したテキストを送信してください"

        body = "\n".join(selected)
        return f"{start}-{end}行目\n```tex\n{body}\n```\n編集したテキストを送信してください"

    async def _wait_for_same_user_message(
        self,
        ctx: commands.Context,
        *,
        timeout: float | None = None,
    ) -> discord.Message | None:
        def check(message: discord.Message) -> bool:
            return message.author == ctx.author and message.channel == ctx.channel

        try:
            wait_timeout = get_menu_timeout_seconds() if timeout is None else timeout
            return await self.bot.wait_for("message", timeout=wait_timeout, check=check)
        except Exception:
            return None

    def _format_error_tail(self, detail: str) -> str:
        text = (detail or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return "詳細ログが取得できませんでした"
        if len(text) <= LATEX_ERROR_SNIPPET_LIMIT:
            return text

        tail = text[-LATEX_ERROR_SNIPPET_LIMIT:]
        newline_index = tail.find("\n")
        if 0 <= newline_index < 200:
            tail = tail[newline_index + 1 :]
        return "...(末尾抜粋)\n" + tail

    def _extract_tex_body(self, raw: str) -> str:
        text = (raw or "").strip()
        if not text:
            return ""
        block = re.search(r"```(?:tex)?\s*([\s\S]*?)```", text, re.IGNORECASE)
        if block is not None:
            text = block.group(1).strip()
        return self._normalize_image_tex_body(text)

    def _normalize_image_tex_body(self, text: str) -> str:
        return LATEX_LINEBREAK_DIMENSION_RE.sub(r"\\\\[\1]", text)

    def _normalize_message_to_lines(
        self,
        content: str,
        *,
        strip_number_prefix: bool = False,
    ) -> list[str]:
        text = content.strip()
        block = re.fullmatch(r"```(?:tex)?\n?([\s\S]*?)```", text)
        if block is not None:
            text = block.group(1)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        if strip_number_prefix:
            lines = [re.sub(r"^\s*\d+\s*:\s?", "", line) for line in lines]
        return lines

    async def _compile_named_pdf(self, work_dir: Path, stem: str) -> tuple[bool, str]:
        try:
            lua_command = await asyncio.create_subprocess_exec(
                "lualatex",
                "--interaction=nonstopmode",
                "--halt-on-error",
                f"--output-directory={work_dir}",
                f"{stem}.tex",
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            return False, "lualatex が見つかりません"

        lua_stdout, _ = await lua_command.communicate()
        lua_text = lua_stdout.decode("utf-8", errors="replace")
        if lua_command.returncode != 0:
            return False, lua_text

        pdf_path = work_dir / f"{stem}.pdf"
        if not pdf_path.exists():
            return False, f"{stem}.pdf が生成されませんでした"
        return True, lua_text
    
    async def _render_pdf_to_image(self, pdf_path: Path, output_path: Path) -> Path:
        temp_base = output_path.with_suffix("")
    
        proc = await asyncio.create_subprocess_exec(
            "pdftoppm",
            "-png",
            "-gray",
            "-singlefile",
            "-r",
            str(IMAGE_RENDER_DPI),
            str(pdf_path),
            str(temp_base),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    
        _, stderr = await proc.communicate()
    
        if proc.returncode != 0:
            raise RuntimeError(
                stderr.decode("utf-8", errors="ignore")
            )
    
        generated_path = temp_base.with_suffix(".png")
    
        if not generated_path.exists():
            raise RuntimeError("PDF から画像を生成できませんでした")
    
        page = Image.open(generated_path)
    
        cropped = self._crop_content(page)
        if cropped is None:
            raise RuntimeError("PDFに画像化できる本文がありません")
    
        margin_px = round((IMAGE_MARGIN_CM / 2.54) * IMAGE_RENDER_DPI)
        canvas = self._add_margin(cropped, margin_px) if margin_px > 0 else cropped
    
        canvas.save(output_path, optimize=True)
    
        if generated_path.exists():
            generated_path.unlink()
    
        return output_path

    def _crop_content(self, image: Image.Image) -> Image.Image | None:
        rgb = image.convert("RGB")
        background = Image.new("RGB", rgb.size, "white")
        diff = ImageChops.difference(rgb, background)
        bbox = diff.getbbox()
        if bbox is None:
            return None
        return rgb.crop(bbox)

    def _add_margin(self, image: Image.Image, margin_px: int) -> Image.Image:
        canvas = Image.new(
            "RGB",
            (image.width + margin_px * 2, image.height + margin_px * 2),
            "white",
        )
        canvas.paste(image, (margin_px, margin_px))
        return canvas

    def _cleanup_temp_latex_files(self, stem: str) -> None:
        for path in TEMP_LATEX_DIR.glob(f"{stem}.*"):
            try:
                if path.is_file():
                    path.unlink()
            except Exception:
                pass

    def _load_base_meta(self, name: str) -> dict:
        path = self._base_json_path(name)
        if not path.exists():
            return {"targets": []}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {"targets": []}
        if not isinstance(data, dict):
            return {"targets": []}
        targets = data.get("targets", [])
        if not isinstance(targets, list):
            targets = []
        cleaned = [str(target).replace("\\", "/") for target in targets if str(target).strip()]
        return {"targets": cleaned}

    def _save_base_meta(self, name: str, data: dict) -> None:
        path = self._base_json_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _project_relative_dir(self, name: str) -> str:
        return f"latex/{name}"

    def _target_main_tex_from_relative(self, relative_dir: str) -> Path:
        normalized = relative_dir.replace("\\", "/").strip("/")
        return BASE_DIR / "data" / Path(normalized) / "main.tex"

    def _register_base_target(self, base_name: str, relative_dir: str) -> None:
        data = self._load_base_meta(base_name)
        targets = data.get("targets", [])
        if relative_dir not in targets:
            targets.append(relative_dir)
        data["targets"] = targets
        self._save_base_meta(base_name, data)

    def _sync_base_to_targets(self, base_name: str) -> int:
        base_tex = self._base_tex_path(base_name)
        if not base_tex.exists():
            return 0

        data = self._load_base_meta(base_name)
        copied = 0
        valid_targets: list[str] = []
        for relative_dir in data.get("targets", []):
            target_main_tex = self._target_main_tex_from_relative(relative_dir)
            if not target_main_tex.exists():
                continue
            shutil.copyfile(base_tex, target_main_tex)
            copied += 1
            valid_targets.append(relative_dir)

        data["targets"] = valid_targets
        self._save_base_meta(base_name, data)
        return copied

    async def _compile_pdf(self, project_dir: Path, output_dir: Path) -> tuple[bool, str]:
        for target in output_dir.iterdir():
            if target.is_file():
                target.unlink()

        try:
            lua_command = await asyncio.create_subprocess_exec(
                "lualatex",
                "--interaction=nonstopmode",
                "--halt-on-error",
                f"--output-directory={output_dir}",
                "main.tex",
                cwd=str(project_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            return False, "lualatex が見つかりません"
        try:
            lua_stdout, _ = await asyncio.wait_for(
                lua_command.communicate(),
                timeout=25,
            )
        except asyncio.TimeoutError:
            lua_command.kill()
            return False, "LuaLaTeXの実行がタイムアウトしました"
        lua_text = lua_stdout.decode("utf-8", errors="replace")
        if lua_command.returncode != 0:
            return False, lua_text

        pdf_path = output_dir / "main.pdf"
        if not pdf_path.exists():
            return False, "main.pdf が生成されませんでした"

        return True, lua_text


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Latex(bot))
    print("latex.py cog loaded")
