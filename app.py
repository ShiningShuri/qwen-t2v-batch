"""
Qwen i2v batch GUI - local FastAPI server.

Run:  python app.py
Then open http://localhost:8765 in any browser (same machine or iPad on LAN).

Lets you:
  - launch N debug Chrome instances on isolated profiles (one per Qwen account)
  - sign in to each Chrome manually
  - pick an image folder + prompt file
  - start batch automation: scenes are distributed across logged-in accounts
  - watch live logs and per-scene status
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent.resolve()
PY = sys.executable
APP_PORT = 8765
QWEN_URL = "https://chat.qwen.ai/?inputFeature=t2v"
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

app = FastAPI()


def find_chrome() -> Optional[str]:
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    return None


def cdp_alive(port: int) -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=0.3)
        s.close()
        return True
    except OSError:
        return False


class Account:
    """One Qwen account = one debug-Chrome instance + status."""

    def __init__(self, idx: int, port: int):
        self.idx = idx
        self.port = port
        self.profile_dir = ROOT / f"chrome-profile-{idx}"
        self.proc: Optional[subprocess.Popen] = None
        self.scenes_done: list[int] = []
        self.scenes_failed: list[int] = []
        self.status: str = "stopped"  # stopped | starting | logged-in | running | rate-limited | error

    def start_chrome(self) -> None:
        chrome = find_chrome()
        if not chrome:
            raise RuntimeError("Chrome not found")
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        if cdp_alive(self.port):
            self.status = "logged-in"
            return
        args = [
            chrome,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            QWEN_URL,
        ]
        self.proc = subprocess.Popen(args)
        self.status = "starting"

    def to_dict(self) -> dict:
        return {
            "idx": self.idx,
            "port": self.port,
            "status": self.status,
            "scenes_done": self.scenes_done,
            "scenes_failed": self.scenes_failed,
            "cdp_alive": cdp_alive(self.port),
        }


class State:
    def __init__(self):
        self.accounts: list[Account] = []
        self.logs: deque[str] = deque(maxlen=2000)
        self.running: bool = False
        self.workers: list[asyncio.Task] = []
        self.images_dir: str = ""
        self.prompts_file: str = ""
        self.suffix: str = ""

    def add_account(self) -> Account:
        idx = len(self.accounts) + 1
        port = 9222 + (idx - 1) * 2  # 9222, 9224, 9226, 9228, ...
        acc = Account(idx, port)
        self.accounts.append(acc)
        return acc

    def log(self, line: str) -> None:
        ts = time.strftime("%H:%M:%S")
        msg = f"[{ts}] {line}"
        self.logs.append(msg)
        print(msg)


state = State()


# ---------- HTML ----------

PAGE = """<!doctype html>
<html lang=\"ko\">
<head>
<meta charset=\"utf-8\">
<title>Qwen i2v 배치 자동화</title>
<script src=\"https://cdn.tailwindcss.com\"></script>
<style>
  body { font-family: 'Pretendard', -apple-system, sans-serif; }
  .log-line { font-family: ui-monospace, Consolas, monospace; font-size: 12px; }
