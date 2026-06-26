from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

import admin_ids
import config
from utils.help_loader import load_embed_from_json
from utils.timeout_manager import get_menu_timeout_seconds

# helpデータファイル
BASE_DIR = Path(__file__).resolve().parent.parent.parent
HELP_COMMANDS_DIR = BASE_DIR / "data" / "commands" / "help"
HELP_PAGES_DIR = HELP_COMMANDS_DIR / "help_pages"

# ページ移動用絵文字
FIRST_EMOJI = config.FIRST_EMOJI
PREV_EMOJI = config.PREV_EMOJI
NEXT_EMOJI = config.NEXT_EMOJI
LAST_EMOJI = config.LAST_EMOJI


def _is_admin_page(path: Path) -> bool:
    rel = path.relative_to(HELP_PAGES_DIR)
    rel_parts = [p.lower() for p in rel.parts]
    return "admin" in rel_parts or path.stem.lower() == "admin"


# Slash用ページ移動View
class HelpPageView(discord.ui.View):
    def __init__(self, pages: list[Path], author_id: int):
        super().__init__(timeout=get_menu_timeout_seconds())
        self.pages = pages
        self.index = 0
        self.author_id = author_id

    def get_embed(self):
        embed = load_embed_from_json(str(self.pages[self.index]))
        embed.set_footer(text=f"Page {self.index + 1}/{len(self.pages)}")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("このhelpはあなた専用です。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⏮", style=discord.ButtonStyle.secondary)
    async def first(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = 0
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index - 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index + 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.secondary)
    async def last(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = len(self.pages) - 1
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.page_files: list[Path] = []
        self.command_help_map: dict[str, Path] = {}
        self._refresh_help_sources()

    def _refresh_help_sources(self) -> None:
        self.page_files = self._scan_help_pages()
        self.command_help_map = self._scan_command_help_files()

    def _scan_help_pages(self) -> list[Path]:
        if not HELP_PAGES_DIR.exists():
            return []
        files = [p for p in HELP_PAGES_DIR.rglob("*.json")]
        return sorted(files, key=lambda p: str(p.relative_to(HELP_PAGES_DIR)).lower())

    def _scan_command_help_files(self) -> dict[str, Path]:
        mapping: dict[str, Path] = {}
        if not HELP_COMMANDS_DIR.exists():
            return mapping

        for path in HELP_COMMANDS_DIR.rglob("*.json"):
            try:
                path.relative_to(HELP_PAGES_DIR)
                continue
            except ValueError:
                pass
            mapping[path.stem.lower()] = path
        return mapping

    def _build_pages_for_user(self, user_id: int) -> list[Path]:
        is_admin = user_id in admin_ids.ADMIN_IDS
        pages: list[Path] = []
        for path in self.page_files:
            if _is_admin_page(path) and not is_admin:
                continue
            pages.append(path)
        return pages

    def _resolve_command_help_path(self, command_name: str | None) -> Path | None:
        if not command_name:
            return None
        key = command_name.lower().strip()
        if not key:
            return None

        # ファイル移動直後でも反映されるよう都度更新
        self.command_help_map = self._scan_command_help_files()

        # 1) 入力文字列をそのままキーとして検索
        direct = self.command_help_map.get(key)
        if direct:
            return direct

        # 2) コマンド解決して、正規名/エイリアスで検索
        cmd = self.bot.get_command(key)
        if cmd:
            candidates = [cmd.name.lower(), *[a.lower() for a in cmd.aliases]]
            for name in candidates:
                path = self.command_help_map.get(name)
                if path:
                    return path

        return None

    async def _clear_menu_reactions(self, msg: discord.Message) -> None:
        try:
            await msg.clear_reactions()
        except Exception:
            for emoji in (FIRST_EMOJI, PREV_EMOJI, NEXT_EMOJI, LAST_EMOJI):
                try:
                    await msg.clear_reaction(emoji)
                except Exception:
                    pass

    # Prefixコマンド: c!help
    @commands.command(name="help")
    async def help_prefix(self, ctx, command_name: str | None = None):
        if command_name is not None:
            path = self._resolve_command_help_path(command_name)
            if path is None or not path.exists():
                await ctx.reply("そのコマンドのhelpは見つかりません。")
                return
            await ctx.reply(embed=load_embed_from_json(str(path)))
            return

        self.page_files = self._scan_help_pages()
        pages = self._build_pages_for_user(ctx.author.id)
        if not pages:
            await ctx.reply("helpページが見つかりません。")
            return

        index = 0

        def make_embed():
            embed = load_embed_from_json(str(pages[index]))
            embed.set_footer(
                text=f"Page {index + 1}/{len(pages)} ・ {FIRST_EMOJI} {PREV_EMOJI} {NEXT_EMOJI} {LAST_EMOJI} で移動"
            )
            return embed

        msg = await ctx.reply(embed=make_embed())

        if len(pages) <= 1:
            return

        await msg.add_reaction(FIRST_EMOJI)
        await msg.add_reaction(PREV_EMOJI)
        await msg.add_reaction(NEXT_EMOJI)
        await msg.add_reaction(LAST_EMOJI)

        def check(reaction, user):
            return (
                user == ctx.author
                and reaction.message.id == msg.id
                and str(reaction.emoji) in (FIRST_EMOJI, PREV_EMOJI, NEXT_EMOJI, LAST_EMOJI)
            )

        while True:
            try:
                reaction, user = await self.bot.wait_for(
                    "reaction_add",
                    timeout=get_menu_timeout_seconds(),
                    check=check,
                )
            except TimeoutError:
                await self._clear_menu_reactions(msg)
                break
            except Exception:
                break

            await msg.remove_reaction(reaction, user)

            if str(reaction.emoji) == FIRST_EMOJI:
                index = 0
            elif str(reaction.emoji) == LAST_EMOJI:
                index = len(pages) - 1
            elif str(reaction.emoji) == NEXT_EMOJI:
                index = (index + 1) % len(pages)
            else:
                index = (index - 1) % len(pages)

            await msg.edit(embed=make_embed())

    @app_commands.command(
        name="help",
        description="コマンドの使い方を表示します",
    )
    @app_commands.describe(
        command="詳しく見たいコマンド名",
    )
    async def help_slash(
        self,
        interaction: discord.Interaction,
        command: str | None = None,
    ):
        if command is not None:
            path = self._resolve_command_help_path(command)
            if path is None or not path.exists():
                await interaction.response.send_message(
                    "そのコマンドのhelpは見つかりません。",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(embed=load_embed_from_json(str(path)))
            return

        self.page_files = self._scan_help_pages()
        pages = self._build_pages_for_user(interaction.user.id)
        if not pages:
            await interaction.response.send_message("helpページが見つかりません。", ephemeral=True)
            return

        view = HelpPageView(pages, interaction.user.id)
        await interaction.response.send_message(embed=view.get_embed(), view=view)


async def setup(bot):
    await bot.add_cog(Help(bot))
    print("help.py cog loaded")
