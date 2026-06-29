import asyncio
import math
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image
from playwright.async_api import async_playwright

import config
from utils.timeout_manager import get_menu_timeout_seconds


A4_HEIGHT_RATIO = math.sqrt(2)
OVERLAP_CSS_PX = 190
SCREENSHOT_WIDTH = 1280
MIN_SCREENSHOT_WIDTH = 720
DISCORD_SAFE_FILE_LIMIT = 8 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 10
SEARCH_RESULTS_PER_PAGE = 6
SEARCH_DESCRIPTION_LIMIT = 2000
SEARCH_RESULT_LIMIT = 30


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _safe_filename_part(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value)
    return value.strip("._")[:40] or "page"


def _clean_google_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.netloc and parsed.path.startswith("/url"):
        target = parse_qs(parsed.query).get("q", [""])[0]
        if target:
            return target

    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        target = parse_qs(parsed.query).get("q", [""])[0]
        if target:
            return target
    return value


def _is_search_result_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    blocked_hosts = (
        "google.com",
        "www.google.com",
        "accounts.google.com",
        "support.google.com",
        "policies.google.com",
        "webcache.googleusercontent.com",
    )
    return parsed.netloc.lower() not in blocked_hosts


def _escape_markdown_link_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _safe_markdown_url(value: str) -> str:
    return value.replace(")", "%29").replace(" ", "%20")


def _truncate(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


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


def _is_navigation_evaluate_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        "Execution context was destroyed" in message
        or "Cannot find context with specified id" in message
    )


async def _evaluate_with_navigation_retry(page, script: str, *args):
    last_error = None
    for attempt in range(3):
        try:
            return await page.evaluate(script, *args)
        except Exception as exc:
            if not _is_navigation_evaluate_error(exc):
                raise
            last_error = exc
            if attempt >= 2:
                break
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(300)
    raise last_error


async def _expand_wiki_sections(page) -> None:
    await _evaluate_with_navigation_retry(
        page,
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
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=3000)
    except Exception:
        pass
    await page.wait_for_timeout(500)


