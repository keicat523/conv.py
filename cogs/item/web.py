import asyncio
import math
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image
from playwright.async_api import async_playwright


A4_HEIGHT_RATIO = math.sqrt(2)
OVERLAP_CSS_PX = 190
SCREENSHOT_WIDTH = 1280
MIN_SCREENSHOT_WIDTH = 720
DISCORD_SAFE_FILE_LIMIT = 8 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 10


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _safe_filename_part(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value)
    return value.strip("._")[:40] or "page"


def _file_limit_for(ctx: commands.Context) -> int:
    guild_limit = getattr(getattr(ctx, "guild", None), "filesize_limit", None)
    if isinstance(guild_limit, int) and guild_limit > 0:
        return max(1, min(guild_limit, DISCORD_SAFE_FILE_LIMIT) - 256 * 1024)
    return DISCORD_SAFE_FILE_LIMIT - 256 * 1024


def _file_limit_for_interaction(interaction: discord.Interaction) -> int:
    guild_limit = getattr(getattr(interaction, "guild", None), "filesize_limit", None)
    if isinstance(guild_limit, int) and guild_limit > 0:
        return max(1, min(guild_limit, DISCORD_SAFE_FILE_LIMIT) - 256 * 1024)
    return DISCORD_SAFE_FILE_LIMIT - 256 * 1024


async def _expand_wiki_sections(page) -> None:
    await page.evaluate(
        """
        () => {
            document.querySelectorAll('details:not([open])').forEach((el) => {
                el.setAttribute('open', '');
            });

            const selectors = [
                '.mw-collapsible-toggle-collapsed',
                '.mw-collapsed .mw-collapsible-toggle',
                '.collapsible.collapsed .mw-collapsible-toggle',
                '.oo-ui-buttonElement-button[aria-expanded="false"]'
            ];

            for (const selector of selectors) {
                document.querySelectorAll(selector).forEach((el) => {
                    if (typeof el.click === 'function') {
                        el.click();
                    }
                });
            }

            document.querySelectorAll('[aria-expanded="false"]').forEach((el) => {
                const text = (el.textContent || '').toLowerCase();
                if (
                    text.includes('subcategor') ||
                    text.includes('カテゴリ') ||
                    text.includes('category')
                ) {
                    el.click();
                }
            });
        }
        """
    )
    await page.wait_for_timeout(500)


async def _capture_page_parts(url: str, output_dir: Path, file_limit: int) -> list[Path]:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page_paths: list[Path] = []
            width = SCREENSHOT_WIDTH

            while True:
                page_paths.clear()
                context = await browser.new_context(
                    viewport={"width": width, "height": int(width * A4_HEIGHT_RATIO)},
                    device_scale_factor=1,
                    ignore_https_errors=True,
                )
                page = await context.new_page()

                try:
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=45000)
                    except Exception:
                        await page.goto(url, wait_until="domcontentloaded", timeout=45000)

                    await _expand_wiki_sections(page)

                    metrics = await page.evaluate(
                        """
                        () => ({
                            width: Math.ceil(Math.max(
                                document.documentElement.scrollWidth,
                                document.body ? document.body.scrollWidth : 0,
                                window.innerWidth
                            )),
                            height: Math.ceil(Math.max(
                                document.documentElement.scrollHeight,
                                document.body ? document.body.scrollHeight : 0,
                                window.innerHeight
                            ))
                        })
                        """
                    )
                    capture_width = min(width, max(1, int(metrics["width"])))
                    total_height = max(1, int(metrics["height"]))
                    page_height = max(1, int(capture_width * A4_HEIGHT_RATIO))
                    overlap = min(OVERLAP_CSS_PX, max(0, page_height - 1))
                    step = max(1, page_height - overlap)
                    url_part = _safe_filename_part(urlparse(url).netloc)

                    y = 0
                    part = 1
                    while y < total_height:
                        clip_height = min(page_height, total_height - y)
                        path = output_dir / f"web_{url_part}_{part:02d}.jpg"
                        await page.set_viewport_size(
                            {"width": width, "height": max(1, clip_height)}
                        )
                        await page.evaluate("(scrollY) => window.scrollTo(0, scrollY)", y)
                        await page.wait_for_timeout(150)
                        await page.screenshot(
                            path=str(path),
                            type="jpeg",
                            quality=82,
                            clip={
                                "x": 0,
                                "y": 0,
                                "width": capture_width,
                                "height": clip_height,
                            },
                        )
                        page_paths.append(path)
                        if y + clip_height >= total_height:
                            break
                        y += step
                        part += 1
                finally:
                    await context.close()

                oversized = [path for path in page_paths if path.stat().st_size > file_limit]
                if not oversized or width <= MIN_SCREENSHOT_WIDTH:
                    break

                for path in page_paths:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                width = max(MIN_SCREENSHOT_WIDTH, int(width * 0.85))

            for path in list(page_paths):
                if path.stat().st_size > file_limit:
                    await asyncio.to_thread(_compress_image, path, file_limit)

            return page_paths
        finally:
            await browser.close()