</style>
</head>
<body class=\"bg-slate-900 text-slate-100 min-h-screen p-6\">
<div class=\"max-w-5xl mx-auto\">
  <h1 class=\"text-3xl font-bold mb-2\">Qwen 이미지→비디오 배치 자동화</h1>
  <p class=\"text-slate-400 mb-6\">계정 추가 → 각 Chrome에서 Qwen 로그인 → 폴더/프롬프트 선택 → 시작</p>

  <!-- 계정 카드들 -->
  <section class=\"mb-6\">
    <div class=\"flex items-center justify-between mb-3\">
      <h2 class=\"text-xl font-semibold\">1. 계정 (Chrome 인스턴스)</h2>
      <button id=\"add-acc\" class=\"bg-indigo-600 hover:bg-indigo-500 px-4 py-2 rounded font-medium\">＋ 계정 추가</button>
    </div>
    <div id=\"accounts\" class=\"grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3\"></div>
  </section>

  <!-- 입력 -->
  <section class=\"mb-6 bg-slate-800/50 p-4 rounded\">
    <h2 class=\"text-xl font-semibold mb-3\">2. 입력</h2>
    <label class=\"block mb-3\">
      <span class=\"text-sm text-slate-300\">📁 이미지 폴더 경로</span>
      <input id=\"images\" type=\"text\" placeholder=\"C:\\Users\\you\\Downloads\\scenes\"
        class=\"w-full mt-1 bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm\">
    </label>
    <label class=\"block mb-3\">
      <span class=\"text-sm text-slate-300\">📄 프롬프트 파일 경로 (.txt)</span>
      <input id=\"prompts\" type=\"text\" placeholder=\"C:\\Users\\you\\Documents\\prompts.txt\"
        class=\"w-full mt-1 bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm\">
    </label>
    <label class=\"block mb-3\">
      <span class=\"text-sm text-slate-300\">접미사 (선택, 같은 이미지 재시도 시 파일명 충돌 방지)</span>
      <input id=\"suffix\" type=\"text\" placeholder=\"예: remake, v2 (빈 칸이면 scene_NN.mp4)\"
        class=\"w-full mt-1 bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm\">
    </label>
    <label class=\"block mb-3\">
      <span class=\"text-sm text-slate-300\">시작 씬 번호 (기본 1)</span>
      <input id=\"start\" type=\"number\" value=\"1\" min=\"1\"
        class=\"w-32 mt-1 bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm\">
    </label>
  </section>

  <!-- 시작 -->
  <section class=\"mb-6 flex gap-3\">
    <button id=\"start\" class=\"bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-700 px-6 py-3 rounded text-lg font-bold\">
      ▶ 배치 시작
    </button>
    <button id=\"stop\" class=\"bg-rose-600 hover:bg-rose-500 disabled:bg-slate-700 px-6 py-3 rounded text-lg font-bold\" disabled>
      ■ 중지
    </button>
    <button id=\"open-downloads\" class=\"bg-slate-700 hover:bg-slate-600 px-4 py-3 rounded text-sm\">
      📂 다운로드 폴더 열기
    </button>
  </section>

  <!-- 로그 -->
  <section class=\"bg-black border border-slate-800 rounded p-3 h-96 overflow-y-auto\" id=\"log-pane\">
    <div id=\"logs\" class=\"log-line text-slate-300\"></div>
  </section>
</div>