async def _google_search(query: str) -> list[dict[str, str]]:
    search_url = f"https://www.google.com/search?q={quote_plus(query)}&num={SEARCH_RESULT_LIMIT}&hl=ja&udm=14"
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="ja-JP",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            try:
                await page.goto(search_url, wait_until="networkidle", timeout=30000)
            except Exception:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            raw_results = await _evaluate_with_navigation_retry(
                page,
                """
                () => {
                    const results = [];
                    const seen = new Set();

                    const cleanText = (value) => (value || '').replace(/\\s+/g, ' ').trim();

                    const findContainer = (el) => {
                        let node = el;
                        for (let i = 0; i < 6 && node; i += 1) {
                            const text = cleanText(node.innerText || node.textContent || '');
                            if (text.length > 80) {
                                return node;
                            }
                            node = node.parentElement;
                        }
                        return el.parentElement || el;
                    };

                    const findSnippet = (container, title) => {
                        const preferred = container.querySelector(
                            '.VwiC3b, .IsZvec, [data-sncf], .kb0PBd, .aCOpRe, .st'
                        );
                        if (preferred) {
                            return cleanText(preferred.innerText || preferred.textContent || '');
                        }

                        const text = cleanText(container.innerText || container.textContent || '');
                        if (!text) {
                            return '';
                        }
                        return cleanText(text.replace(title, ''));
                    };

                    const titleLinks = Array.from(document.querySelectorAll('a[href]'))
                        .map((link) => ({ link, titleEl: link.querySelector('h3') }))
                        .filter((entry) => entry.titleEl);

                    for (const { link, titleEl } of titleLinks) {
                        const url = link.href || link.getAttribute('href') || '';
                        const title = cleanText(titleEl.innerText || titleEl.textContent || '');
                        if (!url || !title || seen.has(url)) {
                            continue;
                        }

                        const container = findContainer(link);
                        const snippet = findSnippet(container, title);
                        seen.add(url);
                        results.push({ title, url, snippet });
                    }

                    if (results.length > 0) {
                        return results;
                    }

                    const fallbackLinks = Array.from(document.querySelectorAll('a[href]'));
                    for (const link of fallbackLinks) {
                        const url = link.href || link.getAttribute('href') || '';
                        const text = cleanText(link.innerText || link.textContent || '');
                        if (!url || !text || text.length < 3 || seen.has(url)) {
                            continue;
                        }
                        seen.add(url);
                        results.push({ title: text, url, snippet: '' });
                        if (results.length >= 40) {
                            break;
                        }
                    }

                    return results;
                }
                """
            )
        finally:
            await browser.close()

    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in raw_results:
        url = _clean_google_url(str(item.get("url", "")))
        if not _is_search_result_url(url) or url in seen_urls:
            continue
        title = _truncate(str(item.get("title", "")), 120)
        snippet = _truncate(str(item.get("snippet", "")), 240)
        if not title:
            continue
        seen_urls.add(url)
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= SEARCH_RESULT_LIMIT:
            break

    return results


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

                    metrics = await _evaluate_with_navigation_retry(
                        page,
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
                        await _evaluate_with_navigation_retry(
                            page,
                            "(scrollY) => window.scrollTo(0, scrollY)",
                            y,
                        )
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


    def _format_search_result(self, index: int, result: dict[str, str]) -> str:
        title = _escape_markdown_link_text(result["title"])
        url = _safe_markdown_url(result["url"])
        snippet = result.get("snippet") or "No snippet."
        return f"**{index}. [{title}]({url})**\n{snippet}"

    def _fit_search_result(self, index: int, result: dict[str, str]) -> dict[str, str]:
        if len(self._format_search_result(index, result)) <= SEARCH_DESCRIPTION_LIMIT:
            return result

        fitted = {**result, "snippet": _truncate(result.get("snippet", ""), 140)}
        if len(self._format_search_result(index, fitted)) <= SEARCH_DESCRIPTION_LIMIT:
            return fitted

        fitted["snippet"] = ""
        fitted["title"] = _truncate(fitted["title"], 80)
        return fitted

    def _build_search_pages(self, query: str, results: list[dict[str, str]]) -> list[discord.Embed]:
        pages: list[list[tuple[int, dict[str, str]]]] = []
        current: list[tuple[int, dict[str, str]]] = []
        current_len = 0

        for index, result in enumerate(results, start=1):
            result = self._fit_search_result(index, result)
            line = self._format_search_result(index, result)
            line_len = len(line) + (2 if current else 0)
            if current and (
                len(current) >= SEARCH_RESULTS_PER_PAGE
                or current_len + line_len > SEARCH_DESCRIPTION_LIMIT
            ):
                pages.append(current)
                current = []
                current_len = 0

            current.append((index, result))
            current_len += len(line) + (2 if current_len else 0)

        if current:
            pages.append(current)

        embeds: list[discord.Embed] = []
        total = len(pages)
        for page_index, page_results in enumerate(pages, start=1):
            description = "\n\n".join(
                self._format_search_result(index, result)
                for index, result in page_results
            )
            embed = discord.Embed(
                title=f"Google Search: {query}",
                description=description[:SEARCH_DESCRIPTION_LIMIT],
                color=discord.Color.blue(),
            )
            embed.set_footer(text=f"Page {page_index}/{total}")
            embeds.append(embed)
        return embeds

    async def _clear_search_reactions(self, msg: discord.Message) -> None:
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

    async def _send_search_menu(
        self,
        ctx: commands.Context,
        query: str,
        results: list[dict[str, str]],
    ) -> None:
        if not results:
            await ctx.reply("検索結果が見つかりませんでした。")
            return

        pages = self._build_search_pages(query, results)
        index = 0
        msg = await ctx.reply(embed=pages[index])

        if len(pages) <= 1:
            return

        for emoji in (
            config.FIRST_EMOJI,
            config.PREV_EMOJI,
            config.NEXT_EMOJI,
            config.LAST_EMOJI,
        ):
            await msg.add_reaction(emoji)

        def check(reaction, user):
            return (
                user == ctx.author
                and reaction.message.id == msg.id
                and str(reaction.emoji) in {
                    config.FIRST_EMOJI,
                    config.PREV_EMOJI,
                    config.NEXT_EMOJI,
                    config.LAST_EMOJI,
                }
            )

        while True:
            try:
                reaction, user = await self.bot.wait_for(
                    "reaction_add",
                    timeout=get_menu_timeout_seconds(),
                    check=check,
                )
            except TimeoutError:
                await self._clear_search_reactions(msg)
                break
            except Exception:
                break

            try:
                await msg.remove_reaction(reaction, user)
            except Exception:
                pass

            if str(reaction.emoji) == config.FIRST_EMOJI:
                index = 0
            elif str(reaction.emoji) == config.LAST_EMOJI:
                index = len(pages) - 1
            elif str(reaction.emoji) == config.NEXT_EMOJI:
                index = (index + 1) % len(pages)
            else:
                index = (index - 1) % len(pages)

            await msg.edit(embed=pages[index])

    @web.command(name="search")
    async def web_search(self, ctx: commands.Context, *terms: str):
        query = " ".join(terms).strip()
        if not query:
            await ctx.reply("検索語がありません。使い方: `c!web search <s1> <s2> ...`")
            return

        await ctx.reply("Googleで検索しています...")
        try:
            results = await _google_search(query)
        except Exception as exc:
            await ctx.send(f"検索に失敗しました: `{str(exc)[:1800]}`")
            return

        await self._send_search_menu(ctx, query, results)

    async def _send_search_menu_interaction(
        self,
        interaction: discord.Interaction,
        query: str,
        results: list[dict[str, str]],
    ) -> None:
        if not results:
            await interaction.followup.send("検索結果が見つかりませんでした。")
            return

        pages = self._build_search_pages(query, results)
        index = 0
        msg = await interaction.followup.send(embed=pages[index], wait=True)

        if len(pages) <= 1:
            return

        for emoji in (
            config.FIRST_EMOJI,
            config.PREV_EMOJI,
            config.NEXT_EMOJI,
            config.LAST_EMOJI,
        ):
            await msg.add_reaction(emoji)

        def check(reaction, user):
            return (
                user == interaction.user
                and reaction.message.id == msg.id
                and str(reaction.emoji) in {
                    config.FIRST_EMOJI,
                    config.PREV_EMOJI,
                    config.NEXT_EMOJI,
                    config.LAST_EMOJI,
                }
            )

        while True:
            try:
                reaction, user = await self.bot.wait_for(
                    "reaction_add",
                    timeout=get_menu_timeout_seconds(),
                    check=check,
                )
            except TimeoutError:
                await self._clear_search_reactions(msg)
                break
            except Exception:
                break

            try:
                await msg.remove_reaction(reaction, user)
            except Exception:
                pass

            if str(reaction.emoji) == config.FIRST_EMOJI:
                index = 0
            elif str(reaction.emoji) == config.LAST_EMOJI:
                index = len(pages) - 1
            elif str(reaction.emoji) == config.NEXT_EMOJI:
                index = (index + 1) % len(pages)
            else:
                index = (index - 1) % len(pages)

            await msg.edit(embed=pages[index])

    @web_slash.command(name="search", description="Google検索結果をEmbedメニューで表示します")
    @app_commands.describe(query="Googleで検索する語句")
    async def web_search_slash(self, interaction: discord.Interaction, query: str):
        query = query.strip()
        if not query:
            await interaction.response.send_message("検索語がありません。", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        await interaction.followup.send("Googleで検索しています...")
        try:
            results = await _google_search(query)
        except Exception as exc:
            await interaction.followup.send(f"検索に失敗しました: `{str(exc)[:1800]}`")
            return

        await self._send_search_menu_interaction(interaction, query, results)


async def setup(bot):
    await bot.add_cog(Web(bot))
    print("web.py cog loaded")