def _compress_image(path: Path, file_limit: int) -> None:
    with Image.open(path) as image:
        image = image.convert("RGB")
        for quality in (76, 68, 60, 52, 44, 36, 30):
            image.save(path, "JPEG", quality=quality, optimize=True)
            if path.stat().st_size <= file_limit:
                return

        width, height = image.size
        while path.stat().st_size > file_limit and width > MIN_SCREENSHOT_WIDTH:
            width = max(MIN_SCREENSHOT_WIDTH, int(width * 0.85))
            height = max(1, int(image.size[1] * (width / image.size[0])))
            resized = image.resize((width, height), Image.Resampling.LANCZOS)
            resized.save(path, "JPEG", quality=40, optimize=True)
            image = resized


class Web(commands.Cog):
    web_slash = app_commands.Group(name="web", description="Webページのスクリーンショットを送信します")

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="web", invoke_without_command=True)
    async def web(self, ctx: commands.Context):
        await ctx.reply("使い方: `c!web open <link>`")

    @web.command(name="open")
    async def web_open(self, ctx: commands.Context, link: str | None = None):
        if not link:
            await ctx.reply("リンクがありません。使い方: `c!web open <link>`")
            return

        if not _is_http_url(link):
            await ctx.reply("有効な `http://` または `https://` のリンクを指定してください。")
            return

        file_limit = _file_limit_for(ctx)
        await ctx.reply("ページを読み込んでスクショを作成しています...")

        try:
            with tempfile.TemporaryDirectory(prefix="conv_web_") as tmp:
                paths = await _capture_page_parts(link, Path(tmp), file_limit)
                if not paths:
                    await ctx.send("スクショを作成できませんでした。")
                    return

                total = len(paths)
                for start in range(0, total, MAX_FILES_PER_MESSAGE):
                    chunk = paths[start:start + MAX_FILES_PER_MESSAGE]
                    files = [discord.File(path, filename=path.name) for path in chunk]
                    page_from = start + 1
                    page_to = start + len(chunk)
                    await ctx.send(
                        content=f"web screenshot {page_from}-{page_to}/{total}",
                        files=files,
                    )
        except Exception as exc:
            message = str(exc)
            if "Executable doesn't exist" in message or "playwright install" in message:
                await ctx.send("Playwright のブラウザが未インストールです。`playwright install chromium` を実行してください。")
                return
            await ctx.send(f"スクショの作成に失敗しました: `{message[:1800]}`")


    @web_slash.command(name="open", description="Webページ全体のスクリーンショットを送信します")
    @app_commands.describe(link="スクリーンショットを撮る http:// または https:// のリンク")
    async def web_open_slash(self, interaction: discord.Interaction, link: str):
        if not _is_http_url(link):
            await interaction.response.send_message(
                "有効な `http://` または `https://` のリンクを指定してください。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        file_limit = _file_limit_for_interaction(interaction)

        try:
            await interaction.followup.send("ページを読み込んでスクショを作成しています...")
            with tempfile.TemporaryDirectory(prefix="conv_web_") as tmp:
                paths = await _capture_page_parts(link, Path(tmp), file_limit)
                if not paths:
                    await interaction.followup.send("スクショを作成できませんでした。")
                    return

                total = len(paths)
                for start in range(0, total, MAX_FILES_PER_MESSAGE):
                    chunk = paths[start:start + MAX_FILES_PER_MESSAGE]
                    files = [discord.File(path, filename=path.name) for path in chunk]
                    page_from = start + 1
                    page_to = start + len(chunk)
                    await interaction.followup.send(
                        content=f"web screenshot {page_from}-{page_to}/{total}",
                        files=files,
                    )
        except Exception as exc:
            message = str(exc)
            if "Executable doesn't exist" in message or "playwright install" in message:
                await interaction.followup.send(
                    "Playwright のブラウザが未インストールです。`playwright install chromium` を実行してください。"
                )
                return
            await interaction.followup.send(f"スクショの作成に失敗しました: `{message[:1800]}`")


async def setup(bot):
    await bot.add_cog(Web(bot))
    print("web.py cog loaded")
