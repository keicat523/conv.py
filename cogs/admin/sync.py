
import os
import ast
import base64
import hashlib
import importlib
import importlib.util
import tempfile
import asyncio

from pathlib import Path, PurePosixPath
from urllib.parse import quote

import aiohttp
import discord

from discord.ext import commands
from discord import app_commands

import admin_ids


# =========================
# Settings
# =========================

ROOT_DIR = Path(__file__).resolve().parents[2]
COGS_DIR = ROOT_DIR / "cogs"

GITHUB_API = "https://api.github.com"

MAX_COG_SIZE = 200_000
MAX_TOTAL_SIZE = 5_000_000
MAX_FILES = 500

OK_EMOJI = "✅"
NG_EMOJI = "❌"

# 同時実行防止
SYNC_LOCK = asyncio.Lock()


# =========================
# Admin
# =========================

def is_admin(user_id: int) -> bool:
    return user_id in admin_ids.ADMIN_IDS


# =========================
# GitHub
# =========================

def github_headers():
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def fetch_github_files():
    """
    GitHubのmainブランチからcogs以下のPythonファイルを取得する。
    Git blob SHA-1も検証する。
    """

    owner = os.environ["GITHUB_OWNER"]
    repo = os.environ["GITHUB_REPO"]
    branch = os.environ["GITHUB_BRANCH"]

    headers = github_headers()

    timeout = aiohttp.ClientTimeout(total=120)

    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=headers
    ) as session:

        # -------------------------
        # Branch SHA取得
        # -------------------------

        ref_url = (
            f"{GITHUB_API}/repos/{owner}/{repo}"
            f"/git/ref/heads/{quote(branch, safe='')}"
        )

        async with session.get(ref_url) as response:
            response.raise_for_status()
            ref_data = await response.json()

        commit_sha = ref_data["object"]["sha"]

        # -------------------------
        # Commit取得
        # -------------------------

        commit_url = (
            f"{GITHUB_API}/repos/{owner}/{repo}"
            f"/git/commits/{commit_sha}"
        )

        async with session.get(commit_url) as response:
            response.raise_for_status()
            commit_data = await response.json()

        tree_sha = commit_data["tree"]["sha"]

        # -------------------------
        # Tree取得
        # -------------------------

        tree_url = (
            f"{GITHUB_API}/repos/{owner}/{repo}"
            f"/git/trees/{tree_sha}"
        )

        async with session.get(
            tree_url,
            params={"recursive": "1"}
        ) as response:
            response.raise_for_status()
            tree_data = await response.json()

        if tree_data.get("truncated"):
            raise RuntimeError(
                "GitHubのTree取得結果が途中で切れています"
            )

        files = {}

        entries = [
            item for item in tree_data["tree"]
            if (
                item.get("type") == "blob"
                and item["path"].startswith("cogs/")
                and item["path"].endswith(".py")
            )
        ]

        if len(entries) > MAX_FILES:
            raise ValueError("Cogのファイル数が上限を超えています")

        total_size = 0

        # -------------------------
        # Blob取得
        # -------------------------

        for item in entries:

            relative = item["path"]
            pure_path = PurePosixPath(relative)

            if (
                pure_path.is_absolute()
                or ".." in pure_path.parts
                or len(pure_path.parts) < 2
            ):
                raise ValueError(
                    f"不正なGitHubパス: {relative}"
                )

            if item.get("size", MAX_COG_SIZE + 1) > MAX_COG_SIZE:
                raise ValueError(
                    f"ファイルサイズ上限超過: {relative}"
                )

            total_size += item["size"]

            if total_size > MAX_TOTAL_SIZE:
                raise ValueError(
                    "Cog全体のサイズが上限を超えています"
                )

            blob_url = (
                f"{GITHUB_API}/repos/{owner}/{repo}"
                f"/git/blobs/{item['sha']}"
            )

            async with session.get(blob_url) as response:
                response.raise_for_status()
                blob_data = await response.json()

            if blob_data.get("encoding") != "base64":
                raise ValueError(
                    f"Blobの形式が不正です: {relative}"
                )

            content = base64.b64decode(
                blob_data["content"],
                validate=False
            )

            if len(content) > MAX_COG_SIZE:
                raise ValueError(
                    f"取得サイズ上限超過: {relative}"
                )

            # Git blob SHA-1検証
            header = (
                f"blob {len(content)}\0".encode("ascii")
            )

            actual_sha = hashlib.sha1(
                header + content
            ).hexdigest()

            if actual_sha != item["sha"]:
                raise ValueError(
                    f"Git SHA-1検証失敗: {relative}"
                )

            # BOM除去・構文検証
            source = content.decode("utf-8-sig")

            ast.parse(
                source,
                filename=relative
            )

            files[relative] = source.encode("utf-8")

        return files


