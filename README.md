# Qwen 이미지→비디오 배치 자동화

Qwen Chat의 image-to-video를 GUI 한 화면에서 배치 처리.
**더블클릭 한 번**으로 설치 + 서버 + 브라우저 GUI 자동 실행.

## 사용 시나리오

> 18개 이미지가 있어. 각각 똑같은 톤의 5초 영상으로 만들고 싶어.
> Qwen은 무료지만 한도가 작아서 여러 계정을 동시에 돌려야 해.
> PowerShell 명령 외울 시간 없음.

→ 이 도구가 GUI에서 계정 N개 관리 + 작업 자동 분배 + 자동 다운로드를 해줌.

## 빠른 시작

### 사전 준비 (1회만)
- Windows 10/11
- Python 3.10+ ([`python-3.14.3-amd64.exe`](https://www.python.org/ftp/python/3.14.3/python-3.14.3-amd64.exe) 더블클릭 → "Add to PATH" 체크 → Install)
- Google Chrome 설치되어 있어야 함

### 실행
1. 이 폴더에서 **`start.bat`** 더블클릭
2. 첫 실행 시 자동으로 가상환경 + 의존성 + Chromium 설치 (2~3분)
3. 끝나면 브라우저에서 **`http://localhost:8765`** 자동으로 열림
4. GUI에서:
   - **계정 추가** 클릭 → Chrome 창 자동으로 뜸
   - 그 Chrome에서 Qwen에 로그인 (1회만, CAPTCHA 통과)
   - **로그인 확인** 클릭 → 카드가 "로그인 완료" 로 바뀜
   - 필요하면 계정 2~3개 더 추가 (각 다른 Qwen 계정)
   - 이미지 폴더 / 프롬프트 파일 경로 입력
   - **▶ 배치 시작** 클릭
5. 작업 끝나면 **📂 다운로드 폴더 열기**

## GUI 화면

```
┌─ 계정 (Chrome 인스턴스) ─────────────────────────[＋ 계정 추가]
│  ┌───────────┐  ┌───────────┐  ┌───────────┐
│  │ 계정 1    │  │ 계정 2    │  │ 계정 3    │
│  │ ● 로그인  │  │ ● 로그인  │  │ ◌ 시작중  │
│  │ 완료 6    │  │ 완료 5    │  │ 완료 0    │
│  │ [Chrome]  │  │ [Chrome]  │  │ [Chrome]  │
│  └───────────┘  └───────────┘  └───────────┘
│
├─ 입력
│  📁 이미지 폴더: C:\Users\...\scenes
│  📄 프롬프트 파일: prompts.txt
│  접미사: (비워두면 scene_NN.mp4)
│  시작 씬 번호: 1
│
├─ [▶ 배치 시작]  [■ 중지]  [📂 다운로드 폴더 열기]
│
└─ 로그 (라이브)
   [11:23:01] [acc#1] scene 01 starting
   [11:23:14] [acc#1] [scene 01] submitted OK
   [11:25:33] [acc#1] [scene 01] downloaded OK
   [11:25:34] [acc#2] scene 02 starting
   ...
```

## 동작 원리

- `app.py` = FastAPI 로컬 서버 (`localhost:8765`)
- 계정 추가 시 별도 `chrome-profile-N/` 폴더 + 디버그 포트 (9222, 9224, 9226...)로
  Chrome 새 인스턴스를 자동 실행
- 작업 시작하면 씬을 라운드로빈으로 계정들에 분배
- 각 씬마다 `main.py`를 subprocess로 호출 → Playwright가 CDP로 그 Chrome에 붙어
  자동화 (이미지 첨부, 프롬프트 paste, 영상 생성 대기, CDN에서 mp4 직접 fetch)
- 한 계정이 한도(`오늘 한도 초과...`)에 걸려도 그 계정만 멈추고 나머지는 계속

## 한도 정보

- Qwen 무료 한도: 1계정 / 3시간당 약 **4~5개 영상**
- 18씬이면 3~4계정 동시에 돌리는 게 현실적
- 한도 도달 시 GUI의 해당 계정 카드가 **"한도 도달"** 빨간 뱃지로 변함 → 그 계정만 정지
- 새 계정 만들고 **계정 추가** 클릭 → 그 카드 Chrome 열어서 로그인 → **▶ 배치 시작** 다시

## 프롬프트 파일 형식

```
=== Scene 1 (hook) ===
씬 1 동작 / 분위기 설명

=== Scene 2 (hook) ===
씬 2 동작 / 분위기 설명
```

- 헤더 `=== Scene N (...) ===` 로 구분
- 헤더 이후 줄이 그 씬의 동작 묘사
- `main.py`의 `WRAPPER_TEMPLATE`에 들어가 Qwen에 paste

## 이미지 파일명

- `scene_01.jpg`, `scene_02.png` 또는 `scene01.jpg` 같은 형식 (언더바 옵션)
- 프롬프트의 Scene 번호와 매칭됨

## 접미사 (--suffix)

같은 이미지로 다른 프롬프트를 재시도할 때 파일 충돌 방지:
- 접미사 비움 → `scene_01.mp4`
- 접미사 `remake` → `scene_01_remake.mp4`
- 접미사 `2` → `scene_01_2.mp4`

## 고급: CLI 단독 사용

GUI 안 쓰고 CLI로:

```powershell
.venv\Scripts\python main.py `
  --images "이미지폴더" `
  --prompts "prompts.txt" `
  --cdp http://localhost:9222 `
  --start 1 --limit 0 --suffix remake
```

옵션:
| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `--images <dir>` | 필수 | 이미지 폴더 |
| `--prompts <file>` | 필수 | 프롬프트 파일 |
| `--cdp <url>` | `http://localhost:9222` | 붙을 Chrome 디버그 포트 |
| `--start N` | 1 | 씬 N부터 시작 |
| `--limit N` | 1 | 첫 N개만 (0 = 전체) |
| `--suffix <s>` | "" | `scene_NN_<s>.mp4` |
| `--tabs N` | 1 | 동시 탭 (1 추천) |
| `--preview` | off | 자동화 안 함, 최종 prompt 출력만 |
| `--no-download` | off | 제출만, 다운은 수동 |

수동 다운(자동 다운 실패 시):

```powershell
.venv\Scripts\python download_current.py <scene_no> <cdp_url> [suffix]
```

## 폴더 구조

```
qwen-t2v-batch/
├── start.bat                ← 더블클릭 진입점
├── app.py                   FastAPI GUI 서버
├── main.py                  자동화 코어 (Playwright + CDP)
├── download_current.py      수동 다운 보조
├── inspect_dom.py           DOM 디버깅 (셀렉터 깨질 때)
├── inspect_video.py         결과 비디오 검사
├── prompts_simplified.txt   예시 프롬프트 (18씬)
├── prompts_remake.txt       헤더만 (공통 wrapper용)
├── requirements.txt
├── README.md (이 파일)
├── chrome-profile-*/        계정별 Chrome user-data (gitignore)
├── downloads/               결과 mp4 (gitignore)
└── .venv/                   Python 가상환경 (gitignore)
```

## 알려진 한계

- Qwen UI가 변경되면 셀렉터(`.mode-select`, `.video-bg-card`, `#filesUpload`)가 깨질 수
  있음. `inspect_dom.py` / `inspect_video.py` 로 새 셀렉터 추출 후 `main.py` 수정.
- 첫 로그인의 CAPTCHA는 본질적으로 수동 (봇 방지). 한 번 통과한 후 `chrome-profile-N/`
  에 쿠키가 남아 다음부터는 자동.
- 한국어 경로는 PowerShell 인코딩 문제로 깨질 수 있음 → GUI에서 입력하거나, 영문 폴더
  사용 권장.

## 라이선스 / 면책

Qwen 무료 한도 안에서 개인 사용 가능. 한도 우회용 어뷰징 X. Qwen 약관 준수.