<script>
const $ = s => document.querySelector(s);
const accountsEl = $(\"#accounts\");
const logsEl = $(\"#logs\");
const logPane = $(\"#log-pane\");

function statusBadge(s) {
  const map = {
    'stopped': ['중지됨', 'bg-slate-600'],
    'starting': ['시작 중', 'bg-amber-600 animate-pulse'],
    'logged-in': ['로그인 완료', 'bg-emerald-600'],
    'running': ['작업 중', 'bg-sky-600 animate-pulse'],
    'rate-limited': ['한도 도달', 'bg-rose-600'],
    'error': ['오류', 'bg-rose-700'],
  };
  const [text, cls] = map[s] || [s, 'bg-slate-600'];
  return `<span class=\"text-xs px-2 py-0.5 rounded ${cls}\">${text}</span>`;
}

async function refreshAccounts() {
  const r = await fetch('/accounts').then(r => r.json());
  accountsEl.innerHTML = r.accounts.map(a => `
    <div class=\"bg-slate-800 border border-slate-700 rounded p-3\">
      <div class=\"flex justify-between items-center mb-2\">
        <span class=\"font-bold\">계정 ${a.idx}</span>
        ${statusBadge(a.status)}
      </div>
      <div class=\"text-xs text-slate-400 mb-1\">CDP 포트: ${a.port}</div>
      <div class=\"text-xs text-slate-400 mb-2\">완료: ${a.scenes_done.length} / 실패: ${a.scenes_failed.length}</div>
      <button onclick=\"startChrome(${a.idx})\" class=\"text-xs bg-indigo-600 hover:bg-indigo-500 px-2 py-1 rounded mr-1\">Chrome 열기</button>
      <button onclick=\"checkLogin(${a.idx})\" class=\"text-xs bg-slate-600 hover:bg-slate-500 px-2 py-1 rounded\">로그인 확인</button>
    </div>
  `).join('');
}

async function addAccount() {
  await fetch('/account/add', { method: 'POST' });
  await refreshAccounts();
}

async function startChrome(idx) {
  await fetch(`/account/${idx}/start-chrome`, { method: 'POST' });
  await refreshAccounts();
}

async function checkLogin(idx) {
  await fetch(`/account/${idx}/check-login`, { method: 'POST' });
  await refreshAccounts();
}

async function startBatch() {
  const body = new URLSearchParams({
    images: $('#images').value,
    prompts: $('#prompts').value,
    suffix: $('#suffix').value,
    start: $('#start').value,
  });
  const r = await fetch('/batch/start', { method: 'POST', body });
  if (!r.ok) {
    alert((await r.json()).detail || '시작 실패');
    return;
  }
  $('#start').disabled = true;
  $('#stop').disabled = false;
}

async function stopBatch() {
  await fetch('/batch/stop', { method: 'POST' });
  $('#start').disabled = false;
  $('#stop').disabled = true;
}

$('#add-acc').onclick = addAccount;
$('#start').onclick = startBatch;
$('#stop').onclick = stopBatch;
$('#open-downloads').onclick = () => fetch('/open-downloads', {method:'POST'});

// 로그 SSE
const es = new EventSource('/logs');
es.onmessage = e => {
  const div = document.createElement('div');
  div.textContent = e.data;
  logsEl.appendChild(div);
  logPane.scrollTop = logPane.scrollHeight;
};

// 폴링
setInterval(refreshAccounts, 2000);
refreshAccounts();
</script>
</body>
</html>
"""


# ---------- routes ----------

@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return PAGE


@app.get("/accounts")
async def accounts_list():
    return {"accounts": [a.to_dict() for a in state.accounts]}


@app.post("/account/add")
async def account_add():
    acc = state.add_account()
    state.log(f"account #{acc.idx} added (port {acc.port})")
    return {"ok": True, "idx": acc.idx}


@app.post("/account/{idx}/start-chrome")
async def account_start_chrome(idx: int):
    acc = next((a for a in state.accounts if a.idx == idx), None)
    if not acc:
        raise HTTPException(404, "account not found")
    try:
        acc.start_chrome()
        state.log(f"account #{idx} Chrome started on port {acc.port}")
    except Exception as e:  # noqa: BLE001
        acc.status = "error"
        state.log(f"account #{idx} Chrome start failed: {e!r}")
        raise HTTPException(500, str(e))
    return acc.to_dict()


@app.post("/account/{idx}/check-login")
async def account_check_login(idx: int):
    acc = next((a for a in state.accounts if a.idx == idx), None)
    if not acc:
        raise HTTPException(404, "account not found")
    if not cdp_alive(acc.port):
        acc.status = "stopped"
        return acc.to_dict()
    # connect and look for the composer textarea
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(f"http://localhost:{acc.port}")
            ctx = browser.contexts[0] if browser.contexts else None
            if ctx and any("qwen.ai" in (p.url or "") for p in ctx.pages):
                acc.status = "logged-in"
            else:
                acc.status = "starting"
            await browser.close()
        except Exception as e:  # noqa: BLE001
            state.log(f"account #{idx} check-login error: {e!r}")
            acc.status = "error"
    return acc.to_dict()


@app.post("/batch/start")
async def batch_start(
    images: str = Form(...),
    prompts: str = Form(...),
    suffix: str = Form(""),
    start: int = Form(1),
):
    if state.running:
        raise HTTPException(409, "already running")
    if not Path(images).is_dir():
        raise HTTPException(400, f"image folder not found: {images}")
    if not Path(prompts).is_file():
        raise HTTPException(400, f"prompts file not found: {prompts}")
    logged = [a for a in state.accounts if a.status == "logged-in"]
    if not logged:
        raise HTTPException(400, "no logged-in account. add account, start chrome, log in, then 'check login'.")

    state.images_dir = images
    state.prompts_file = prompts
    state.suffix = suffix
    state.running = True

    # parse scene numbers (use the same parser as main.py)
    sys.path.insert(0, str(ROOT))
    from main import parse_prompts, find_images  # type: ignore

    scenes = parse_prompts(Path(prompts))
    imgs = find_images(Path(images))
    work = [(n, blk) for n, blk in scenes if n in imgs and n >= start]
    if not work:
        state.running = False
        raise HTTPException(400, "no scenes to run (check start + image filenames)")

    # distribute round-robin across logged-in accounts
    buckets: dict[int, list[int]] = {a.idx: [] for a in logged}
    for i, (n, _blk) in enumerate(work):
        acc = logged[i % len(logged)]
        buckets[acc.idx].append(n)

    state.log(f"batch start: {len(work)} scenes across {len(logged)} account(s)")
    for acc in logged:
        scenes_for = buckets[acc.idx]
        state.log(f"  account #{acc.idx} -> scenes {scenes_for}")
        task = asyncio.create_task(run_worker(acc, scenes_for, images, prompts, suffix))
        state.workers.append(task)

    return {"ok": True, "workers": len(state.workers)}


async def run_worker(acc: Account, scenes: list[int], images: str, prompts: str, suffix: str) -> None:
    """Invoke main.py once per scene for this account so a rate-limit on one
    account does not abort the whole batch."""
    acc.status = "running"
    for n in scenes:
        if not state.running:
            break
        cmd = [
            PY,
            str(ROOT / "main.py"),
            "--images", images,
            "--prompts", prompts,
            "--cdp", f"http://localhost:{acc.port}",
            "--start", str(n),
            "--limit", "1",
            "--tabs", "1",
        ]
        if suffix:
            cmd += ["--suffix", suffix]
        state.log(f"[acc#{acc.idx}] scene {n:02d} starting")
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=str(ROOT)
        )
        assert proc.stdout
        rate_limited = False
        downloaded = False
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            state.log(f"[acc#{acc.idx}] {line}")
            if "STOP" in line and ("한도" in line or "quota" in line or "시간" in line.lower()):
                rate_limited = True
            if "downloaded OK" in line:
                downloaded = True
        await proc.wait()
        if rate_limited:
            acc.status = "rate-limited"
            state.log(f"[acc#{acc.idx}] rate-limited at scene {n:02d}, stopping this account.")
            break
        if downloaded:
            acc.scenes_done.append(n)
        else:
            acc.scenes_failed.append(n)
    if acc.status == "running":
        acc.status = "logged-in"


@app.post("/batch/stop")
async def batch_stop():
    state.running = False
    for t in state.workers:
        t.cancel()
    state.workers.clear()
    state.log("batch stopped by user")
    return {"ok": True}


@app.get("/logs")
async def logs_stream():
    async def gen():
        last = 0
        while True:
            cur = len(state.logs)
            if cur > last:
                for line in list(state.logs)[last:cur]:
                    yield f"data: {line}\n\n"
                last = cur
            await asyncio.sleep(0.5)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/open-downloads")
async def open_downloads():
    d = ROOT / "downloads"
    d.mkdir(exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(d))
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    state.log(f"server starting on http://localhost:{APP_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=APP_PORT, log_level="warning")
