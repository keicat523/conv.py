from discord.ext import commands
from discord import app_commands
import discord
import os

import ast
import base64
import hashlib
import importlib
import importlib.util
import tempfile
from pathlib import Path

import aiohttp


# プロジェクトのルートディレクトリ
ROOT_DIR = Path(__file__).resolve().parents[2]

# GitHubから取得してよいCogを明示的に指定する
RELOADABLE_COGS = {
    "cogs.admin.admin",
    "cogs.math.diff",
}

MAX_COG_SIZE = 200_000
GITHUB_API = "https://api.github.com"


OK_EMOJI = "✅"
NG_EMOJI = "❌"


async def _fetch_github_cog(self, module: str) -> bytes:
    if module not in self.RELOADABLE_COGS:
        raise ValueError("このCogは更新対象として許可されていません")

    owner = os.environ["GITHUB_OWNER"]
    repo = os.environ["GITHUB_REPO"]
    branch = os.environ["GITHUB_BRANCH"]
    token = os.environ["GITHUB_TOKEN"]

    # cogs.admin.admin -> cogs/admin/admin.py
    relative_path = module.replace(".", "/") + ".py"

    url = (
        f"{self.GITHUB_API}/repos/{owner}/{repo}"
        f"/contents/{relative_path}"
    )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            url,
            headers=headers,
            params={"ref": branch},
        ) as response:
            response.raise_for_status()
            data = await response.json()

    if data.get("type") != "file":
        raise ValueError("GitHub上の対象が通常ファイルではありません")

    if data.get("encoding") != "base64":
        raise ValueError("GitHubの応答形式が不正です")

    if data.get("size", MAX_COG_SIZE + 1) > MAX_COG_SIZE:
        raise ValueError("Cogのファイルサイズが上限を超えています")

    content = base64.b64decode(data["content"], validate=False)

    if len(content) > MAX_COG_SIZE:
        raise ValueError("取得したファイルサイズが上限を超えています")

    # Gitのblob SHA-1を検証する
    header = f"blob {len(content)}\0".encode("ascii")
    actual_sha = hashlib.sha1(header + content).hexdigest()

    if actual_sha != data.get("sha"):
        raise ValueError("Git blob SHAの検証に失敗しました")

    # UTF-8とPython構文を検証する
    source = content.decode("utf-8")
    ast.parse(source, filename=relative_path)

    return content



def _atomic_write(self, path: Path, content: bytes) -> None:
    temp_name = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=".cog-update-",
            suffix=".tmp",
            delete=False,
        ) as temp:
            temp.write(content)
            temp.flush()
            os.fsync(temp.fileno())
            temp_name = temp.name

        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


async def _update_and_reload(self, module: str):
    if module not in self.RELOADABLE_COGS:
        raise ValueError("GitHubからの更新が許可されていないCogです")

    relative_path = module.replace(".", "/") + ".py"
    path = (ROOT_DIR / relative_path).resolve()

    # 念のため、プロジェクト外への書き込みを拒否
    path.relative_to(ROOT_DIR.resolve())

    if not path.is_file():
        raise FileNotFoundError(f"対象ファイルがありません: {relative_path}")

    new_content = await self._fetch_github_cog(module)
    old_content = path.read_bytes()

    try:
        # 先に新しい内容を構文検証
        compile(new_content.decode("utf-8"), str(path), "exec")

        self._atomic_write(path, new_content)

        # 古いバイトコードが使われないようにする
        importlib.invalidate_caches()
        pyc = Path(importlib.util.cache_from_source(str(path)))
        if pyc.exists():
            pyc.unlink()

        await self.bot.reload_extension(module)

    except Exception:
        # 置換後に失敗した場合、ファイルを復元する
        if path.exists():
            self._atomic_write(path, old_content)

        importlib.invalidate_caches()
        pyc = Path(importlib.util.cache_from_source(str(path)))
        if pyc.exists():
            pyc.unlink()

        raise

    return relative_path


