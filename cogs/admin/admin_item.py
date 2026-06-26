from discord.ext import commands
from discord import app_commands
import discord

from utils.inventory_manager import (
    add_item,
    remove_item,
    create_item,
    item_exists,
    load_rarity_map,
    normalize_rarity,
    get_user_rarity_inputs
)

# ================================
# Item Admin Cog
# ================================
class ItemAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.rarity_map = load_rarity_map()
        self.rarities = list(self.rarity_map.keys())


    # =========================
    # エラー文整形
    # =========================
    def error_message(self, err: str) -> str:
        return {
            "invalid_amount": "❌ 数量は 1 以上で指定してください",
            "item_not_found": "❌ そのアイテムは存在しません",
            "not_owned": "❌ ユーザーはそのアイテムを所持していません",
            "not_enough": "❌ 所持数が足りません",
        }.get(err, f"❌ 不明なエラー: `{err}`")

    # ==================================================
    # create_item (prefix)
    # ==================================================
    @commands.command(name="createitem", aliases=["citem"])
    async def create_item_cmd(
        self,
        ctx: commands.Context,
        rarity: str,
        name: str,
        *,
        description: str
    ):
        ok, err, item_id = create_item(rarity, name, description)

        if ok:
            rarity_key = normalize_rarity(rarity)
            await ctx.reply(
                f"アイテム作成に成功しました\n"
                f"ID: `{item_id}` / rarity: `{rarity_key}` / name: `{name}` / description : `{description}`"
            )
            return

        if err == "invalid_rarity":
            await ctx.reply(
                "未知のレア度です\n"
                f"使用可能なレア度: `{', '.join(get_user_rarity_inputs())}`"
            )
            return
        if err == "invalid_name":
            await ctx.reply("名前を指定してください")
            return
        if err == "invalid_description":
            await ctx.reply("説明文を指定してください")
            return

        await ctx.reply(self.error_message(err))

    @create_item_cmd.error
    async def create_item_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                "引数が不足しています\n"
                "使用方法: `c!citem rarity <name> <description>`\n"
                "例: `c!citem normal n3 this is a normal item`"
            )
            return
        raise error

    # ==================================================
    # add_item (prefix)
    # ==================================================
    @commands.command(name="add_item")
    async def add_item_cmd(
        self,
        ctx: commands.Context,
        member: discord.Member,
        item_id: str,
        rarity: str,
        amount: int = 1
    ):
        item_id = str(item_id)
        rarity = normalize_rarity(rarity)

        if rarity not in self.rarities:
            await ctx.reply(
                "❌ レアリティが不正です\n"
                f"使用可能: `{', '.join(get_user_rarity_inputs())}`"
            )
            return

        if not item_exists(item_id, rarity):
            await ctx.reply("❌ 指定されたアイテムは存在しません")
            return

        ok, err = add_item(member.id, item_id, rarity, amount)

        if ok:
            await ctx.reply(
                f"✅ `{member.display_name}` に "
                f"`{item_id}`（{rarity}）×{amount} を追加しました"
            )
        else:
            await ctx.reply(self.error_message(err))

    # ===== add_item error =====
    @add_item_cmd.error
    async def add_item_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                "❌ 引数が足りません\n"
                "**形式:**\n"
                "`c!add_item @user item_id rarity [amount]`\n"
                "**例:**\n"
                "`c!add_item @user 1 normal 2`"
            )
            return

        if isinstance(error, commands.BadArgument):
            await ctx.reply("❌ 引数の型が正しくありません")
            return

        raise error

    # ==================================================
    # remove_item (prefix)
    # ==================================================
    @commands.command(name="remove_item")
    async def remove_item_cmd(
        self,
        ctx: commands.Context,
        member: discord.Member,
        item_id: str,
        rarity: str,
        amount: int = 1
    ):
        item_id = str(item_id)
        rarity = normalize_rarity(rarity)

        if rarity not in self.rarities:
            await ctx.reply(
                "❌ レアリティが不正です\n"
                f"使用可能: `{', '.join(get_user_rarity_inputs())}`"
            )
            return

        ok, err = remove_item(member.id, item_id, rarity, amount)

        if ok:
            await ctx.reply(
                f"✅ `{member.display_name}` から "
                f"`{item_id}`（{rarity}）×{amount} を削除しました"
            )
        else:
            await ctx.reply(self.error_message(err))

    # ===== remove_item error =====
    @remove_item_cmd.error
    async def remove_item_error(self, ctx, error):
        if isinstance(error, commands.CheckFailure):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                "❌ 引数が足りません\n"
                "**形式:**\n"
                "`c!remove_item @user item_id rarity [amount]`\n"
                "**例:**\n"
                "`c!remove_item @user 1 normal 1`"
            )
            return

        if isinstance(error, commands.BadArgument):
            await ctx.reply("❌ 引数の型が正しくありません")
            return

        raise error

    # ==================================================
    # create_item (slash)
    # ==================================================
    @app_commands.command(
        name="create_item",
        description="新規アイテムを作成します(Admin専用)"
    )
    async def create_item_slash(
        self,
        interaction: discord.Interaction,
        rarity: str,
        name: str,
        description: str
    ):
        ok, err, item_id = create_item(rarity, name, description)

        if ok:
            rarity_key = normalize_rarity(rarity)
            await interaction.response.send_message(
                f"アイテム作成に成功しました\n"
                f"ID: `{item_id}` / rarity: `{rarity_key}` / name: `{name}` / description: `{description}`"
            )
            return

        if err == "invalid_rarity":
            await interaction.response.send_message(
                "未知のレアリティです\n"
                f"使用可能なレアリティ: `{', '.join(get_user_rarity_inputs())}`",
                ephemeral=True
            )
            return
        if err == "invalid_name":
            await interaction.response.send_message(
                "名前を入力してください",
                ephemeral=True
            )
            return
        if err == "invalid_description":
            await interaction.response.send_message(
                "説明文を入力してください",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            self.error_message(err),
            ephemeral=True
        )

    # ==================================================
    # add_item (slash)
    # ==================================================
    @app_commands.command(
        name="add_item",
        description="ユーザーにアイテムを追加します（管理者）"
    )
    async def add_item_slash(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        item_id: str,
        rarity: str,
        amount: int = 1
    ):
        item_id = str(item_id)
        rarity = normalize_rarity(rarity)

        if rarity not in self.rarities or not item_exists(item_id, rarity):
            await interaction.response.send_message(
                "❌ 指定されたアイテムは存在しません",
                ephemeral=True
            )
            return

        ok, err = add_item(member.id, item_id, rarity, amount)

        if ok:
            await interaction.response.send_message(
                f"✅ `{member.display_name}` に "
                f"`{item_id}`（{rarity}）×{amount} を追加しました"
            )
        else:
            await interaction.response.send_message(
                self.error_message(err),
                ephemeral=True
            )

    # ==================================================
    # remove_item (slash)
    # ==================================================
    @app_commands.command(
        name="remove_item",
        description="ユーザーからアイテムを削除します（管理者）"
    )
    async def remove_item_slash(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        item_id: str,
        rarity: str,
        amount: int = 1
    ):
        item_id = str(item_id)
        rarity = normalize_rarity(rarity)

        ok, err = remove_item(member.id, item_id, rarity, amount)

        if ok:
            await interaction.response.send_message(
                f"✅ `{member.display_name}` から "
                f"`{item_id}`（{rarity}）×{amount} を削除しました"
            )
        else:
            await interaction.response.send_message(
                self.error_message(err),
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(ItemAdmin(bot))
    print("admin_item.py cog loaded")
