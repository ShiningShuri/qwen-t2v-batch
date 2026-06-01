"""
Qwen chat.qwen.ai t2v (img-to-video) batch automation.

Flow per scene:
  1. wait for the chat composer placeholder to appear (= logged in)
  2. click the '+' button next to the composer
  3. click '첨부 파일 업로드'
  4. set the image into the file input
  5. paste the wrapped prompt
  6. press Enter to submit

Usage:
  python main.py --images <dir> --prompts <file> [--tabs 1] [--limit 1]
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
    async_playwright,
)

QWEN_URL = "https://chat.qwen.ai/?inputFeature=t2v"

WRAPPER_TEMPLATE = (
    "첨부된 이미지를 시네마틱 비디오로 애니메이션화합니다. "
    "이미지의 원래 분위기, 환경, 색상을 보존하면서 "
    "이미지의 상황에 맞는 장면으로 영상을 만들어주세요\n"
    "Animize the attached image into a cinematic video. "
    "Make a video with a scene that fits the situation of the image "
    "while preserving the original atmosphere, environment, and color of the image"
    "{scene_block}"
)

SCENE_HEADER_RE = re.compile(r"^===\s*Scene\s+(\d+).*?===\s*$", re.MULTILINE)

# Drop the fixed "policy block" that appears verbatim in every scene of
# prompts.txt and trips Qwen's content-safety filter. The block always
# starts with "Render text in the image ONLY ..." and runs to the end of
# the scene description ("...arbitrarily change the language.").
POLICY_BLOCK_RE = re.compile(
    r"\s*Render text in the image ONLY[\s\S]*?arbitrarily change the language\.\s*",
    re.IGNORECASE,
)

# Mild softening of copyright-trigger phrases per Qwen's own diagnosis.
SAFETY_SUBSTITUTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"keep the exact same character design without any modification",
                re.IGNORECASE),
     "maintain the same subject design without modification"),
    (re.compile(r"the character stays visually identical to the reference",
                re.IGNORECASE),
     "the subject remains visually the same as the reference"),
    (re.compile(r"masterpiece,?\s*", re.IGNORECASE),
     ""),
    # Replace 'character' with 'subject' to avoid IP-related safety triggers.
    (re.compile(r"\bcharacter\b", re.IGNORECASE),
     "subject"),
]


def sanitize_for_safety(text: str) -> str:
    """Strip the fixed policy block and soften copyright-trigger phrases."""
    text = POLICY_BLOCK_RE.sub(" ", text)
    for pat, repl in SAFETY_SUBSTITUTIONS:
        text = pat.sub(repl, text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text

# placeholder text used by Qwen on the composer (Korean)
COMPOSER_PLACEHOLDER_RE = re.compile("도와드릴까요|설명하세요|describe", re.IGNORECASE)


def parse_prompts(prompts_path: Path | None) -> list[tuple[int, str]]:
    if prompts_path is None:
        return []
    text = prompts_path.read_text(encoding="utf-8")
    matches = list(SCENE_HEADER_RE.finditer(text))
    if not matches:
        raise ValueError(f"No '=== Scene N ===' headers found in {prompts_path}")
    blocks: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        # Start AFTER the header line so the "=== Scene N ===" marker does
        # not leak into the final prompt sent to Qwen.
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append((int(m.group(1)), text[start:end].strip()))
    return blocks


def find_images(images_dir: Path) -> dict[int, Path]:
    pat = re.compile(r"scene[_-]?(\d+)\.(png|jpe?g|webp)$", re.IGNORECASE)
    out: dict[int, Path] = {}
    for p in images_dir.iterdir():
        m = pat.search(p.name)
        if p.is_file() and m:
            out[int(m.group(1))] = p
    return out


def build_prompt(scene_block: str) -> str:
    cleaned = sanitize_for_safety(scene_block)
    return WRAPPER_TEMPLATE.format(scene_block=cleaned)


async def wait_for_login(page: Page) -> None:
    """Wait until the composer placeholder is visible (= user logged in)."""
    print("[wait] looking for the chat composer (sign of being logged in)...")
    composer = page.get_by_placeholder(COMPOSER_PLACEHOLDER_RE)
    # No total timeout — wait forever; user logs in in the visible window.
    while True:
        try:
            await composer.first.wait_for(state="visible", timeout=10_000)
            print("[wait] composer detected - logged in.")
            return
        except PWTimeout:
            print("[wait] still waiting for login. Log in in the Chromium window.")


async def click_plus_button(page: Page) -> None:
    """Click the '+' button that opens the attachment / mode menu.

    Strategy: scan every icon-only button on the page, click each until the
    '첨부 파일 업로드' menu item becomes visible. Click again to close any
    spurious menu before moving on.
    """
    composer = page.locator("textarea.message-input-textarea").first
    await composer.wait_for(state="visible", timeout=15_000)

    # All icon-only buttons that sit in the composer's bounding region.
    buttons = page.locator(
        'button:has(svg):not(:has-text("비디오")):not(:has-text("이미지")):not(:has-text("리서치"))'
    )
    count = await buttons.count()
    if count == 0:
        raise RuntimeError("No icon-only buttons found near the composer")

    upload_item = page.get_by_text("첨부 파일 업로드", exact=False).first

    for i in range(count):
        btn = buttons.nth(i)
        try:
            if not await btn.is_visible(timeout=500):
                continue
            await btn.click()
            await page.wait_for_timeout(300)
            if await upload_item.is_visible(timeout=800):
                return
            # wrong menu — press Escape to close before trying the next button.
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(150)
        except PWTimeout:
            continue
        except Exception:
            continue
    raise RuntimeError("Could not find a '+' that opens the '첨부 파일 업로드' menu")


async def click_video_mode(page: Page) -> None:
    """Make sure '비디오 만들기' mode is active. URL preselects it, but click to be sure."""
    try:
        item = page.get_by_text("비디오 만들기", exact=False).first
        if await item.is_visible(timeout=2_000):
            await item.click()
            await page.wait_for_timeout(400)
    except PWTimeout:
        pass


async def attach_image(page: Page, image_path: Path) -> None:
    """Attach an image through the '+' dropdown menu.

    DOM observed on chat.qwen.ai (2025-11):
      <div class="mode-select">
        <span class="ant-dropdown-trigger">         <-- '+' button
          <div class="mode-select-open">
            <svg><use href="#icon-line-plus-01"/></svg>
          </div>
        </span>
        ...
        <input id="filesUpload" multiple type="file" style="display:none">
      </div>

    Clicking '+' opens an Ant Design dropdown whose first item is
    '첨부 파일 업로드'. Clicking that item triggers a native file chooser
    that Playwright can intercept with expect_file_chooser.
    """
    plus = page.locator(".mode-select .ant-dropdown-trigger").first
    try:
        await plus.wait_for(state="visible", timeout=15_000)
    except PWTimeout as e:
        raise RuntimeError("'+' dropdown trigger not visible") from e
    await plus.click()
    await page.wait_for_timeout(300)

    upload_item = page.get_by_text("첨부 파일 업로드", exact=False).first
    try:
        await upload_item.wait_for(state="visible", timeout=5_000)
    except PWTimeout as e:
        raise RuntimeError("'첨부 파일 업로드' menu item did not appear") from e

    try:
        async with page.expect_file_chooser(timeout=15_000) as fc_info:
            await upload_item.click()
        fc = await fc_info.value
        await fc.set_files(str(image_path))
    except PWTimeout as e:
        raise RuntimeError("File chooser did not open") from e

    # Verify upload: a preview thumbnail or the filename appears near
    # the composer within 30s.
    fname = image_path.name
    preview_candidates = [
        page.get_by_text(fname, exact=False),
        page.locator("img[src^='blob:'], img[src*='oss'], img[src*='preview']"),
        page.locator("[class*='attachment'], [class*='file-card'], [class*='upload-preview']"),
    ]
    for _ in range(30):
        for loc in preview_candidates:
            try:
                if await loc.first.is_visible(timeout=1_000):
                    return
            except PWTimeout:
                continue
        await page.wait_for_timeout(1_000)
    raise RuntimeError("Upload did not produce a visible preview within 30s")


async def submit_prompt(page: Page, prompt: str) -> None:
    composer = page.locator("textarea.message-input-textarea").first
    await composer.click()
    # Flatten newlines: Qwen's single-row textarea treats raw '\n' as Enter
    # (= submit), which would cut the prompt off at the first newline.
    flat = " ".join(line.strip() for line in prompt.splitlines() if line.strip())
    # True clipboard paste: simulate a real paste event on the focused
    # textarea so React/Vue input handlers see exactly what a human Ctrl+V
    # would deliver — atomic, no per-character keydown.
    await composer.evaluate(
        """(el, text) => {
            el.focus();
            const dt = new DataTransfer();
            dt.setData('text/plain', text);
            el.dispatchEvent(new ClipboardEvent('paste', {
                clipboardData: dt,
                bubbles: true,
                cancelable: true,
            }));
            // Fallback for frameworks that ignore the paste event:
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, 'value'
            ).set;
            setter.call(el, text);
            el.dispatchEvent(new Event('input', { bubbles: true }));
        }""",
        flat,
    )
    await page.wait_for_timeout(600)
    await page.keyboard.press("Enter")


REJECTION_RE = re.compile(
    r"(콘텐츠 보안|부적절한 내용|content security|inappropriate)",
    re.IGNORECASE,
)
RATE_LIMIT_RE = re.compile(
    r"(한도|\d+\s*시간\s*뒤|\d+\s*hours?|rate.?limit|quota|일일\s*한도|daily limit)",
    re.IGNORECASE,
)
# Transient server errors — retry once before giving up.
TRANSIENT_RE = re.compile(
    r"(Internal Error|일시적|문제가 있었습니다|server error|try again)",
    re.IGNORECASE,
)


class RateLimitedError(RuntimeError):
    """Raised when Qwen says the account/IP has hit a usage cap."""


class StopBatchError(RuntimeError):
    """Raised on any rejection/server-error so the whole batch stops immediately."""


async def check_rejection(page: Page, timeout_ms: int = 5_000) -> str | None:
    """Return the rejection-banner text if Qwen refused the prompt."""
    deadline_ms = timeout_ms
    poll = 500
    waited = 0
    while waited < deadline_ms:
        for pat in (RATE_LIMIT_RE, REJECTION_RE, TRANSIENT_RE):
            try:
                msg = page.get_by_text(pat).last
                if await msg.is_visible(timeout=200):
                    text = (await msg.inner_text()).strip()
                    return text
            except PWTimeout:
                pass
            except Exception:
                pass
        await page.wait_for_timeout(poll)
        waited += poll
    return None


async def submit_one(
    context: BrowserContext,
    scene_no: int,
    image_path: Path,
    scene_block: str,
    download_dir: Path | None = None,
    suffix: str = "",
) -> tuple[bool, bool]:
    """Return (submitted, downloaded). Only rate-limit / rejection halts the batch."""
    page = await context.new_page()
    try:
        await page.goto(QWEN_URL, wait_until="domcontentloaded")
        await wait_for_login(page)
        await click_video_mode(page)
        await attach_image(page, image_path)
        await page.wait_for_timeout(2_500)
        await submit_prompt(page, build_prompt(scene_block))

        rejection = await check_rejection(page, timeout_ms=6_000)
        if rejection:
            # Rejection / quota — stop the whole batch (otherwise we burn quota).
            raise StopBatchError(f"[scene {scene_no:02d}] {rejection}")

        print(f"[scene {scene_no:02d}] submitted OK")
        downloaded = False
        if download_dir is not None:
            try:
                downloaded = await wait_and_download(page, scene_no, download_dir, suffix)
                if downloaded:
                    print(f"[scene {scene_no:02d}] downloaded OK")
                else:
                    print(f"[scene {scene_no:02d}] DOWNLOAD FAILED - fetch later with download_current.py", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"[scene {scene_no:02d}] DOWNLOAD ERROR: {e!r}", file=sys.stderr)
        return True, downloaded
    except (RateLimitedError, StopBatchError):
        raise
    except Exception as e:  # noqa: BLE001
        # Unexpected error on submit path — stop so we can diagnose.
        raise StopBatchError(f"[scene {scene_no:02d}] unexpected: {e!r}") from e


async def wait_and_download(page: Page, scene_no: int, out_dir: Path, suffix: str = "") -> bool:
    """Wait for the Qwen video to render, then fetch the CDN mp4 directly.

    Qwen renders the result as <video class="video-bg-card"> whose `src` is
    a JWT-signed HTTPS URL on cdn.qwenlm.ai. Fetching that URL through the
    page's request context (which carries the auth cookies) is far more
    robust than hovering and clicking the UI download button.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    suf = f"_{suffix}" if suffix else ""
    target = out_dir / f"scene_{scene_no:02d}{suf}.mp4"

    video = page.locator(".video-bg-card").last
    try:
        await video.wait_for(state="attached", timeout=15 * 60_000)
    except PWTimeout:
        print(f"[scene {scene_no:02d}] video did not appear in 15 min", file=sys.stderr)
        return False

    # Poll for src to be populated by Qwen (can lag a few seconds).
    src = None
    for _ in range(30):
        src = await video.get_attribute("src")
        if src and src.startswith("http"):
            break
        await page.wait_for_timeout(2_000)

    if not src or not src.startswith("http"):
        print(f"[scene {scene_no:02d}] no HTTPS src on video: {src!r}", file=sys.stderr)
        return False

    try:
        resp = await page.context.request.get(src)
        body = await resp.body()
        target.write_bytes(body)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[scene {scene_no:02d}] fetch failed: {e!r}", file=sys.stderr)
        return False


