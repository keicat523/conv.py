import json
from pathlib import Path

import discord
from discord.ext import commands

from utils.ignore_mode import is_ignore_enabled, toggle_ignore


# 状態保存ファイルパス
BASE_DIR = Path(__file__).resolve().parents[2]
INFO_STATE_PATH = BASE_DIR / "data" / "info_state.json"

STATUS_TO_KEY = {
    discord.Status.online: "online",
    discord.Status.invisible: "offline",
    discord.Status.offline: "offline",
    discord.Status.dnd: "dnd",
    discord.Status.idle: "idle",
}

KEY_TO_STATUS = {
    "online": discord.Status.online,
    "offline": discord.Status.invisible,
    "dnd": discord.Status.dnd,
    "idle": discord.Status.idle,
}

STATUS_TO_LABEL = {
    discord.Status.online: "online",
    discord.Status.invisible: "offline",
    discord.Status.offline: "offline",
    discord.Status.dnd: "取り込み中",
    discord.Status.idle: "退席中",
}

INPUT_TO_STATUS = {
    "0": discord.Status.online,
    "1": discord.Status.invisible,
    "2": discord.Status.dnd,
    "3": discord.Status.idle,
}


# 状態永続化ヘルパー
def _load_info_state() -> dict:
    if not INFO_STATE_PATH.exists():
        return {"status": "online", "activity": None}
    try:
        with open(INFO_STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"status": "online", "activity": None}

    status_key = str(data.get("status", "online")).lower()
    if status_key not in KEY_TO_STATUS:
        status_key = "online"

    activity = data.get("activity")
    if activity is not None:
        activity = str(activity)
        if not activity:
            activity = None

    return {"status": status_key, "activity": activity}


def _save_info_state(status: discord.Status, activity: str | None) -> None:
    INFO_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": STATUS_TO_KEY.get(status, "online"),
        "activity": activity if activity else None,
    }
    with open(INFO_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


class Info(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.current_status: discord.Status = discord.Status.online
        self.activity_text: str | None = None

    async def cog_load(self) -> None:
        state = _load_info_state()
        self.current_status = KEY_TO_STATUS.get(state["status"], discord.Status.online)
        self.activity_text = state["activity"]

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._apply_presence()

    # 表示用ヘルパー
    def _status_label(self, status: discord.Status) -> str:
        return STATUS_TO_LABEL.get(status, str(status))

    def _activity_label(self) -> str:
        return self.activity_text if self.activity_text else "なし"

    async def _apply_presence(self) -> None:
        activity: discord.BaseActivity | None = None
        if self.activity_text:
            activity = discord.Game(name=self.activity_text)
        await self.bot.change_presence(status=self.current_status, activity=activity)

    async def _add_x_reaction(self, message: discord.Message) -> None:
        try:
            await message.add_reaction("❌")
        except Exception:
            pass

    def _build_show_embed(self) -> discord.Embed:
        user = self.bot.user
        name = user.name if user else "unknown"
        user_id = str(user.id) if user else "unknown"
        devmode = "ON" if is_ignore_enabled() else "OFF"

        embed = discord.Embed(title="Bot Info", color=discord.Color.blurple())
        embed.description = (
            f"ユーザー名: {name}\n"
            f"ユーザーID: {user_id}\n"
            f"ステータス状態[stat]: {self._status_label(self.current_status)}\n"
            f"アクティビティ[astat]: {self._activity_label()}\n"
            f"DevMode[devmode]: {devmode}"
        )
        if user:
            embed.set_thumbnail(url=user.display_avatar.url)
        return embed

    # Prefixコマンド: c!info
    @commands.command(name="info")
    async def info_cmd(
        self,
        ctx: commands.Context,
        action: str | None = None,
        option: str | None = None,
    ) -> None:
        if action is None:
            await ctx.reply("使用方法: c!info <show/merge> [サブオプション]")
            return

        action = action.lower()
        if action == "show":
            await ctx.reply(embed=self._build_show_embed())
            return

        if action != "merge":
            await ctx.reply("使用方法: c!info <show/merge> [サブオプション]")
            return

        if option is None:
            await ctx.reply("使用方法: c!info merge <devmode/stat/astat>")
            return

        option = option.lower()

        if option == "devmode":
            enabled = toggle_ignore()
            state = "ON" if enabled else "OFF"
            await ctx.reply(f"DevMode[devmode] を {state} に切り替えました")
            return

        if option == "stat":
            prompt = await ctx.reply(
                "stat 一覧:\n"
                "0: online\n"
                "1: offline\n"
                "2: 取り込み中\n"
                "3: 退席中\n"
                "10秒以内に 0/1/2/3 を送信してください"
            )

            def check(m: discord.Message) -> bool:
                return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

            try:
                msg = await self.bot.wait_for("message", timeout=10, check=check)
            except TimeoutError:
                await self._add_x_reaction(prompt)
                return

            selected = INPUT_TO_STATUS.get(msg.content.strip())
            if selected is None:
                await self._add_x_reaction(prompt)
                return

            self.current_status = selected
            _save_info_state(self.current_status, self.activity_text)
            await self._apply_presence()
            await ctx.reply(f"ステータス状態[stat] を {self._status_label(selected)} に変更しました")
            return

        if option == "astat":
            prompt = await ctx.reply(
                "アクティビティステータスを送信してください\n"
                "(xで停止, 00で設定を消す(表示しない))"
            )

            def check(m: discord.Message) -> bool:
                return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

            try:
                msg = await self.bot.wait_for("message", timeout=10, check=check)
            except TimeoutError:
                await self._add_x_reaction(prompt)
                return

            content = msg.content.strip()
            if content.lower() == "x":
                await self._add_x_reaction(prompt)
                return

            if content == "00":
                self.activity_text = None
                _save_info_state(self.current_status, self.activity_text)
                await self._apply_presence()
                await ctx.reply("アクティビティ[astat] を消去しました")
                return

            self.activity_text = content
            _save_info_state(self.current_status, self.activity_text)
            await self._apply_presence()
            await ctx.reply(f"アクティビティ[astat] を `{content}` に設定しました")
            return

        await ctx.reply("使用方法: c!info merge <devmode/stat/astat>")


async def setup(bot: commands.Bot):
    await bot.add_cog(Info(bot))
    print("info.py cog loaded")
