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
        # scene_no -> {"state": pending|running|done|failed, "acc": idx|None}
        self.scenes: dict[int, dict] = {}

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
<html lang="ko" x-data="app()" x-init="init()">
<head>
<meta charset="utf-8">
<title>Qwen i2v Studio</title>
<script src="https://cdn.tailwindcss.com"></script>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<style>
  body { font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, sans-serif; }
  .mono { font-family: ui-monospace, 'JetBrains Mono', Consolas, monospace; }
  /* Animated progress stripes */
  @keyframes stripes { from { background-position: 0 0 } to { background-position: 40px 0 } }
  .stripes {
    background-image: linear-gradient(45deg, rgba(255,255,255,.15) 25%, transparent 25%,
      transparent 50%, rgba(255,255,255,.15) 50%, rgba(255,255,255,.15) 75%, transparent 75%, transparent);
    background-size: 40px 40px;
    animation: stripes 1.2s linear infinite;
  }
  .scrollbar::-webkit-scrollbar { width: 8px; }
  .scrollbar::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
</style>
</head>
<body class="bg-gradient-to-b from-slate-950 to-slate-900 text-slate-100 min-h-screen">

<!-- top bar -->
<header class="border-b border-slate-800 bg-slate-950/80 backdrop-blur sticky top-0 z-10">
  <div class="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between">
    <div class="flex items-center gap-3">
      <div class="w-9 h-9 rounded-lg bg-gradient-to-br from-indigo-500 to-fuchsia-500 flex items-center justify-center font-bold">Q</div>
      <div>
        <h1 class="text-lg font-bold leading-tight">Qwen i2v Studio</h1>
        <p class="text-xs text-slate-400 leading-tight">이미지 → 비디오 배치 자동화</p>
      </div>
    </div>
    <div class="flex items-center gap-2 text-xs">
      <span class="text-slate-400">완료</span>
      <span class="font-bold text-emerald-400" x-text="totalDone"></span>
      <span class="text-slate-600">/</span>
      <span class="font-bold" x-text="totalPlanned || '-'"></span>
      <template x-if="anyRunning">
        <span class="ml-3 inline-flex items-center gap-1 text-sky-400">
          <span class="w-2 h-2 rounded-full bg-sky-400 animate-pulse"></span> 실행 중
        </span>
      </template>
    </div>
  </div>
</header>

