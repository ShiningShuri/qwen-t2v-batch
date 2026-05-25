# Qwen 이미지→비디오 배치 자동화

Qwen Chat의 image-to-video 기능을 자동화. 이미지 폴더와 프롬프트 파일을 주면 영상을
한 씬씩 생성·다운로드.

## 누구를 위한 것

- VS Code + Claude Code 사용 가능한 개발자
- 한 번에 여러 영상을 배치로 만들고 싶은 사람
- Qwen 무료 한도가 작아서 여러 계정을 동시에 돌리고 싶은 사람

## 동작 흐름

1. Chrome을 디버그 모드로 띄움 (1개 이상)
2. 각 Chrome 창에서 사용자가 Qwen에 직접 로그인 (CAPTCHA 통과)
3. `main.py`가 CDP로 그 Chrome에 붙어 자동화:
   - 새 채팅 탭 열기
   - 이미지 첨부 + 프롬프트 paste
   - 영상 생성 대기
   - 결과 mp4 직접 다운로드

## 한도

- Qwen 무료 한도 = 1 계정당 약 4–5개 영상 / 3시간
- 한도 도달 시 즉시 정지. 다른 계정으로 로그인 후 `--start <next>`로 재개

## 설치

### 1) Python 3.14 (없으면)

폴더에 동봉된 installer 더블클릭:
- `python-3.14.3-amd64.exe` (일반 Windows 64bit)
- `python-3.14.3-32bit.exe` (32bit Windows)
- `python-3.14.3-arm64.exe` (ARM64 Windows)

**"Add to PATH" 체크 필수.**

### 2) 가상환경 + 의존성

PowerShell 또는 VS Code 터미널에서:

```powershell
cd C:\Users\<you>\Documents\qwen-t2v-batch
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\playwright install chromium
```

## 사용법 (1 계정 / 단일 Chrome)

### 1) Chrome 디버그 모드 띄우기

```powershell
.\start-chrome.ps1
```

뜬 Chrome 창에서 chat.qwen.ai 에 로그인 (한 번만 — 이후 `chrome-profile/` 폴더에 쿠키 유지).

### 2) 자동화 실행

```powershell
.\run.ps1
```

또는 직접:

```powershell
.venv\Scripts\python main.py `
  --images "이미지폴더경로" `
  --prompts "프롬프트파일.txt" `
  --start 1 --limit 0 --tabs 1 `
  --cdp http://localhost:9222
```

결과는 `downloads/scene_NN.mp4` 로 저장.

## 멀티 계정 (병렬 가속)

Chrome을 N개 동시에 띄워 각 다른 계정으로 로그인, 작업을 나눠 돌릴 수 있음.

```
start-chrome.ps1   → port 9222, chrome-profile/    (계정 A)
start-chrome2.ps1  → port 9224, chrome-profile-2/  (계정 B)
start-chrome3.ps1  → port 9226, chrome-profile-3/  (계정 C)
```

각 Chrome에 다른 계정으로 로그인한 뒤, 작업을 분배:

```powershell
# 계정 A: 씬 1~6
.venv\Scripts\python main.py --cdp http://localhost:9222 --start 1 --limit 6  ...

# 계정 B: 씬 7~12
.venv\Scripts\python main.py --cdp http://localhost:9224 --start 7 --limit 6  ...

# 계정 C: 씬 13~18
.venv\Scripts\python main.py --cdp http://localhost:9226 --start 13 --limit 6 ...
```

세 PowerShell 창을 동시에 띄우면 병렬 실행. 한 계정이 한도 도달해도 다른 계정은 계속.

## 파일명 충돌 회피 (`--suffix`)

같은 이미지로 다른 프롬프트를 시도할 때:

```powershell
.venv\Scripts\python main.py ... --suffix remake
```

→ `downloads/scene_01_remake.mp4` 형식으로 저장.

## 프롬프트 파일 형식

```
=== Scene 1 (hook) ===
씬 1 동작 / 분위기 설명

=== Scene 2 (hook) ===
씬 2 동작 / 분위기 설명
```

각 블록은 `main.py`의 `WRAPPER_TEMPLATE`에 들어가 wrapper + scene 형태로 Qwen에 전달.
`SAFETY_SUBSTITUTIONS` 가 콘텐츠 필터를 자극할 만한 문구를 자동 완화.

## 옵션 요약

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `--images <dir>` | (필수) | `scene_01.png`, `scene_02.jpg`... 가 있는 폴더 |
| `--prompts <file>` | (필수) | `=== Scene N ===` 헤더로 구분된 파일 |
| `--cdp <url>` | `http://localhost:9222` | 붙을 Chrome 디버그 포트 |
| `--start N` | 1 | 씬 N부터 시작 (재개용) |
| `--limit N` | 1 | 첫 N개만 (`0` = 전체) |
| `--tabs N` | 1 | 동시 탭 수 (한도 고려 — 1 추천) |
| `--suffix <s>` | "" | 파일명 접미사 (`scene_01_<s>.mp4`) |
| `--download-dir <dir>` | `./downloads` | 결과 저장 위치 |
| `--no-download` | off | 제출만, 다운은 사용자 수동 |
| `--preview` | off | 자동화 안 함, 최종 prompt만 출력 |

## 누락 다운로드 수동 복구

자동 다운로드가 실패한 씬은 SUMMARY에 표시. 수동으로 받으려면:

```powershell
.venv\Scripts\python download_current.py <scene_no> <cdp_url> [suffix]
```

예: `python download_current.py 5 http://localhost:9222 remake`
→ Chrome #1의 가장 최근 채팅 탭에서 비디오 src 추출 → `downloads/scene_05_remake.mp4`

## 알려진 한계

- Qwen UI가 변하면 셀렉터가 깨질 수 있음 (`.mode-select`, `.video-bg-card`, `#filesUpload`).
  변경 시 `inspect_dom.py` / `inspect_video.py` 로 DOM 재분석.
- 한국어 경로는 PowerShell 인코딩 문제로 깨질 수 있음. 그럴 땐 Bash나 Python에서 직접
  명령을 호출하면 안전.
- Qwen 콘텐츠 안전 필터가 false positive로 거부할 수 있음. `WRAPPER_TEMPLATE`을
  더 짧고 자연스럽게 다듬으면 통과 확률 ↑.

## 폴더 구조

```
qwen-t2v-batch/
├── main.py                      자동화 본체
├── download_current.py          수동 다운로드 보조
├── inspect_dom.py               DOM 디버깅
├── inspect_video.py             결과 비디오 디버깅
├── start-chrome.ps1             Chrome #1 디버그
├── start-chrome2.ps1            Chrome #2 디버그
├── start-chrome3.ps1            Chrome #3 디버그
├── start-edge.ps1               Edge 디버그 (대안)
├── run.ps1 / run-a.ps1 / ...    각 계정별 실행 스크립트
├── prompts_simplified.txt       18씬 동작 프롬프트 예시
├── prompts_remake.txt           헤더만 (모든 씬 공통 wrapper 용)
├── requirements.txt
├── README.md (이 파일)
├── chrome-profile/              계정별 Chromium user-data (gitignore)
├── downloads/                   결과 mp4 (gitignore)
└── .venv/                       Python 가상환경 (gitignore)
```
