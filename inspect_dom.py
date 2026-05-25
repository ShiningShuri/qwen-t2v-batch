"""
Connect to the already-running Chrome (CDP) and dump every interesting
selector on the chat.qwen.ai composer so we can build robust locators.
"""

import asyncio
import json

from playwright.async_api import async_playwright

CDP_URL = "http://localhost:9222"


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
        page = None
        for p in context.pages:
            if "qwen.ai" in (p.url or ""):
                page = p
                break
        if page is None:
            print("No qwen.ai tab open. Open one in the debug Chrome first.")
            return

        await page.bring_to_front()
        await page.wait_for_load_state("domcontentloaded")

        # Dump the entire mode-select sub-tree so we can see what's around #filesUpload.
        mode_select_html = await page.evaluate(
            "() => document.querySelector('.mode-select')?.outerHTML?.slice(0, 5000) || 'NOT FOUND'"
        )
        print("=== .mode-select outerHTML ===")
        print(mode_select_html)
        print("=== end mode-select ===\n")

        info = await page.evaluate(
            """
            () => {
                const ta = document.querySelector('textarea');
                const taRect = ta?.getBoundingClientRect();
                const out = {
                    textarea: ta ? {
                        placeholder: ta.placeholder,
                        classes: ta.className,
                        x: Math.round(taRect.x), y: Math.round(taRect.y),
                        w: Math.round(taRect.width), h: Math.round(taRect.height),
                    } : null,
                    buttons: [],
                    fileInputs: [],
                };

                // Every visible button in the viewport
                for (const b of document.querySelectorAll('button')) {
                    const r = b.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    // Only buttons in the bottom half of the page (composer area)
                    if (taRect && r.y < taRect.y - 100) continue;
                    out.buttons.push({
                        text: b.innerText?.trim()?.slice(0, 40) || '',
                        ariaLabel: b.getAttribute('aria-label') || '',
                        title: b.getAttribute('title') || '',
                        id: b.id || '',
                        dataTestId: b.getAttribute('data-testid') || '',
                        classes: b.className,
                        hasSvg: !!b.querySelector('svg'),
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        w: Math.round(r.width),
                        h: Math.round(r.height),
                        outer: b.outerHTML.slice(0, 220),
                    });
                }

                // All file inputs
                for (const i of document.querySelectorAll('input[type=file]')) {
                    out.fileInputs.push({
                        accept: i.getAttribute('accept') || '',
                        name: i.getAttribute('name') || '',
                        classes: i.className,
                        id: i.id,
                        parentTag: i.parentElement?.tagName,
                        parentClasses: i.parentElement?.className,
                        outer: i.outerHTML.slice(0, 200),
                    });
                }

                // Also try to look at div/span items that act as buttons in the
                // composer toolbar (Qwen often uses non-button clickables).
                out.clickables = [];
                const composerArea = ta?.closest('.chat-message-input, [class*="input"], [class*="composer"]') || ta?.parentElement?.parentElement;
                if (composerArea) {
                    out.composerHTML = composerArea.outerHTML.slice(0, 2000);
                }
                return out;
            }
            """
        )
        print(json.dumps(info, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