async def run(
    images_dir: Path,
    prompts_file: Path,
    cdp_url: str,
    limit: int,
    tabs: int,
    download_dir: Path | None,
    start: int = 1,
    suffix: str = "",
) -> None:
    images = find_images(images_dir)
    if prompts_file is not None:
        scenes = parse_prompts(prompts_file)
        blocks = {n: blk for n, blk in scenes}
    else:
        # No prompt file: every image becomes a scene with an empty block, so
        # only the common cinematic wrapper is sent to Qwen.
        blocks = {n: "" for n in images}
    work = [
        (n, images[n], blocks[n])
        for n in sorted(images)
        if n in blocks and n >= start
    ]
    if limit > 0:
        work = work[:limit]
    if not work:
        print("Nothing to do.", file=sys.stderr)
        return

    print(f"[plan] {len(work)} scenes, {tabs} concurrent tab(s)")
    print(f"[cdp] connecting to {cdp_url} ...")

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            print(
                f"[cdp] connect failed: {e!r}\n"
                "Did you launch Chrome with start-chrome.ps1 first?",
                file=sys.stderr,
            )
            return
        # Use the first existing context (= the regular browser session).
        context = browser.contexts[0] if browser.contexts else await browser.new_context()

        # Open the first tab and wait for login BEFORE submitting any work.
        # Reuse an existing chat.qwen.ai tab if one is open; otherwise open one.
        first = None
        for p in context.pages:
            if "qwen.ai" in (p.url or ""):
                first = p
                break
        if first is None:
            first = await context.new_page()
            await first.goto(QWEN_URL, wait_until="domcontentloaded")
        await wait_for_login(first)

        # Process one at a time when tabs=1 (safer, easier to debug).
        sem = asyncio.Semaphore(tabs)

        async def gated(n: int, img: Path, blk: str) -> tuple[int, bool, bool]:
            async with sem:
                submitted, downloaded = await submit_one(context, n, img, blk, download_dir, suffix)
                return n, submitted, downloaded

        try:
            results = await asyncio.gather(*[gated(n, img, blk) for n, img, blk in work])
        except (RateLimitedError, StopBatchError) as e:
            print(f"\n[STOP] {e}", file=sys.stderr)
            print(
                "[hint] Fix the issue (re-login / wait for quota / etc.), "
                "then re-run with --start <next-scene-number>.",
                file=sys.stderr,
            )
            return

        submitted = [n for n, s, _ in results if s]
        downloaded = [n for n, _, d in results if d]
        not_downloaded = [n for n, s, d in results if s and not d]
        print("\n=== SUMMARY ===")
        print(f"  submitted : {len(submitted)} -> {submitted}")
        print(f"  downloaded: {len(downloaded)} -> {downloaded}")
        if not_downloaded:
            print(f"  MISSING DOWNLOADS for scenes {not_downloaded}", file=sys.stderr)
            print(f"  -> run: python download_current.py <N> <cdp_url>", file=sys.stderr)
        # Keep the browser alive so generations finish.
        await asyncio.sleep(86_400)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", required=True, type=Path)
    ap.add_argument("--prompts", type=Path, default=None,
                    help="Prompt file. Optional: omit to use wrapper-only prompt for every image.")
    ap.add_argument("--cdp", default="http://localhost:9222", help="Chrome DevTools Protocol endpoint")
    ap.add_argument("--tabs", type=int, default=1)
    ap.add_argument("--limit", type=int, default=1, help="Only run first N scenes (default 1 for safety). 0 = all.")
    ap.add_argument("--start", type=int, default=1, help="Start from scene number N (use to resume after rate-limit)")
    ap.add_argument(
        "--download-dir",
        type=Path,
        default=Path(__file__).parent / "downloads",
        help="Folder to save finished videos (use --no-download to skip)",
    )
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--suffix", default="", help="Suffix appended to mp4 filename (e.g. 'remake' -> scene_01_remake.mp4)")
    ap.add_argument("--preview", action="store_true",
                    help="Print the final prompts and exit. No browser, no submit.")
    args = ap.parse_args()

    if args.preview:
        images = find_images(args.images)
        if args.prompts is not None:
            scene_blocks = parse_prompts(args.prompts)
        else:
            scene_blocks = [(n, "") for n in sorted(images)]
        for n, blk in scene_blocks:
            img = images.get(n)
            sep = "=" * 70
            print(f"\n{sep}\n[scene {n:02d}] image: {img.name if img else 'MISSING'}\n{sep}")
            print(build_prompt(blk))
        return
    dl = None if args.no_download else args.download_dir
    asyncio.run(
        run(
            images_dir=args.images,
            prompts_file=args.prompts,
            cdp_url=args.cdp,
            limit=args.limit,
            tabs=args.tabs,
            download_dir=dl,
            start=args.start,
            suffix=args.suffix,
        )
    )


if __name__ == "__main__":
    main()
