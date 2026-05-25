"""Download the latest video shown in any open chat.qwen.ai tab."""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def main(scene_no: int = 1, cdp: str = "http://localhost:9222", suffix: str = "") -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp)
        ctx = browser.contexts[0]
        qwen_pages = [p for p in ctx.pages if "qwen.ai/c/" in (p.url or "")]
        if not qwen_pages:
            print("No qwen chat tab open")
            return
        page = qwen_pages[-1]
        await page.bring_to_front()

        video = page.locator(".video-bg-card").last
        await video.wait_for(state="attached", timeout=10_000)
        src = await video.get_attribute("src")
        if not src or not src.startswith("http"):
            print(f"No HTTPS src on video: {src!r}")
            return
        print(f"Fetching: {src[:120]}...")
        resp = await page.context.request.get(src)
        body = await resp.body()
        suf = f"_{suffix}" if suffix else ""
        out = Path(__file__).parent / "downloads" / f"scene_{scene_no:02d}{suf}.mp4"
        out.parent.mkdir(exist_ok=True)
        out.write_bytes(body)
        print(f"Saved: {out} ({len(body)//1024} KB)")


if __name__ == "__main__":
    scene = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    cdp = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:9222"
    suffix = sys.argv[3] if len(sys.argv) > 3 else ""
    asyncio.run(main(scene, cdp, suffix))