# =========================
# Local files
# =========================

def get_local_files() -> dict[str, Path]:
    result = {}

    for path in COGS_DIR.rglob("*.py"):

        if "__pycache__" in path.parts:
            continue

        relative = path.relative_to(ROOT_DIR).as_posix()
        result[relative] = path

    return result


def module_from_path(relative: str) -> str | None:
    """
    PythonファイルのパスからExtension名を作る。
    __init__.pyとアンダースコア始まりは除外。
    """

    path = PurePosixPath(relative)

    if path.name == "__init__.py":
        return None

    if path.name.startswith("_"):
        return None

    return ".".join(path.with_suffix("").parts)


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_name = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=".sync-",
            suffix=".tmp",
            delete=False
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


def invalidate_module_cache(path: Path):
    importlib.invalidate_caches()

    try:
        pyc = Path(
            importlib.util.cache_from_source(str(path))
        )

        if pyc.exists():
            pyc.unlink()

    except (NotImplementedError, ValueError):
        pass


# =========================
# Result
# =========================

def build_embed(
    added,
    updated,
    deleted,
    failed,
    slash_result=None
):
    embed = discord.Embed(
        title="GitHub同期結果",
        color=(
            discord.Color.green()
            if not failed and slash_result is not False
            else discord.Color.orange()
        )
    )

    def add_field(name, items):
        if not items:
            return

        text = "\n".join(
            f"`{item}`" for item in items
        )

        if len(text) > 1000:
            text = text[:990] + "\n…省略"

        embed.add_field(
            name=f"{name} ({len(items)}件)",
            value=text,
            inline=False
        )

    add_field("新規追加", added)
    add_field("更新", updated)
    add_field("削除", deleted)

    if failed:
        lines = [
            f"`{name}`: {str(error)[:200]}"
            for name, error in failed.items()
        ]

        embed.add_field(
            name=f"失敗 ({len(failed)}件)",
            value="\n".join(lines)[:1000],
            inline=False
        )

    if slash_result is True:
        embed.add_field(
            name="Slash同期",
            value="✅ Discordへの同期完了",
            inline=False
        )
    elif slash_result is False:
        embed.add_field(
            name="Slash同期",
            value="❌ Discordへの同期失敗",
            inline=False
        )

    embed.set_footer(
        text=(
            f"追加: {len(added)} / "
            f"更新: {len(updated)} / "
            f"削除: {len(deleted)} / "
            f"失敗: {len(failed)}"
        )
    )

    return embed


# =========================
# Main synchronization
# =========================

