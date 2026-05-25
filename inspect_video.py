"""Find Qwen download button selector by inspecting the page after a video is generated."""
import asyncio
import json

from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp("http://localhost:9222")
        ctx = browser.contexts[0]
        # List ALL qwen.ai tabs and pick the most recently active one
        qwen_pages = [p for p in ctx.pages if "qwen.ai" in (p.url or "")]
        print(f"Found {len(qwen_pages)} qwen.ai tab(s):")
        for i, p in enumerate(qwen_pages):
            print(f"  [{i}] {p.url}")
        if not qwen_pages:
            print("No qwen.ai tab open")
            return
        # use the LAST one (most likely the one with the new video)
        page = qwen_pages[-1]
        await page.bring_to_front()

        info = await page.evaluate(
            """() => {
                // Try multiple media element types Qwen might use
                const videos = document.querySelectorAll('video');
                const playButtons = document.querySelectorAll('[class*="play" i]:not(button), .video-thumbnail, [class*="video" i] img, [class*="media" i]');
                const out = {
                    videoCount: videos.length,
                    videos: [],
                    buttonsNearVideo: [],
                    mediaElements: [],
                };
                for (const el of playButtons) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 50 || r.height < 50) continue;
                    out.mediaElements.push({
                        tag: el.tagName,
                        classes: el.className,
                        src: el.src || el.getAttribute('src') || '',
                        bgImage: getComputedStyle(el).backgroundImage,
                        x: Math.round(r.x), y: Math.round(r.y),
                        w: Math.round(r.width), h: Math.round(r.height),
                        outer: el.outerHTML.slice(0, 300),
                    });
                }
                for (const v of videos) {
                    const r = v.getBoundingClientRect();
                    out.videos.push({
                        src: v.src || v.currentSrc || '',
                        poster: v.poster || '',
                        classes: v.className,
                        x: Math.round(r.x), y: Math.round(r.y),
                        w: Math.round(r.width), h: Math.round(r.height),
                        parentClasses: v.parentElement?.className || '',
                        grandparent: v.parentElement?.parentElement?.outerHTML?.slice(0, 800) || '',
                    });
                }
                // Find all buttons / clickable elements near the last media element
                const lastVideo = videos[videos.length - 1] || playButtons[playButtons.length - 1];
                if (lastVideo) {
                    const vRect = lastVideo.getBoundingClientRect();
                    const cy = vRect.y + vRect.height / 2;
                    for (const el of document.querySelectorAll('button, [role="button"], a, [class*="download" i], [class*="action" i]')) {
                        const r = el.getBoundingClientRect();
                        if (r.width === 0 || r.height === 0) continue;
                        // within 200px vertically of the video
                        if (Math.abs((r.y + r.height/2) - cy) > 400) continue;
                        out.buttonsNearVideo.push({
                            tag: el.tagName,
                            text: el.innerText?.trim()?.slice(0, 40) || '',
                            ariaLabel: el.getAttribute('aria-label') || '',
                            title: el.getAttribute('title') || '',
                            classes: el.className,
                            href: el.getAttribute('href') || '',
                            download: el.getAttribute('download') || '',
                            x: Math.round(r.x), y: Math.round(r.y),
                            outer: el.outerHTML.slice(0, 250),
                        });
                    }
                }
                return out;
            }"""
        )
        with open("inspect_out.json", "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2, ensure_ascii=False)
        print("Wrote inspect_out.json")


if __name__ == "__main__":
    asyncio.run(main())
