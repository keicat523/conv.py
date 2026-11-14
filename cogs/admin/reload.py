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
import config


# =========================
# 基本設定
# =========================

ROOT_DIR = Path(__file__).resolve().parents[2]
COGS_DIR = ROOT_DIR / "cogs"
CONFIG_PATH = ROOT_DIR / "config.py"

MAX_COG_SIZE = 200_000
GITHUB_API = "https://api.github.com"

OK_EMOJI = "✅"
NG_EMOJI = "❌"


# =========================
# Cog一覧取得
# =========================

def get_reloadable_cogs() -> set[str]:
    """cogs フォルダ内の Python モジュール名を自動取得する。"""
    result = set()

    for path in COGS_DIR.rglob("*.py"):
        if path.name == "__init__.py":
            continue

        if "__pycache__" in path.parts:
            continue

        relative = path.relative_to(ROOT_DIR).with_suffix("")
        module = ".".join(relative.parts)

        result.add(module)

    return result


# =========================
# GitHubからCog取得
# =========================

async def _fetch_github_cog(module: str) -> bytes:
    if module not in get_reloadable_cogs():
        raise ValueError("このCogは更新対象として許可されていません")

    owner = os.environ["GITHUB_OWNER"]
    repo = os.environ["GITHUB_REPO"]
    branch = os.environ["GITHUB_BRANCH"]
    token = os.environ["GITHUB_TOKEN"]

    relative_path = module.replace(".", "/") + ".py"

    url = (
        f"{GITHUB_API}/repos/{owner}/{repo}"
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

    # Gitのblob SHA-1を検証
    header = f"blob {len(content)}\0".encode("ascii")
    actual_sha = hashlib.sha1(header + content).hexdigest()

    if actual_sha != data.get("sha"):
        raise ValueError("Git blob SHAの検証に失敗しました")

    # UTF-8 BOMを除去して構文検証
    source = content.decode("utf-8-sig")
    ast.parse(source, filename=relative_path)

    return source.encode("utf-8")


# =========================
# GitHubからconfig.py取得
# =========================

async def _fetch_github_config() -> bytes:
    owner = os.environ["GITHUB_OWNER"]
    repo = os.environ["GITHUB_REPO"]
    branch = os.environ["GITHUB_BRANCH"]
    token = os.environ["GITHUB_TOKEN"]

    url = (
        f"{GITHUB_API}/repos/{owner}/{repo}"
        "/contents/config.py"
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
        raise ValueError("GitHub上のconfig.pyが通常ファイルではありません")

    if data.get("encoding") != "base64":
        raise ValueError("GitHubのconfig.py取得形式が不正です")

    if data.get("size", MAX_COG_SIZE + 1) > MAX_COG_SIZE:
        raise ValueError("config.pyのファイルサイズが上限を超えています")

    content = base64.b64decode(data["content"], validate=False)

    if len(content) > MAX_COG_SIZE:
        raise ValueError("config.pyのファイルサイズが上限を超えています")

    # Gitのblob SHA-1を検証
    header = f"blob {len(content)}\0".encode("ascii")
    actual_sha = hashlib.sha1(header + content).hexdigest()

    if actual_sha != data.get("sha"):
        raise ValueError("config.pyのGit blob SHA検証に失敗しました")

    source = content.decode("utf-8-sig")
    ast.parse(source, filename="config.py")

    return source.encode("utf-8")


# =========================
# ファイル書き込み
# =========================

def _atomic_write(path: Path, content: bytes) -> None:
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


# =========================
# Cog更新・再読み込み
# =========================

async def _update_and_reload(self, module: str):
    if module not in get_reloadable_cogs():
        raise ValueError("GitHubからの更新が許可されていないCogです")

    relative_path = module.replace(".", "/") + ".py"
    path = (ROOT_DIR / relative_path).resolve()

    # プロジェクト外への書き込みを拒否
    path.relative_to(ROOT_DIR.resolve())

    if not path.is_file():
        raise FileNotFoundError(
            f"対象ファイルがありません: {relative_path}"
        )

    new_content = await _fetch_github_cog(module)
    old_content = path.read_bytes()

    try:
        compile(
            new_content.decode("utf-8"),
            str(path),
            "exec",
        )

        _atomic_write(path, new_content)

        importlib.invalidate_caches()

        pyc = Path(
            importlib.util.cache_from_source(str(path))
        )

        if pyc.exists():
            pyc.unlink()

        await self.bot.reload_extension(module)

    except Exception:
        # ファイルを元に戻す
        if path.exists():
            _atomic_write(path, old_content)

        importlib.invalidate_caches()

        pyc = Path(
            importlib.util.cache_from_source(str(path))
        )

        if pyc.exists():
            pyc.unlink()

        raise

    return relative_path


# =========================
# config.py更新・再読み込み
# =========================

async def _update_and_reload_config():
    if not CONFIG_PATH.is_file():
        raise FileNotFoundError("config.py が見つかりません")

    new_content = await _fetch_github_config()
    old_content = CONFIG_PATH.read_bytes()

    try:
        compile(
            new_content.decode("utf-8"),
            str(CONFIG_PATH),
            "exec",
        )

        _atomic_write(CONFIG_PATH, new_content)

        importlib.invalidate_caches()

        pyc = Path(
            importlib.util.cache_from_source(str(CONFIG_PATH))
        )

        if pyc.exists():
            pyc.unlink()

        importlib.reload(config)

    except Exception:
        # ファイルを元に戻す
        _atomic_write(CONFIG_PATH, old_content)

        importlib.invalidate_caches()

        pyc = Path(
            importlib.util.cache_from_source(str(CONFIG_PATH))
        )

        if pyc.exists():
            pyc.unlink()

        # 元のconfig.pyを再読み込み
        importlib.reload(config)

        raise

    return "config.py"


# =========================
# Reload Cog
# =========================

class Reload(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =========================
    # Embed
    # =========================

    def build_result_embed(self, success, failed):
        embed = discord.Embed(
            title="再読み込み結果",
            color=(
                discord.Color.green()
                if not failed
                else discord.Color.orange()
            ),
        )

        def make_text(lines, limit=1000):
            result = []

            for line in lines:
                remaining = limit - sum(
                    len(x) + 1 for x in result
                )

                if remaining <= 0:
                    result.append("…以下省略")
                    break

                if len(line) > remaining:
                    result.append(
                        line[:max(0, remaining - 6)] + "…省略"
                    )
                    break

                result.append(line)

            return "\n".join(result) or "なし"

        if success:
            lines = [f"`{c}`" for c in success]

            embed.add_field(
                name=f"成功（{len(success)}件）",
                value=make_text(lines),
                inline=False,
            )

        if failed:
            lines = [
                f"`{c}` → `{str(err)[:250]}`"
                for c, err in failed.items()
            ]

            embed.add_field(
                name=f"失敗（{len(failed)}件）",
                value=make_text(lines),
                inline=False,
            )

        embed.set_footer(
            text=f"成功: {len(success)} / 失敗: {len(failed)}"
        )

        return embed

    # =========================
    # Cog一覧取得
    # =========================

    def iter_cogs(self):
        for path in COGS_DIR.rglob("*.py"):
            if path.name.startswith("_"):
                continue

            if "__pycache__" in path.parts:
                continue

            relative = path.relative_to(ROOT_DIR).with_suffix("")
            module = ".".join(relative.parts)

            yield module

    def iter_cogs_in_folder(self, folder: str):
        target = (COGS_DIR / folder).resolve()

        try:
            target.relative_to(COGS_DIR.resolve())
        except ValueError:
            return []

        if not target.is_dir():
            return []

        modules = []

        for path in target.rglob("*.py"):
            if path.name.startswith("_"):
                continue

            if "__pycache__" in path.parts:
                continue

            relative = path.relative_to(ROOT_DIR).with_suffix("")
            module = ".".join(relative.parts)

            modules.append(module)

        return modules

    # =========================
    # 全Cog再読み込み
    # =========================

    async def reload_all(self):
        success = []
        failed = {}

        for cog in self.iter_cogs():
            try:
                if cog in get_reloadable_cogs():
                    path = await _update_and_reload(self, cog)

                    success.append(
                        f"{cog} (GitHub更新済み: {path})"
                    )
                else:
                    await self.bot.reload_extension(cog)
                    success.append(cog)

            except Exception as e:
                failed[cog] = (
                    f"{type(e).__name__}: {e}"
                )

        # config.pyも再読み込み
        try:
            path = await _update_and_reload_config()

            success.append(
                f"config.py (GitHub更新済み: {path})"
            )

        except Exception as e:
            failed["config.py"] = (
                f"{type(e).__name__}: {e}"
            )

        return success, failed

    # =========================
    # フォルダ再読み込み
    # =========================

    async def reload_folder(self, folder: str):
        success = []
        failed = {}

        modules = sorted(
            self.iter_cogs_in_folder(folder)
        )

        if not modules:
            return [], {
                folder: "フォルダが見つかりません"
            }

        for cog in modules:
            try:
                if cog in get_reloadable_cogs():
                    path = await _update_and_reload(self, cog)

                    success.append(
                        f"{cog} (GitHub更新済み: {path})"
                    )

                else:
                    await self.bot.reload_extension(cog)
                    success.append(cog)

            except Exception as e:
                failed[cog] = (
                    f"{type(e).__name__}: {e}"
                )

        return success, failed

    # =========================
    # 個別再読み込み
    # =========================

    async def reload_one(self, cog: str):
        # config.pyの場合
        if cog == "config":
            try:
                path = await _update_and_reload_config()

                return [
                    f"config.py (GitHub更新済み: {path})"
                ], {}

            except Exception as e:
                return [], {
                    "config.py": f"{type(e).__name__}: {e}"
                }

        module = f"cogs.{cog}"

        try:
            if module in get_reloadable_cogs():
                path = await _update_and_reload(self, module)

                return [
                    f"{module} (GitHub更新済み: {path})"
                ], {}

            # 許可リスト外はローカルのコードを再読み込み
            await self.bot.reload_extension(module)

            return [module], {}

        except Exception as e:
            return [], {
                module: f"{type(e).__name__}: {e}"
            }

    # =========================
    # Cog一覧
    # =========================

    def list_cogs(self):
        return sorted(self.iter_cogs()) + ["config.py"]

    # =========================
    # Prefix Command
    # =========================

    @commands.command(name="reload")
    async def reload(self, ctx, cog: str | None = None):

        # 一覧
        if cog == "list":
            items = self.list_cogs()

            await ctx.reply(
                "\n".join(f"`{c}`" for c in items)
            )

            return

        # config.py
        if cog in ("config", "config.py"):
            success, failed = await self.reload_one("config")

            embed = self.build_result_embed(
                success,
                failed,
            )

            await ctx.reply(embed=embed)
            return

        # フォルダ
        if cog and (
            COGS_DIR / cog
        ).is_dir():

            success, failed = await self.reload_folder(cog)

            embed = self.build_result_embed(
                success,
                failed,
            )

            await ctx.reply(embed=embed)
            return

        # 個別Cog
        if cog:
            success, failed = await self.reload_one(cog)

            embed = self.build_result_embed(
                success,
                failed,
            )

            await ctx.reply(embed=embed)
            return

        # 全Cog再読み込みの確認
        confirm = await ctx.reply(
            "⚠️ **全ての Cog と config.py を再読み込みします**\n"
            "10秒以内に `ok` と返信してください"
        )

        def check(m):
            return (
                m.author == ctx.author
                and m.channel == ctx.channel
            )

        try:
            msg = await self.bot.wait_for(
                "message",
                timeout=10,
                check=check,
            )

            if msg.content.lower() != "ok":
                await confirm.add_reaction(NG_EMOJI)
                return

            success, failed = await self.reload_all()

            embed = self.build_result_embed(
                success,
                failed,
            )

            await confirm.add_reaction(OK_EMOJI)
            await ctx.reply(embed=embed)

        except TimeoutError:
            await confirm.add_reaction(NG_EMOJI)

    # =========================
    # Slash Command
    # =========================

    @app_commands.command(
        name="reload",
        description="Cogを再読み込みします（管理者のみ）",
    )
    @app_commands.describe(
        cog="例: math.diff / item.inventory / admin / config"
    )
    async def reload_slash(
        self,
        interaction: discord.Interaction,
        cog: str,
    ):

        # 一覧
        if cog == "list":
            items = self.list_cogs()

            await interaction.response.send_message(
                "\n".join(f"`{c}`" for c in items),
                ephemeral=True,
            )

            return

        # config.py
        if cog in ("config", "config.py"):
            success, failed = await self.reload_one("config")

            embed = self.build_result_embed(
                success,
                failed,
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )

            return

        # フォルダ
        if (COGS_DIR / cog).is_dir():
            success, failed = await self.reload_folder(cog)

            embed = self.build_result_embed(
                success,
                failed,
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
            )

            return

        # 個別Cog
        success, failed = await self.reload_one(cog)

        embed = self.build_result_embed(
            success,
            failed,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(Reload(bot))
    print("reload.py cog loaded")