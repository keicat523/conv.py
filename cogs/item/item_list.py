from discord.ext import commands
from discord import app_commands
import discord
import json
from pathlib import Path

from admin_ids import ADMIN_IDS
from utils.inventory_manager import (
    load_rarity_map,
    normalize_rarity,
    rarity_display_name,
    get_user_rarity_inputs
)
import config

# project_root
BASE_DIR = Path(__file__).resolve().parents[2]
ITEM_DATA_DIR = BASE_DIR / "data" / "item_data"
MAX_ITEMS_PER_PAGE = 30
MAX_CHARS_PER_PAGE = 2000

"◀️"
"▶️"


class ItemList(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rarity_map = load_rarity_map()
        self.rarities = list(self.rarity_map.keys())

    def is_admin(self, user_id: int) -> bool:
        return user_id in ADMIN_IDS

    def _item_line(self, item):
        item_id, name = item
        return f"**{name}** ID:{item_id}"

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
        emoji = config.RARITY_EMOJI.get(rarity, "")
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

    def build_item_list_embed(self, rarity, items, rarity_page, rarity_total, page, total):
        header = self._header_text(rarity, rarity_page, rarity_total)
        if not items:
            items_text = "No items."
        else:
            items_text = "\n".join(self._item_line(item) for item in items)
        embed = discord.Embed(
            title="Item List",
            description=f"{header}\n\n{items_text}",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Page {page}/{total}")
        return embed

    def load_item_list_pages(self):
        pages = {}

        for rarity, filename in self.rarity_map.items():
            path = ITEM_DATA_DIR / filename
            if not path.exists():
                pages[rarity] = []
                continue

            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            items = [
                (item_id, item.get("name", "Unknown"))
                for item_id, item in data.items()
            ]

            def _sort_key(entry):
                item_id = entry[0]
                try:
                    return (0, int(item_id))
                except ValueError:
                    return (1, str(item_id))

            items.sort(key=_sort_key)
            pages[rarity] = items

        return pages
    async def send_item_list(self, send_func, user, start_rarity=None):
        item_pages = self.load_item_list_pages()

        pages = []
        for rarity in self.rarities:
            items = item_pages.get(rarity, [])
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
        embed = self.build_item_list_embed(rarity, items, rarity_page, rarity_total, index + 1, total)

        msg = await send_func(embed=embed)

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
                    "reaction_add", timeout=60, check=check
                )
            except:
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
            embed = self.build_item_list_embed(rarity, items, rarity_page, rarity_total, index + 1, total)

            await msg.edit(embed=embed)
    @commands.command(name="item_list")
    async def item_list(self, ctx, rarity: str | None = None):
        if not self.is_admin(ctx.author.id):
            await ctx.reply("権限がありません")
            return

        if rarity:
            rarity = normalize_rarity(rarity)
            if rarity not in self.rarities:
                await ctx.reply(
                    "レア度が不正です。\n"
                    f"使用可能: `{', '.join(get_user_rarity_inputs())}`"
                )
                return

        await self.send_item_list(ctx.reply, ctx.author, rarity)

    @app_commands.command(
        name="item_list",
        description="データ上のアイテム一覧を表示します（管理者のみ）"
    )
    async def item_list_slash(self, interaction: discord.Interaction, rarity: str | None = None):
        if not self.is_admin(interaction.user.id):
            await interaction.response.send_message(
                "権限がありません",
                ephemeral=True
            )
            return

        if rarity:
            rarity = normalize_rarity(rarity)
            if rarity not in self.rarities:
                await interaction.response.send_message(
                    "レア度が不正です。\n"
                    f"使用可能: `{', '.join(get_user_rarity_inputs())}`",
                    ephemeral=True
                )
                return

        await interaction.response.send_message(
            embed=discord.Embed(description="読み込み中...")
        )
        msg = await interaction.original_response()
        await self.send_item_list(lambda **kw: msg.edit(**kw), interaction.user, rarity)


async def setup(bot):
    await bot.add_cog(ItemList(bot))
    print("item_list.py cog loaded")