<main class="max-w-7xl mx-auto px-6 py-6 grid grid-cols-1 lg:grid-cols-3 gap-6">

  <!-- left column: control -->
  <div class="lg:col-span-1 space-y-4">

    <!-- step 1: accounts -->
    <section class="bg-slate-900/70 border border-slate-800 rounded-xl p-4">
      <div class="flex items-center justify-between mb-3">
        <h2 class="font-semibold flex items-center gap-2">
          <span class="w-6 h-6 rounded-full bg-indigo-600 text-xs flex items-center justify-center font-bold">1</span>
          Qwen 계정
        </h2>
        <button @click="addAccount()" class="text-sm bg-indigo-600 hover:bg-indigo-500 px-3 py-1.5 rounded font-medium">
          + 계정 추가
        </button>
      </div>
      <p class="text-xs text-slate-400 mb-3 leading-relaxed">
        하나의 계정 = 하나의 Chrome 창. 각 Chrome에서 다른 Qwen 계정으로 로그인하면 한도가 N배.
      </p>
      <div class="space-y-2">
        <template x-for="a in accounts" :key="a.idx">
          <div class="bg-slate-800/60 border border-slate-700/50 rounded-lg p-3">
            <div class="flex items-center justify-between mb-2">
              <div class="flex items-center gap-2">
                <span class="font-bold text-sm">계정 #<span x-text="a.idx"></span></span>
                <span class="text-[10px] text-slate-500 mono">:<span x-text="a.port"></span></span>
              </div>
              <span class="text-[11px] px-2 py-0.5 rounded font-medium"
                    :class="statusClass(a.status)" x-text="statusText(a.status)"></span>
            </div>
            <div class="flex items-center gap-3 text-[11px] text-slate-400 mb-2">
              <span>완료 <span class="text-emerald-400 font-medium" x-text="a.scenes_done.length"></span></span>
              <span>실패 <span class="text-rose-400 font-medium" x-text="a.scenes_failed.length"></span></span>
            </div>
            <div class="flex gap-1.5">
              <button @click="startChrome(a.idx)"
                class="flex-1 text-xs bg-slate-700 hover:bg-slate-600 px-2 py-1 rounded">
                Chrome 열기
              </button>
              <button @click="checkLogin(a.idx)"
                class="flex-1 text-xs bg-slate-700 hover:bg-slate-600 px-2 py-1 rounded">
                로그인 확인
              </button>
            </div>
          </div>
        </template>
        <template x-if="accounts.length === 0">
          <div class="text-center text-slate-500 text-sm py-6 border border-dashed border-slate-800 rounded-lg">
            계정 추가를 눌러 시작
          </div>
        </template>
      </div>
    </section>

    <!-- step 2: inputs -->
    <section class="bg-slate-900/70 border border-slate-800 rounded-xl p-4">
      <h2 class="font-semibold flex items-center gap-2 mb-3">
        <span class="w-6 h-6 rounded-full bg-indigo-600 text-xs flex items-center justify-center font-bold">2</span>
        입력
      </h2>
      <div class="space-y-3 text-sm">
        <label class="block">
          <span class="block text-xs text-slate-400 mb-1">이미지 폴더</span>
          <input x-model="images" type="text" placeholder="C:\\Users\\you\\images"
            class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 mono text-xs focus:border-indigo-500 focus:outline-none">
        </label>
        <label class="block">
          <span class="block text-xs text-slate-400 mb-1">프롬프트 파일 (.txt)</span>
          <input x-model="prompts" type="text" placeholder="C:\\Users\\you\\prompts.txt"
            class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 mono text-xs focus:border-indigo-500 focus:outline-none">
        </label>
        <div class="grid grid-cols-2 gap-3">
          <label class="block">
            <span class="block text-xs text-slate-400 mb-1">시작 씬</span>
            <input x-model.number="start" type="number" min="1"
              class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs focus:border-indigo-500 focus:outline-none">
          </label>
          <label class="block">
            <span class="block text-xs text-slate-400 mb-1">접미사 (선택)</span>
            <input x-model="suffix" type="text" placeholder="remake"
              class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs focus:border-indigo-500 focus:outline-none">
          </label>
        </div>
      </div>
    </section>

    <!-- step 3: run -->
    <section class="bg-slate-900/70 border border-slate-800 rounded-xl p-4">
      <h2 class="font-semibold flex items-center gap-2 mb-3">
        <span class="w-6 h-6 rounded-full bg-indigo-600 text-xs flex items-center justify-center font-bold">3</span>
        실행
      </h2>
      <div class="space-y-2">
        <button @click="startBatch()" :disabled="anyRunning"
          class="w-full bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 disabled:from-slate-700 disabled:to-slate-700 disabled:cursor-not-allowed py-2.5 rounded font-bold text-sm">
          ▶ 배치 시작
        </button>
        <button @click="stopBatch()" :disabled="!anyRunning"
          class="w-full bg-rose-600 hover:bg-rose-500 disabled:bg-slate-700 disabled:cursor-not-allowed py-2 rounded font-medium text-sm">
          ■ 중지
        </button>
        <button @click="openDownloads()"
          class="w-full bg-slate-800 hover:bg-slate-700 py-2 rounded text-sm">
          📂 다운로드 폴더 열기
        </button>
      </div>
    </section>
  </div>

  <!-- right column: scenes + log -->
  <div class="lg:col-span-2 space-y-4">

    <!-- progress -->
    <section class="bg-slate-900/70 border border-slate-800 rounded-xl p-4">
      <div class="flex items-center justify-between mb-2">
        <h2 class="font-semibold">씬 진행 상태</h2>
        <div class="text-xs text-slate-400">
          <span x-text="totalDone"></span> / <span x-text="totalPlanned || scenes.length"></span> 완료
        </div>
      </div>
      <div class="w-full h-2 bg-slate-800 rounded-full overflow-hidden mb-3">
        <div class="h-full bg-gradient-to-r from-emerald-500 to-teal-500 transition-all"
             :class="anyRunning ? 'stripes' : ''"
             :style="{width: progressPct + '%'}"></div>
      </div>
      <div class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
        <template x-for="s in scenes" :key="s.no">
          <div class="aspect-square rounded-md border flex flex-col items-center justify-center text-center p-1 transition-all"
               :class="sceneCardClass(s)">
            <div class="text-xs font-bold mono" x-text="s.no.toString().padStart(2,'0')"></div>
            <div class="text-[9px] mt-0.5 opacity-80" x-text="sceneStatus(s)"></div>
          </div>
        </template>
        <template x-if="scenes.length === 0">
          <div class="col-span-full text-center text-slate-500 text-sm py-6">
            프롬프트 파일 + 이미지 폴더 경로를 입력하면 씬 카드가 표시됨
          </div>
        </template>
      </div>
    </section>

    <!-- live log -->
    <section class="bg-slate-950 border border-slate-800 rounded-xl overflow-hidden">
      <div class="flex items-center justify-between px-4 py-2 border-b border-slate-800 bg-slate-900/50">
        <span class="text-sm font-semibold">실시간 로그</span>
        <button @click="logs = []" class="text-[11px] text-slate-400 hover:text-slate-200">지우기</button>
      </div>
      <div class="h-[28rem] overflow-y-auto p-3 scrollbar" id="log-pane">
        <template x-for="l in logs" :key="l.i">
          <div class="mono text-[11px] leading-relaxed"
               :class="logColor(l.text)" x-text="l.text"></div>
        </template>
        <template x-if="logs.length === 0">
          <div class="text-slate-600 text-xs italic">로그가 여기에 표시됩니다.</div>
        </template>
      </div>
    </section>
  </div>