async def synchronize(bot):

    added = []
    updated = []
    deleted = []
    failed = {}

    if SYNC_LOCK.locked():
        raise RuntimeError("別の同期処理が実行中です")

    async with SYNC_LOCK:

        # -------------------------
        # GitHub取得
        # -------------------------

        remote_files = await fetch_github_files()
        local_files = get_local_files()

        remote_paths = set(remote_files)
        local_paths = set(local_files)

        added_paths = remote_paths - local_paths
        deleted_paths = local_paths - remote_paths

        updated_paths = {
            path for path in remote_paths & local_paths
            if local_files[path].read_bytes() != remote_files[path]
        }

        # -------------------------
        # 更新対象Extension
        # -------------------------

        added_modules = {
            module_from_path(path)
            for path in added_paths
        } - {None}

        updated_modules = {
            module_from_path(path)
            for path in updated_paths
        } - {None}

        deleted_modules = {
            module_from_path(path)
            for path in deleted_paths
        } - {None}

        # sync.py自身は実行中なのでリロードしない
        sync_module = "cogs.admin.sync"

        added_modules.discard(sync_module)
        updated_modules.discard(sync_module)
        deleted_modules.discard(sync_module)

        # -------------------------
        # 変更前の状態を保存
        # -------------------------

        original_extensions = set(bot.extensions)

        original_contents = {
            path: local_files[path].read_bytes()
            for path in local_files
        }

        touched_modules = (
            added_modules
            | updated_modules
            | deleted_modules
        )

        # -------------------------
        # 全ファイルの事前検証
        # -------------------------

        for relative, content in remote_files.items():

            path = (
                ROOT_DIR / Path(*PurePosixPath(relative).parts)
            ).resolve()

            path.relative_to(COGS_DIR.resolve())

            compile(
                content.decode("utf-8"),
                str(path),
                "exec"
            )

        # -------------------------
        # ファイル更新
        # -------------------------

        try:

            for relative in sorted(added_paths | updated_paths):

                path = (
                    ROOT_DIR / Path(*PurePosixPath(relative).parts)
                ).resolve()

                path.relative_to(COGS_DIR.resolve())

                atomic_write(path, remote_files[relative])
                invalidate_module_cache(path)

            # -------------------------
            # 削除
            # -------------------------

            for relative in sorted(deleted_paths):

                path = local_files[relative]
                path.resolve().relative_to(COGS_DIR.resolve())

                if path.exists():
                    path.unlink()

                invalidate_module_cache(path)

            # -------------------------
            # Extension操作
            # -------------------------

            # 削除されたExtensionをアンロード
            for module in sorted(deleted_modules):

                if module in bot.extensions:
                    await bot.unload_extension(module)

            # 変更されたExtensionを再読み込み
            for module in sorted(updated_modules):

                if module not in bot.extensions:
                    continue

                await bot.reload_extension(module)

            # 新しいExtensionを読み込み
            for module in sorted(added_modules):

                if module in bot.extensions:
                    await bot.reload_extension(module)
                else:
                    await bot.load_extension(module)

            # -------------------------
            # 結果記録
            # -------------------------

            added = sorted(added_paths)
            updated = sorted(updated_paths)
            deleted = sorted(deleted_paths)

        except Exception:

            # -------------------------
            # ファイル復元
            # -------------------------

            for relative, content in original_contents.items():

                path = local_files[relative]

                if not path.exists() or path.read_bytes() != content:
                    atomic_write(path, content)

                invalidate_module_cache(path)

            # 新規ファイルを削除
            for relative in added_paths:

                path = (
                    ROOT_DIR / Path(*PurePosixPath(relative).parts)
                ).resolve()

                if path.exists():
                    path.unlink()

                invalidate_module_cache(path)

            # -------------------------
            # Extension状態の復元
            # -------------------------

            for module in sorted(touched_modules):

                try:
                    was_loaded = module in original_extensions
                    is_loaded = module in bot.extensions

                    if was_loaded and is_loaded:
                        await bot.reload_extension(module)

                    elif was_loaded and not is_loaded:
                        await bot.load_extension(module)

                    elif not was_loaded and is_loaded:
                        await bot.unload_extension(module)

                except Exception as restore_error:
                    failed[module] = (
                        "復元失敗: "
                        f"{type(restore_error).__name__}: "
                        f"{restore_error}"
                    )

            raise

        # -------------------------
        # Slash同期
        # -------------------------

        slash_result = True

        try:
            await bot.tree.sync()

        except Exception as error:
            slash_result = False
            failed["Slash同期"] = (
                f"{type(error).__name__}: {error}"
            )

        return (
            added,
            updated,
            deleted,
            failed,
            slash_result
        )


# =========================
# Cog
# =========================

class Sync(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    # -------------------------
    # Prefix command
    # -------------------------

    @commands.command(name="sync")
    async def sync_prefix(self, ctx):

        if not is_admin(ctx.author.id):
            return

        message = await ctx.reply(
            "🔄 **GitHubとの同期を開始します...**"
        )

        try:

            (
                added,
                updated,
                deleted,
                failed,
                slash_result
            ) = await synchronize(self.bot)

            embed = build_embed(
                added,
                updated,
                deleted,
                failed,
                slash_result
            )

            await message.edit(
                content=None,
                embed=embed
            )

        except Exception as error:

            await message.edit(
                content=(
                    "❌ **同期に失敗しました**\n"
                    f"`{type(error).__name__}: {error}`"
                )
            )

    # -------------------------
    # Slash command
    # -------------------------

    @app_commands.command(
        name="sync",
        description="GitHubからCogを取得して同期します"
    )
    async def sync_slash(
        self,
        interaction: discord.Interaction
    ):

        if not is_admin(interaction.user.id):
            await interaction.response.send_message(
                "管理者専用コマンドです。",
                ephemeral=True
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:

            (
                added,
                updated,
                deleted,
                failed,
                slash_result
            ) = await synchronize(self.bot)

            embed = build_embed(
                added,
                updated,
                deleted,
                failed,
                slash_result
            )

            await interaction.followup.send(
                embed=embed,
                ephemeral=True
            )

        except Exception as error:

            await interaction.followup.send(
                (
                    "❌ **同期に失敗しました**\n"
                    f"`{type(error).__name__}: {error}`"
                ),
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(Sync(bot))
    print("sync.py cog loaded")
