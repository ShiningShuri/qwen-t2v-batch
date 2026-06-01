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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
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
        # idx 1 reuses the original 'chrome-profile' folder; >=2 get a suffix.
        self.profile_dir = ROOT / ("chrome-profile" if idx == 1 else f"chrome-profile-{idx}")
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
        self._image_root = None  # resolved Path of the last-scanned image folder
        # scene_no -> {"state": pending|running|done|failed, "acc": idx|None}
        self.scenes: dict[int, dict] = {}

    def add_account(self) -> Account:
        # Reuse the lowest free idx so re-adding after a removal stays compact.
        used = {a.idx for a in self.accounts}
        idx = 1
        while idx in used:
            idx += 1
        port = 9222 + (idx - 1) * 2  # 9222, 9224, 9226, 9228, ...
        acc = Account(idx, port)
        self.accounts.append(acc)
        self.accounts.sort(key=lambda a: a.idx)
        return acc

    def add_accounts(self, n: int) -> list[Account]:
        return [self.add_account() for _ in range(n)]

    def restore_accounts(self) -> list[Account]:
        """Recreate Account objects from existing chrome-profile-* folders so a
        server restart keeps every logged-in account instead of starting empty.

        Profile dirs are named 'chrome-profile' (idx 1) and 'chrome-profile-N'
        (idx N>=2) — matches Account.profile_dir below."""
        found: list[int] = []
        for p in ROOT.glob("chrome-profile*"):
            if not p.is_dir():
                continue
            name = p.name
            if name == "chrome-profile":
                found.append(1)
            else:
                m = re.fullmatch(r"chrome-profile-(\d+)", name)
                if m:
                    found.append(int(m.group(1)))
        existing = {a.idx for a in self.accounts}
        restored: list[Account] = []
        for idx in sorted(set(found)):
            if idx in existing:
                continue
            port = 9222 + (idx - 1) * 2
            acc = Account(idx, port)
            # If its debug Chrome is already running, mark it live.
            if cdp_alive(port):
                acc.status = "logged-in"
            self.accounts.append(acc)
            restored.append(acc)
        self.accounts.sort(key=lambda a: a.idx)
        return restored

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
        <span class="text-[11px] text-slate-400">로그인 완료
          <span class="text-emerald-400 font-bold" x-text="loggedInCount"></span> / <span x-text="accounts.length"></span></span>
      </div>
      <p class="text-xs text-slate-400 mb-3 leading-relaxed">
        한 계정 = Chrome 창 1개(프로필 분리). 각 창에서 다른 Qwen 계정 로그인 → 한도 N배.
        한 번에 여러 개 만들고 창마다 로그인만 하면 됨.
      </p>

      <!-- bulk add -->
      <div class="flex items-end gap-2 mb-2">
        <label class="flex-1">
          <span class="block text-[11px] text-slate-400 mb-1">계정 개수</span>
          <input x-model.number="addCount" type="number" min="1" max="20"
            class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none">
        </label>
        <button @click="addAccountsBulk()"
          class="bg-indigo-600 hover:bg-indigo-500 px-3 py-2 rounded font-medium text-sm whitespace-nowrap">
          + N개 추가 & Chrome 열기
        </button>
      </div>
      <div class="flex gap-2 mb-3">
        <button @click="openAll()" class="flex-1 text-xs bg-slate-800 hover:bg-slate-700 px-2 py-1.5 rounded">
          🖥 닫힌 창 다시 열기
        </button>
        <button @click="checkAll()" class="flex-1 text-xs bg-emerald-700 hover:bg-emerald-600 px-2 py-1.5 rounded font-medium">
          ✓ 전체 로그인 확인
        </button>
      </div>

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
              <button @click="removeAccount(a.idx)"
                class="text-xs bg-slate-700 hover:bg-rose-700 px-2 py-1 rounded" title="목록에서 제거">✕</button>
            </div>
          </div>
        </template>
        <template x-if="accounts.length === 0">
          <div class="text-center text-slate-500 text-sm py-6 border border-dashed border-slate-800 rounded-lg">
            위에서 계정 개수를 정하고 추가하세요
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
          <div class="flex gap-2">
            <input x-model="images" type="text" placeholder="C:\\Users\\you\\images"
              class="flex-1 bg-slate-950 border border-slate-700 rounded px-3 py-2 mono text-xs focus:border-indigo-500 focus:outline-none">
            <button @click="scanImages()"
              class="bg-indigo-600 hover:bg-indigo-500 px-3 py-2 rounded text-xs font-medium whitespace-nowrap">
              📂 불러오기
            </button>
          </div>
        </label>
        <label class="block">
          <span class="block text-xs text-slate-400 mb-1">프롬프트 파일 (.txt) — <span class="text-slate-500">비워두면 공용 영상화 프롬프트만 사용</span></span>
          <input x-model="prompts" type="text" placeholder="(선택) C:\\Users\\you\\prompts.txt"
            class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 mono text-xs focus:border-indigo-500 focus:outline-none">
        </label>
        <label class="block">
          <span class="block text-xs text-slate-400 mb-1">접미사 (선택) — 파일명 충돌 방지</span>
          <input x-model="suffix" type="text" placeholder="remake"
            class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-xs focus:border-indigo-500 focus:outline-none">
        </label>
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

    <!-- progress + image grid -->
    <section class="bg-slate-900/70 border border-slate-800 rounded-xl p-4">
      <div class="flex items-center justify-between mb-2">
        <h2 class="font-semibold">이미지 선택 → 영상 생성</h2>
        <div class="text-xs text-slate-400">
          선택 <span class="text-indigo-300 font-bold" x-text="selectedCount"></span> ·
          완료 <span x-text="totalDone"></span> / <span x-text="totalPlanned || scenes.length"></span>
        </div>
      </div>

      <div class="flex items-center gap-2 mb-3" x-show="scenes.length">
        <button @click="selectAll()" class="text-[11px] bg-slate-800 hover:bg-slate-700 px-2.5 py-1 rounded">전체 선택</button>
        <button @click="selectNone()" class="text-[11px] bg-slate-800 hover:bg-slate-700 px-2.5 py-1 rounded">선택 해제</button>
        <span class="text-[11px] text-slate-500 ml-auto">컷을 클릭해 만들 영상만 고르세요 (아무것도 안 고르면 전체 생성)</span>
      </div>

      <div class="w-full h-2 bg-slate-800 rounded-full overflow-hidden mb-3" x-show="scenes.length">
        <div class="h-full bg-gradient-to-r from-emerald-500 to-teal-500 transition-all"
             :class="anyRunning ? 'stripes' : ''"
             :style="{width: progressPct + '%'}"></div>
      </div>

      <div class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 gap-2">
        <template x-for="s in scenes" :key="s.no">
          <button @click="toggleScene(s.no)"
               class="relative aspect-square rounded-lg border-2 overflow-hidden group transition-all text-left"
               :class="sceneCardClass(s)">
            <template x-if="s.thumb">
              <img :src="s.thumb" loading="lazy" class="absolute inset-0 w-full h-full object-cover"
                   :class="selected.includes(s.no) ? 'opacity-100' : 'opacity-50 group-hover:opacity-80'">
            </template>
            <!-- selection check -->
            <div class="absolute top-1 left-1 w-5 h-5 rounded-full flex items-center justify-center text-[11px] font-bold border"
                 :class="selected.includes(s.no) ? 'bg-indigo-500 border-indigo-300 text-white' : 'bg-black/50 border-slate-400 text-transparent'">✓</div>
            <!-- scene no + state badge -->
            <div class="absolute bottom-0 inset-x-0 bg-black/60 backdrop-blur-sm px-1.5 py-0.5 flex items-center justify-between">
              <span class="text-[10px] font-bold mono text-white" x-text="s.no.toString().padStart(2,'0')"></span>
              <span class="text-[9px] font-medium" :class="stateBadgeColor(s)" x-text="sceneStatus(s)"></span>
            </div>
          </button>
        </template>
        <template x-if="scenes.length === 0">
          <div class="col-span-full text-center text-slate-500 text-sm py-10">
            이미지 폴더 경로 입력 후 <b>📂 불러오기</b>를 누르면 컷 썸네일이 표시됩니다.<br>
            <span class="text-xs text-slate-600">파일명은 scene_01.jpg, scene_02.png … 형식</span>
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
    selected: [],      // scene numbers the user picked in the grid
    logs: [],
    images: '',
    prompts: '',
    suffix: '',
    start: 1,
    addCount: 3,
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
    get loggedInCount() {
      return this.accounts.filter(a => a.status === 'logged-in' || a.status === 'running').length;
    },
    get selectedCount() { return this.selected.length; },
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
      const sel = this.selected.includes(s.no);
      if (s.state === 'done')    return 'border-emerald-400 ring-2 ring-emerald-500/40';
      if (s.state === 'failed')  return 'border-rose-400 ring-2 ring-rose-500/40';
      if (s.state === 'running') return 'border-sky-400 ring-2 ring-sky-400/50 animate-pulse';
      return sel ? 'border-indigo-400 ring-2 ring-indigo-500/40'
                 : 'border-slate-700/60 hover:border-slate-500';
    },
    stateBadgeColor(s) {
      return ({ done:'text-emerald-300', failed:'text-rose-300',
                running:'text-sky-300', pending:'text-slate-400' })[s.state] || 'text-slate-400';
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
      // Merge state updates into existing scene cards WITHOUT losing thumbnails.
      const byNo = {};
      this.scenes.forEach(s => byNo[s.no] = s);
      r.scenes.forEach(rs => {
        if (byNo[rs.no]) byNo[rs.no].state = rs.state;   // keep thumb/file
        else this.scenes.push(rs);
      });
    },
    async scanImages() {
      if (!this.images) { alert('이미지 폴더 경로를 입력하세요'); return; }
      const body = new URLSearchParams({ images: this.images, prompts: this.prompts });
      const r = await fetch('/scan', { method: 'POST', body }).then(r => r.json());
      if (r.error) { alert(r.error); return; }
      this.scenes = r.scenes;
      this.selected = [];   // start empty = "generate all" by default
    },
    toggleScene(no) {
      const i = this.selected.indexOf(no);
      if (i >= 0) this.selected.splice(i, 1);
      else this.selected.push(no);
    },
    selectAll() { this.selected = this.scenes.map(s => s.no); },
    selectNone() { this.selected = []; },
    async addAccountsBulk() {
      const n = Math.max(1, Math.min(this.addCount || 1, 20));
      const body = new URLSearchParams({ count: n, open_chrome: 'true' });
      await fetch('/account/add-bulk', { method: 'POST', body });
      await this.refresh();
    },
    async openAll() {
      await fetch('/account/open-all', { method: 'POST' });
      await this.refresh();
    },
    async checkAll() {
      await fetch('/account/check-all', { method: 'POST' });
      await this.refresh();
    },
    async removeAccount(idx) {
      await fetch(`/account/${idx}/remove`, { method: 'POST' });
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
      if (this.loggedInCount === 0) { alert('로그인된 계정이 없습니다. 계정 추가 → 각 Chrome 로그인 → 전체 로그인 확인'); return; }
      const body = new URLSearchParams({
        images: this.images, prompts: this.prompts,
        suffix: this.suffix, start: this.start,
        scenes: this.selected.join(','),   // empty = run all
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
      // Restore accounts from existing chrome-profile-* folders on load.
      fetch('/account/restore', { method: 'POST' }).then(() => this.refresh());
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
async def scan_inputs(images: str = Form(...), prompts: str = Form("")):
    """Scan an image folder into scene cards. `prompts` is optional now:
    with no prompts file, every image still becomes a scene using the common
    wrapper-only prompt. Returns thumbnails so the UI shows a clickable grid."""
    img_dir = Path(images)
    if not img_dir.is_dir():
        return {"scenes": [], "error": "image folder not found"}

    sys.path.insert(0, str(ROOT))
    from main import parse_prompts, find_images  # type: ignore

    imgs = find_images(img_dir)  # {scene_no: Path}
    have_prompts = bool(prompts) and Path(prompts).is_file()
    prompt_nos = {n for n, _ in parse_prompts(Path(prompts))} if have_prompts else set()

    state.images_dir = images
    state.prompts_file = prompts if have_prompts else ""
    # Remember the folder we're allowed to serve thumbnails from (path-safety).
    state._image_root = img_dir.resolve()  # type: ignore[attr-defined]

    state.scenes = {n: {"state": "pending", "acc": None} for n in sorted(imgs)}
    scenes = []
    for n in sorted(imgs):
        scenes.append({
            "no": n,
            "state": "pending",
            "file": imgs[n].name,
            "thumb": f"/image?file={imgs[n].name}",
            "has_prompt": (n in prompt_nos) if have_prompts else False,
        })
    return {"scenes": scenes, "have_prompts": have_prompts}


@app.get("/image")
async def serve_image(file: str):
    """Serve one image from the last-scanned folder. Only basenames inside that
    folder are allowed — prevents path traversal to arbitrary disk files."""
    root = getattr(state, "_image_root", None)
    if root is None:
        raise HTTPException(404, "no image folder scanned yet")
    # Reject any path component / traversal — must be a plain filename.
    if "/" in file or "\\" in file or ".." in file:
        raise HTTPException(400, "invalid filename")
    target = (Path(root) / file).resolve()
    if Path(root) not in target.parents or not target.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(str(target))


@app.post("/account/add")
async def account_add():
    acc = state.add_account()
    state.log(f"account #{acc.idx} added (port {acc.port})")
    return {"ok": True, "idx": acc.idx}


@app.post("/account/add-bulk")
async def account_add_bulk(count: int = Form(...), open_chrome: bool = Form(True)):
    """Add N accounts at once and (optionally) launch every Chrome window so the
    user only logs in — no per-account 'add → open → check' clicking."""
    count = max(1, min(count, 20))  # sanity cap
    accs = state.add_accounts(count)
    state.log(f"added {count} account(s): {[a.idx for a in accs]}")
    if open_chrome:
        for acc in accs:
            try:
                acc.start_chrome()
                state.log(f"account #{acc.idx} Chrome opening on port {acc.port}")
            except Exception as e:  # noqa: BLE001
                acc.status = "error"
                state.log(f"account #{acc.idx} Chrome start failed: {e!r}")
    return {"ok": True, "idxs": [a.idx for a in accs]}


@app.post("/account/restore")
async def account_restore(open_chrome: bool = Form(False)):
    """Rebuild accounts from existing chrome-profile-* folders (survive restart)."""
    restored = state.restore_accounts()
    state.log(f"restored {len(restored)} account(s) from disk: {[a.idx for a in restored]}")
    if open_chrome:
        for acc in restored:
            if not cdp_alive(acc.port):
                try:
                    acc.start_chrome()
                except Exception as e:  # noqa: BLE001
                    acc.status = "error"
                    state.log(f"account #{acc.idx} Chrome start failed: {e!r}")
    return {"ok": True, "idxs": [a.idx for a in restored]}


@app.post("/account/open-all")
async def account_open_all():
    """Launch Chrome for every account whose debug port is not already alive."""
    opened = []
    for acc in state.accounts:
        if cdp_alive(acc.port):
            acc.status = "logged-in"
            continue
        try:
            acc.start_chrome()
            opened.append(acc.idx)
        except Exception as e:  # noqa: BLE001
            acc.status = "error"
            state.log(f"account #{acc.idx} Chrome start failed: {e!r}")
    state.log(f"open-all: launched Chrome for accounts {opened}")
    return {"ok": True, "opened": opened}


@app.post("/account/check-all")
async def account_check_all():
    """Check login status of every account in one pass (no per-card clicking)."""
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        for acc in state.accounts:
            if not cdp_alive(acc.port):
                acc.status = "stopped"
                continue
            try:
                browser = await pw.chromium.connect_over_cdp(f"http://localhost:{acc.port}")
                ctx = browser.contexts[0] if browser.contexts else None
                if ctx and any("qwen.ai" in (p.url or "") for p in ctx.pages):
                    acc.status = "logged-in"
                else:
                    acc.status = "starting"
                await browser.close()
            except Exception as e:  # noqa: BLE001
                state.log(f"account #{acc.idx} check error: {e!r}")
                acc.status = "error"
    logged = [a.idx for a in state.accounts if a.status == "logged-in"]
    state.log(f"check-all: logged-in accounts {logged}")
    return {"ok": True, "logged_in": logged}


@app.post("/account/{idx}/remove")
async def account_remove(idx: int):
    acc = next((a for a in state.accounts if a.idx == idx), None)
    if not acc:
        raise HTTPException(404, "account not found")
    # Don't kill its Chrome — user may still be using it; just drop from rotation.
    state.accounts = [a for a in state.accounts if a.idx != idx]
    state.log(f"account #{idx} removed from rotation")
    return {"ok": True}


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
    prompts: str = Form(""),
    suffix: str = Form(""),
    start: int = Form(1),
    scenes: str = Form(""),  # optional CSV of scene numbers to run ONLY these
):
    if state.running:
        raise HTTPException(409, "already running")
    if not Path(images).is_dir():
        raise HTTPException(400, f"image folder not found: {images}")
    have_prompts = bool(prompts) and Path(prompts).is_file()
    logged = [a for a in state.accounts if a.status == "logged-in"]
    if not logged:
        raise HTTPException(400, "no logged-in account. add accounts, open Chrome, log in, then 'check all'.")

    state.images_dir = images
    state.prompts_file = prompts if have_prompts else ""
    state.suffix = suffix
    state.running = True

    sys.path.insert(0, str(ROOT))
    from main import parse_prompts, find_images  # type: ignore

    imgs = find_images(Path(images))
    prompt_nos = {n for n, _ in parse_prompts(Path(prompts))} if have_prompts else set()

    # Explicit selection (from the image grid) overrides start-based range.
    selected: set[int] | None = None
    if scenes.strip():
        try:
            selected = {int(x) for x in scenes.split(",") if x.strip()}
        except ValueError:
            state.running = False
            raise HTTPException(400, f"bad scenes list: {scenes!r}")

    work: list[int] = []
    for n in sorted(imgs):
        if selected is not None:
            if n not in selected:
                continue
        elif n < start:
            continue
        work.append(n)

    if not work:
        state.running = False
        raise HTTPException(400, "no scenes to run (check selection / start / image filenames)")

    # build scene plan + distribute round-robin across logged-in accounts
    state.scenes = {n: {"state": "pending", "acc": None} for n in work}
    buckets: dict[int, list[int]] = {a.idx: [] for a in logged}
    for i, n in enumerate(work):
        acc = logged[i % len(logged)]
        buckets[acc.idx].append(n)
        state.scenes[n]["acc"] = acc.idx

    mode = "with prompts" if have_prompts else "wrapper-only (no prompt file)"
    state.log(f"batch start: {len(work)} scenes across {len(logged)} account(s) [{mode}]")
    for acc in logged:
        scenes_for = buckets[acc.idx]
        state.log(f"  account #{acc.idx} -> scenes {scenes_for}")
        task = asyncio.create_task(
            run_worker(acc, scenes_for, images, state.prompts_file, suffix)
        )
        state.workers.append(task)

    return {"ok": True, "workers": len(state.workers), "scenes": work}


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
            "--cdp", f"http://localhost:{acc.port}",
            "--start", str(n),
            "--limit", "1",
            "--tabs", "1",
        ]
        if prompts:
            cmd += ["--prompts", prompts]
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
