from discord.ext import commands
from discord import app_commands
import discord
import json
from pathlib import Path

import config
from utils.timeout_manager import get_menu_timeout_seconds
from utils.inventory_manager import (
    get_inventory_with_names,
    load_rarity_map,
    normalize_rarity,
    rarity_display_name,
    get_user_rarity_inputs
)

# ページ分割 / 表示設定
# =========================
# 設定
# =========================
RARITY_EMOJI = config.RARITY_EMOJI
MAX_ITEMS_PER_PAGE = 30
MAX_CHARS_PER_PAGE = 2000

# project_root 基準
BASE_DIR = Path(__file__).resolve().parents[2]
ATTR_DIR = BASE_DIR / "data" / "item_attribute"


# =========================
# 詳細表示 View
# =========================
class InventoryDetailView(discord.ui.View):
    def __init__(self, user, rarity, items):
        super().__init__(timeout=60)
        self.user = user
        self.rarity = rarity
        self.items = items

        options = [
            discord.SelectOption(
                label=name,
                description=f"ID:{item_id} x{amount}",
                value=str(item_id)
            )
            for item_id, name, amount in items
        ]

        self.select = discord.ui.Select(
            placeholder="詳細を表示するアイテムを選択",
            options=options
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user.id

    async def on_select(self, interaction: discord.Interaction):
        item_id = self.select.values[0]

        # ===== 属性データ読み込み =====
        attr_name = rarity_display_name(self.rarity)
        attr_path = ATTR_DIR / f"{attr_name}.json"
        try:
            with open(attr_path, encoding="utf-8") as f:
                attr_map = json.load(f)

            description = (
                attr_map
                .get(item_id, {})
                .get("description", "説明がありません")
            )

        except FileNotFoundError:
            description = "説明データが見つかりません"

        # ===== 名前と個数 =====
        name = "Unknown"
        amount = 0
        for i, n, a in self.items:
            if str(i) == item_id:
                name = n
                amount = a
                break

        display_name = rarity_display_name(self.rarity)
        embed = discord.Embed(
            title=name,
            description=(
                f"{description}\n\n"
                f"**所持数:** {amount}\n"
                f"{rarity_display_name(self.rarity)}**:** {display_name.upper()}"
            ),
            color=discord.Color.blue()
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )


# =========================
# Inventory Cog
# =========================
class Inventory(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rarity_map = load_rarity_map()
        self.rarities = list(self.rarity_map.keys())

    def _item_line(self, item):
        item_id, name, amount = item
        return f"**{name}** ID:{item_id} x{amount}"

    # メニュータイムアウト時にリアクションを掃除
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

    # 件数と文字数でアイテムをページ分割
    def _chunk_items(self, items, max_items, max_chars):
        pages = []
        current = []
        current_len = 0
        for item in items:
            line = self._item_line(item)
            line_len = len(line) + (1 if current else 0)
            if current and (len(current) >= max_items or current_len + line_len > max_chars):
                pages.append(current)
                current = []
                current_len = 0
            current.append(item)
            current_len += line_len
        if not pages and not current:
            return [[]]
        if current:
            pages.append(current)
        return pages

    def _header_text(self, rarity, rarity_page, rarity_total):
        emoji = RARITY_EMOJI.get(rarity, "")
        display_name = rarity_display_name(rarity)
        return (
            f"{emoji} **{display_name.upper()}**\n"
            f"{display_name.upper()}: {rarity_page}/{rarity_total}"
        )

    def _build_pages_for_rarity(self, rarity, items):
        pages = self._chunk_items(items, MAX_ITEMS_PER_PAGE, MAX_CHARS_PER_PAGE)
        while True:
            total = len(pages)
            new_pages = []
            changed = False
            for idx, page_items in enumerate(pages, start=1):
                header = self._header_text(rarity, idx, total)
                limit = MAX_CHARS_PER_PAGE - len(header) - 2
                if limit < 1:
                    limit = 1
                sub_pages = self._chunk_items(page_items, MAX_ITEMS_PER_PAGE, limit)
                if len(sub_pages) != 1:
                    changed = True
                new_pages.extend(sub_pages)
            pages = new_pages
            if not changed:
                break
        return pages

    def build_embed(self, user, rarity, items, rarity_page, rarity_total, page, total):
        header = self._header_text(rarity, rarity_page, rarity_total)
        if not items:
            items_text = "アイテムはありません"
        else:
            items_text = "\n".join(self._item_line(item) for item in items)
        embed = discord.Embed(
            title=f"{user.name} のインベントリ",
            description=f"{header}\n\n{items_text}",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Page {page}/{total}")
        return embed
    # リアクション移動付きのインベントリメインループ
    async def send_inventory(self, send_func, user, start_rarity=None):
        inventory = get_inventory_with_names(user.id)

        pages = []
        for rarity in self.rarities:
            items = inventory.get(rarity, [])
            rarity_pages = self._build_pages_for_rarity(rarity, items)
            for i, chunk in enumerate(rarity_pages, start=1):
                pages.append((rarity, chunk, i, len(rarity_pages)))

        total = len(pages)
        index = 0

        if start_rarity:
            for i, (r, _items, _rp, _rt) in enumerate(pages):
                if r == start_rarity:
                    index = i
                    break

        rarity, items, rarity_page, rarity_total = pages[index]
        embed = self.build_embed(user, rarity, items, rarity_page, rarity_total, index + 1, total)
        view = InventoryDetailView(user, rarity, items) if items else None

        msg = await send_func(embed=embed, view=view)

        if total <= 1:
            return

        await msg.add_reaction(config.FIRST_EMOJI)
        await msg.add_reaction(config.PREV_EMOJI)
        await msg.add_reaction(config.NEXT_EMOJI)
        await msg.add_reaction(config.LAST_EMOJI)

        def check(r, u):
            return u == user and r.message.id == msg.id and str(r.emoji) in (
                config.FIRST_EMOJI, config.PREV_EMOJI, config.NEXT_EMOJI, config.LAST_EMOJI
            )

        while True:
            try:
                reaction, u = await self.bot.wait_for(
                    "reaction_add", timeout=get_menu_timeout_seconds(), check=check
                )
            except TimeoutError:
                await self._clear_menu_reactions(msg)
                break
            except Exception:
                break

            await msg.remove_reaction(reaction, u)

            if str(reaction.emoji) == config.FIRST_EMOJI:
                index = 0
            elif str(reaction.emoji) == config.LAST_EMOJI:
                index = total - 1
            elif str(reaction.emoji) == config.NEXT_EMOJI:
                index = (index + 1) % total
            else:
                index = (index - 1) % total

            rarity, items, rarity_page, rarity_total = pages[index]
            embed = self.build_embed(user, rarity, items, rarity_page, rarity_total, index + 1, total)
            view = InventoryDetailView(user, rarity, items) if items else None

            await msg.edit(embed=embed, view=view)

        return
    @commands.command(name="inventory", aliases=["inv"])
    async def inventory(self, ctx, rarity: str | None = None):
        if rarity is not None:
            rarity = normalize_rarity(rarity)
            if rarity is None:
                allow = ", ".join(get_user_rarity_inputs())
                await ctx.reply(
                    f"レアリティが不正です\n使用可能: `{allow}`"
                )
                return
        await self.send_inventory(ctx.reply, ctx.author, rarity)

    @app_commands.command(name="inventory", description="所持アイテムを表示します")
    async def inventory_slash(self, interaction: discord.Interaction, rarity: str | None = None):
        if rarity is not None:
            rarity = normalize_rarity(rarity)
            if rarity is None:
                allow = ", ".join(get_user_rarity_inputs())
                await interaction.response.send_message(
                    f"レアリティが不正です\n使用可能: `{allow}`",
                    ephemeral=True
                )
                return
        await interaction.response.send_message(
            embed=discord.Embed(description="📦 インベントリを読み込み中...")
        )
        msg = await interaction.original_response()
        await self.send_inventory(lambda **kw: msg.edit(**kw), interaction.user, rarity)

async def setup(bot):
    await bot.add_cog(Inventory(bot))
    print("inventory.py cog loaded")