</main>

<script>
function app() {
  return {
    accounts: [],
    scenes: [],
    logs: [],
    images: '',
    prompts: '',
    suffix: '',
    start: 1,
    logCounter: 0,
    get anyRunning() {
      return this.accounts.some(a => a.status === 'running' || a.status === 'starting');
    },
    get totalDone() {
      return this.accounts.reduce((s,a) => s + a.scenes_done.length, 0);
    },
    get totalPlanned() {
      return this.scenes.length;
    },
    get progressPct() {
      const t = this.totalPlanned;
      return t ? Math.round(this.totalDone / t * 100) : 0;
    },
    statusText(s) {
      return ({
        'stopped': '중지됨', 'starting': '시작 중', 'logged-in': '로그인 완료',
        'running': '작업 중', 'rate-limited': '한도 도달', 'error': '오류'
      })[s] || s;
    },
    statusClass(s) {
      return ({
        'stopped': 'bg-slate-700 text-slate-300',
        'starting': 'bg-amber-600/30 text-amber-300 border border-amber-500/30',
        'logged-in': 'bg-emerald-600/30 text-emerald-300 border border-emerald-500/30',
        'running': 'bg-sky-600/30 text-sky-300 border border-sky-500/30 animate-pulse',
        'rate-limited': 'bg-rose-600/30 text-rose-300 border border-rose-500/30',
        'error': 'bg-rose-800/40 text-rose-300 border border-rose-600/40',
      })[s] || 'bg-slate-700';
    },
    sceneCardClass(s) {
      if (s.state === 'done') return 'bg-emerald-600/15 border-emerald-500/40 text-emerald-200';
      if (s.state === 'failed') return 'bg-rose-600/15 border-rose-500/40 text-rose-200';
      if (s.state === 'running') return 'bg-sky-600/15 border-sky-500/40 text-sky-200 animate-pulse';
      return 'bg-slate-800/30 border-slate-700/40 text-slate-400';
    },
    sceneStatus(s) {
      return ({ done:'완료', failed:'실패', running:'진행', pending:'대기' })[s.state] || s.state;
    },
    logColor(t) {
      if (t.includes('downloaded OK')) return 'text-emerald-400';
      if (t.includes('submitted OK')) return 'text-sky-400';
      if (t.includes('FAIL') || t.includes('error') || t.includes('STOP') || t.includes('rate-limit')) return 'text-rose-400';
      if (t.includes('starting')) return 'text-amber-400';
      return 'text-slate-300';
    },
    async refresh() {
      const r = await fetch('/state').then(r => r.json());
      this.accounts = r.accounts;
      this.scenes = r.scenes;
    },
    async addAccount() {
      await fetch('/account/add', { method: 'POST' });
      await this.refresh();
    },
    async startChrome(idx) {
      await fetch(`/account/${idx}/start-chrome`, { method: 'POST' });
      await this.refresh();
    },
    async checkLogin(idx) {
      await fetch(`/account/${idx}/check-login`, { method: 'POST' });
      await this.refresh();
    },
    async startBatch() {
      const body = new URLSearchParams({
        images: this.images, prompts: this.prompts,
        suffix: this.suffix, start: this.start,
      });
      const r = await fetch('/batch/start', { method: 'POST', body });
      if (!r.ok) {
        const d = await r.json();
        alert(d.detail || '시작 실패');
      }
    },
    async stopBatch() {
      await fetch('/batch/stop', { method: 'POST' });
    },
    openDownloads() { fetch('/open-downloads', { method: 'POST' }); },
    init() {
      const es = new EventSource('/logs');
      es.onmessage = e => {
        this.logs.push({ i: this.logCounter++, text: e.data });
        if (this.logs.length > 800) this.logs.shift();
        this.$nextTick(() => {
          const p = document.getElementById('log-pane');
          if (p) p.scrollTop = p.scrollHeight;
        });
      };
      this.refresh();
      setInterval(() => this.refresh(), 2000);
    },
  };
}
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