class Reload(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =========================
    # ?? Embed
    # =========================
    def build_result_embed(self, success, failed):
        embed = discord.Embed(
            title="再読み込み結果",
            color=discord.Color.green() if not failed else discord.Color.orange()
        )

        if success:
            embed.add_field(
                name="成功",
                value="\n".join(f"`{c}`" for c in success),
                inline=False
            )

        if failed:
            embed.add_field(
                name="失敗",
                value="\n".join(
                    f"`{c}` → `{err}`" for c, err in failed.items()
                ),
                inline=False
            )

        embed.set_footer(text=f"成功: {len(success)} / 失敗: {len(failed)}")
        return embed

    # =========================
    # cogs ???
    # =========================
    def iter_cogs(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cogs_root = os.path.dirname(base_dir)  # cogs/

        for root, _, files in os.walk(cogs_root):
            for file in files:
                if not file.endswith(".py"):
                    continue
                if file.startswith("_"):
                    continue

                full = os.path.join(root, file)
                rel = os.path.relpath(full, cogs_root)

                # cogs/math/diff.py -> cogs.math.diff
                module = "cogs." + rel.replace(os.sep, ".")[:-3]
                yield module

    def iter_cogs_in_folder(self, folder: str):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cogs_root = os.path.dirname(base_dir)  # cogs/
        target = os.path.join(cogs_root, folder)
        if not os.path.isdir(target):
            return []

        modules = []
        for root, _, files in os.walk(target):
            for file in files:
                if not file.endswith(".py"):
                    continue
                if file.startswith("_"):
                    continue

                full = os.path.join(root, file)
                rel = os.path.relpath(full, cogs_root)
                module = "cogs." + rel.replace(os.sep, ".")[:-3]
                modules.append(module)

        return modules

    # =========================
    # ?? reload
    # =========================
    async def reload_all(self):
        success = []
        failed = {}

        for cog in self.iter_cogs():
            try:
                await self.bot.reload_extension(cog)
                success.append(cog)
            except Exception as e:
                failed[cog] = str(e)

        return success, failed

    # =========================
    # ???? reload
    # =========================
    async def reload_folder(self, folder: str):
        success = []
        failed = {}

        modules = self.iter_cogs_in_folder(folder)
        if not modules:
            return [], {folder: "フォルダが見つかりません"}

        for cog in modules:
            try:
                await self.bot.reload_extension(cog)
                success.append(cog)
            except Exception as e:
                failed[cog] = str(e)

        return success, failed

    # =========================
    # ?? reload
    # =========================
    async def reload_one(self, cog: str):
        module = f"cogs.{cog}"

        try:
            if module in self.RELOADABLE_COGS:
                path = await self._update_and_reload(module)
                return [f"{module} (GitHub更新済み: {path})"], {}

            # 許可リスト外は、従来どおりローカルのコードを再読み込み
            await self.bot.reload_extension(module)
            return [module], {}

        except Exception as e:
            return [], {module: f"{type(e).__name__}: {e}"}

    # =========================
    # ??
    # =========================
    def list_cogs(self):
        return sorted(self.iter_cogs())

    # =========================
    # Prefix
    # =========================
    @commands.command(name="reload")
    async def reload(self, ctx, cog: str | None = None):
        if cog == "list":
            items = self.list_cogs()
            await ctx.reply("\n".join(f"`{c}`" for c in items))
            return

        # ??????
        if cog and os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", cog)):
            success, failed = await self.reload_folder(cog)
            embed = self.build_result_embed(success, failed)
            await ctx.reply(embed=embed)
            return

        # ??
        if cog:
            success, failed = await self.reload_one(cog)
            embed = self.build_result_embed(success, failed)
            await ctx.reply(embed=embed)
            return

        # ??????
        confirm = await ctx.reply(
            "⚠️ **全ての Cog を再読み込みします**\n"
            "10秒以内に `ok` と返信してください"
        )

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            msg = await self.bot.wait_for("message", timeout=10, check=check)
            if msg.content.lower() != "ok":
                await confirm.add_reaction(NG_EMOJI)
                return

            success, failed = await self.reload_all()
            embed = self.build_result_embed(success, failed)

            await confirm.add_reaction(OK_EMOJI)
            await ctx.reply(embed=embed)

        except TimeoutError:
            await confirm.add_reaction(NG_EMOJI)

    # =========================
    # Slash
    # =========================
    @app_commands.command(
        name="reload",
        description="Cog を再読み込みします（管理者のみ）"
    )
    @app_commands.describe(
        cog="例: math.diff / item.inventory / admin"
    )
    async def reload_slash(
        self,
        interaction: discord.Interaction,
        cog: str
    ):
        if cog == "list":
            items = self.list_cogs()
            await interaction.response.send_message(
                "\n".join(f"`{c}`" for c in items),
                ephemeral=True
            )
            return

        # ??????
        if os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", cog)):
            success, failed = await self.reload_folder(cog)
            embed = self.build_result_embed(success, failed)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # ??
        success, failed = await self.reload_one(cog)
        embed = self.build_result_embed(success, failed)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Reload(bot))
    print("reload.py cog loaded")
