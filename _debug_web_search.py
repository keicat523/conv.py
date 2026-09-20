import asyncio
from urllib.parse import quote_plus

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        for name, url in [
            (
                "google",
                f"https://www.google.com/search?q={quote_plus('python')}&num=30&hl=ja&udm=14",
            ),
            ("duck", f"https://html.duckduckgo.com/html/?q={quote_plus('python')}"),
            ("bing", f"https://www.bing.com/search?q={quote_plus('python')}&count=30&setlang=ja-JP"),
        ]:
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            counts = await page.evaluate(
                """
                () => ({
                    title: document.title,
                    url: location.href,
                    h3: document.querySelectorAll('h3').length,
                    links: document.querySelectorAll('a[href]').length,
                    result: document.querySelectorAll('.result,.web-result').length,
                    body: (document.body.innerText || '').slice(0, 1000)
                })
                """
            )
            print("---", name)
            print(repr(counts).encode("ascii", "backslashreplace").decode("ascii"))
            await page.close()
        await browser.close()


asyncio.run(main())