@app.get("/state")
async def full_state():
    scenes = [
        {"no": n, "state": s["state"], "acc": s.get("acc")}
        for n, s in sorted(state.scenes.items())
    ]
    return {
        "accounts": [a.to_dict() for a in state.accounts],
        "scenes": scenes,
        "running": state.running,
    }


@app.post("/scan")
async def scan_inputs(images: str = Form(...), prompts: str = Form(...)):
    """Build the scene plan from the inputs without starting anything."""
    if not Path(images).is_dir() or not Path(prompts).is_file():
        return {"scenes": []}
    sys.path.insert(0, str(ROOT))
    from main import parse_prompts, find_images  # type: ignore
    scenes = parse_prompts(Path(prompts))
    imgs = find_images(Path(images))
    state.scenes = {
        n: {"state": "pending", "acc": None}
        for n, _ in scenes if n in imgs
    }
    return {"scenes": [{"no": n, "state": "pending"} for n in sorted(state.scenes)]}


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

    # build scene plan + distribute round-robin across logged-in accounts
    state.scenes = {n: {"state": "pending", "acc": None} for n, _ in work}
    buckets: dict[int, list[int]] = {a.idx: [] for a in logged}
    for i, (n, _blk) in enumerate(work):
        acc = logged[i % len(logged)]
        buckets[acc.idx].append(n)
        state.scenes[n]["acc"] = acc.idx

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
        if n in state.scenes:
            state.scenes[n]["state"] = "running"
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
            if "STOP" in line and ("한도" in line or "quota" in line or "시간" in line):
                rate_limited = True
            if "downloaded OK" in line:
                downloaded = True
        await proc.wait()
        if rate_limited:
            acc.status = "rate-limited"
            if n in state.scenes:
                state.scenes[n]["state"] = "failed"
            state.log(f"[acc#{acc.idx}] rate-limited at scene {n:02d}, stopping this account.")
            break
        if downloaded:
            acc.scenes_done.append(n)
            if n in state.scenes:
                state.scenes[n]["state"] = "done"
        else:
            acc.scenes_failed.append(n)
            if n in state.scenes:
                state.scenes[n]["state"] = "failed"
    if acc.status == "running":
        acc.status = "logged-in"
    # all workers done?
    if not any(a.status == "running" for a in state.accounts):
        state.running = False


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
