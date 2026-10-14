# 고령자·사투리 음성인식 개선 POC 웹 시뮬레이터

「디지털미디어원리」 팀프로젝트 8주차 중간발표용 POC.
같은 음성을 **A: 그냥 STT** 와 **B: 우리 방식(전처리 + 방언 힌트 STT + LLM 문맥 보정)** 으로 처리해 나란히 보여 주고,
AI 상담사·검색엔진 두 시뮬레이션에서 결과 차이와 참여자 반응(맞아요/아니에요)을 SQLite에 기록한다.

```
poc_source/
  backend/    FastAPI (Python 3.11+)      — 음성 처리 엔진, API, SQLite
  frontend/   React + Vite + TypeScript   — 화면
  data/       방언 사전 · 상담 시나리오 · 검색 문서
  samples/    시연용 음성 (커밋 금지)
  storage/    실행 중 생성: audio/ (원본·전처리 wav), poc.db
```

## 1. 설치

### 필요 프로그램
- Python 3.11 이상 (3.13에서 확인)
- Node.js 18 이상 (22에서 확인)
- ffmpeg (선택 — 아래 참고)

### ffmpeg
음성 변환·음량 정규화·속도 보정에 ffmpeg를 쓴다.

- **시스템에 설치(권장)**
  - Windows: `winget install Gyan.FFmpeg` (설치 후 터미널 재시작)
  - macOS: `brew install ffmpeg`
  - Ubuntu: `sudo apt install ffmpeg`
- **설치하지 않아도 동작함**: `PATH`에 `ffmpeg`가 없으면 `imageio-ffmpeg` 패키지에 들어 있는 ffmpeg 실행 파일을 자동으로 쓴다
  (requirements.txt에 포함). 서버 시작 로그의 `ffmpeg=...` 에서 실제 사용 경로를 확인할 수 있다.

### 백엔드 패키지
```bash
python -m pip install -r backend/requirements.txt
# 로컬 Whisper(STT_PROVIDER=local)를 쓸 때만
python -m pip install -r backend/requirements-local.txt
```
가상환경을 쓰려면 먼저 `python -m venv .venv` 후 활성화(`.venv\Scripts\activate` / `source .venv/bin/activate`).

### 프론트엔드 패키지
```bash
cd frontend && npm install
```

## 2. `.env` 설정

```bash
cp .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
```

| 변수 | 값 | 설명 |
|---|---|---|
| `STT_PROVIDER` | `openai` / `local` / `mock` | 음성인식 공급자 |
| `OPENAI_STT_MODEL` | `whisper-1` (기본) / `gpt-4o-transcribe` | `openai` STT 모델 |
| `LOCAL_WHISPER_MODEL` | `small` (기본) / `medium` | `local`(faster-whisper) 모델 크기. 첫 실행 때 자동 다운로드 (small 약 500MB, medium 약 1.5GB·더 정확·느림) |
| `SHARE_ACCESS_CODE` | 비우면 무작위 | `share.bat`(외부 공유) 실행 때만 쓰는 접속 코드. `start.bat`(내 PC) 은 잠금 없음 |
| `LOCAL_WHISPER_DEVICE` | `cpu` (기본) / `cuda` | `cuda` 는 NVIDIA GPU + `backend/requirements-gpu.txt` 필요. 실패 시 자동 CPU 전환 |
| `LLM_PROVIDER` | `anthropic` / `openai` / `ollama` / `mock` | 문맥 보정·상담사 LLM |
| `LLM_MODEL` | 비우면 기본값 | anthropic → `claude-sonnet-5-5`, openai → `gpt-4o-mini`, ollama → `exaone3.5:7.8b` |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434/v1` (기본) | `ollama` 서버 주소 |
| `OPENAI_API_KEY` | | `STT_PROVIDER=openai` 또는 `LLM_PROVIDER=openai` 일 때 필수 |
| `ANTHROPIC_API_KEY` | | `LLM_PROVIDER=anthropic` 일 때 필수 |

선택한 공급자에 필요한 키가 없으면 서버가 시작할 때 **무엇이 빠졌는지** 목록으로 알려 주고 종료한다.

### 실행 모드 3가지

| | 테스트 모드 | **무료 모드 (권장)** | API 모드 |
|---|---|---|---|
| 설정 | `STT_PROVIDER=mock`, `LLM_PROVIDER=mock` (`.env` 가 없으면 `start.bat` 이 이 모드로 띄움) | `STT_PROVIDER=local`, `LLM_PROVIDER=ollama` | `STT_PROVIDER=openai` + `OPENAI_API_KEY`, `LLM_PROVIDER=openai` 또는 `anthropic`(+`ANTHROPIC_API_KEY`) |
| 음성인식 | **음성을 무시**하고 정해진 예시 문장 | PC에서 faster-whisper (`medium` 권장) | OpenAI Whisper API |
| 문맥 보정 · 상담사 | 방언 사전 치환 · 모의 답변 | PC의 Ollama + EXAONE 3.5 7.8B (한국어 특화, GPU) | Claude / GPT |
| 비용 | 없음 | 없음 | 사용량만큼 |
| 한 번 분석 시간 | 즉시 | 약 6~8초 (CPU 음성인식이 대부분) | 수 초 |

#### 무료 모드 설치 (처음 한 번)

```powershell
python -m pip install -r backend/requirements-local.txt     # faster-whisper
winget install Ollama.Ollama                                # 로컬 LLM 실행기
ollama pull exaone3.5:7.8b                                  # 한국어 LLM (약 4.8GB)
```

`.env`:

```
STT_PROVIDER=local
LOCAL_WHISPER_MODEL=medium
LLM_PROVIDER=ollama
LLM_MODEL=exaone3.5:7.8b
```

Whisper 모델은 첫 실행 때 자동으로 내려받는다(medium 약 1.5GB). `start.bat` 은 Ollama 가 꺼져 있으면 켜 준다.

#### GPU 가속 (NVIDIA, 선택이지만 외부 공유 시 권장)

```powershell
python -m pip install -r backend/requirements-gpu.txt    # CUDA 12 cuBLAS + cuDNN 9 (약 2GB, CUDA 툴킷 설치 불필요)
```
`.env` 에 `LOCAL_WHISPER_DEVICE=cuda`, `LOCAL_WHISPER_MODEL=large-v3-turbo`.
RTX 4080 SUPER 실측: 인식 1건 CPU(medium) 2.7~3.6초 → GPU(turbo) 0.1~0.4초, 분석 전체 1.5~2.5초.
turbo 는 medium 이 "맨누리" 로 틀린 전라도 녹음의 "며느리" 를 A·B 모두 맞혔다.
GPU 에서 오류가 나면 자동으로 CPU 로 바꿔 계속 처리한다. 서버가 켜질 때 모델을 미리 불러 두므로(예열) 첫 사용자도 기다리지 않는다
(예열 전 첫 요청 14.6초 → 예열 후 2.5초).

#### 모델을 고른 근거 (실측)

- 음성인식: 실제 녹음 "오늘 못 본 8시 뉴스 보여주게" → small "오늘 **복번** 8시…" ✗ / medium "오늘 **못본** 8시…" ✓ (CPU 약 3초) / large-v3-turbo ✓ (약 5초).
- 보정 LLM: EXAONE 3.5 7.8B 와 Qwen2.5 14B 를 같은 오인식 문장 9개로 비교. Qwen 은 없는 말을 지어냄("간주"→"간호사", "억수로" 추가) → 제외.
  EXAONE 은 사투리→표준어 변환이 자연스럽고(밥 문나→밥 먹었어요?, 정구지→부추) 응답 0.5초 내외.
- 보정 호출은 `temperature=0` 으로 고정한다. Ollama 기본값(0.8)에서는 두 모델 모두 문장을 바꿔 써 버렸다.
- 한계: 7.8B 모델은 **사전에 없는 엉뚱한 낱말**(예: "복번")을 문맥으로 고치는 데 약하고 가끔 그럴듯한 말로 바꿔 쓴다.
  그래서 B 창에서 "보정 전 인식 원문" 을 항상 함께 보여 준다. 현재 모드는 `GET /api/status` 로도 확인할 수 있다.

### 보정 안전장치 (LLM 이 지어낸 말 거르기)

작은 LLM 은 STT 결과를 고치다가 **없는 말을 지어내거나 낱말을 빼먹는다**
(실제 사례: STT "그 집 맨누리는 … 얼굴도 동구시리한 거에 **아주 복시롭게** 생겼더라" →
LLM 보정 "그 집 **할아버지**는 … 얼굴이 둥글둥글하게 생겼더라". 실제 발화는 "며느리").
그래서 B 경로는 LLM 의 수정을 그대로 쓰지 않고 `backend/app/guard.py` 로 거른다.

- STT 원문과 LLM 보정문을 낱말 단위로 맞대어, **자모(ㄱㅏ…) 유사도가 0.5 이상이고 음절 수가 같은 교체**,
  띄어쓰기만 다른 교체, 방언 사전에 근거한 교체만 채택한다 (문나→뭇나 0.80, 복번→못 본 0.50 채택 /
  맨누리→할아버지 0.25, 진하그로→일찍 0.27, 간주→간호사(음절 증가) 거부).
- 낱말 삭제·추가는 모두 거부하고 원문을 유지한다.
- 표준어 변환도 같은 방식(기준 0.4, 음절 차 2까지, 사전의 방언 낱말은 LLM 표현 채택)으로 거른다.
- 거부한 수정은 응답의 `b.rejected` 로 내려가고, 화면 B 칸에 "AI 보정 중 N곳은 발음이 달라 원문을 유지했어요" 로 보인다.

한계: 안전장치는 LLM 이 **지어내는 것**을 막을 뿐, STT 가 이미 틀리게 적은 낱말을 바로잡지는 못한다.
사전에 없는 방언 낱말(예: 복시롭게)은 표준어로 바뀌지 않고 그대로 남는다.
`data/dialect_dict.json` 에 낱말을 추가하면 B 의 표준어 변환과 STT 힌트에 바로 반영된다.

### 전처리 기본값 (실측으로 정함)

명세는 음량 정규화·무음 단축·속도 보정을 기본으로 켜도록 했지만, 실제 고령자 전라도 녹음
("그 집 **며느리**는 아침 … 문안 인사를 드리고 …")으로 단계별로 껐다 켜 본 결과(Whisper medium):

| B 경로 설정 | "며느리" 인식 |
|---|---|
| 전처리 없음 + 힌트 없음 (= A) | 며느리 ✓ |
| 음량 정규화만 | **맨누리 ✗** |
| 속도 보정(1.15배)만 | **맨누리 ✗** |
| 무음 단축만 | 며느리 ✓ |
| 무음 단축 + 방언 사전 힌트 | 며느리 ✓ |
| 원본 + 방언 사전 힌트 (전처리 없음) | 맨누리 ✗ |

그래서 기본값은 **무음 단축만 켬**, 음량 정규화·속도 보정은 화면 "추가 설정"에서 켤 수 있는 옵션으로 둔다.
녹음 1개 기준이므로 표본을 늘려 다시 확인할 필요가 있다 — 이 자체가 가설 2("고령자 맞춤 전처리가 정확도를 높인다")에
대한 중요한 반례/조건이 된다 (Whisper 는 자체적으로 음량·속도 변화에 강하게 학습되어 있어 인위적 가공이 오히려 해가 될 수 있음).

## 3. 실행

**Windows 한 번에 실행/종료** — 프로젝트 폴더에서 더블클릭:

- `start.bat`: 처음 실행이면 패키지를 설치하고, 백엔드·프론트엔드를 각각 새 창으로 띄운 뒤 브라우저를 연다.
  `.env`가 없으면 그 실행에 한해 mock 모드로 뜬다. 포트 8000/5173이 이미 쓰이고 있으면 시작하지 않는다.
- `stop.bat`: 8000/5173 포트를 쓰는 프로세스와 서버 창을 모두 닫는다.

직접 실행하려면 터미널 두 개에서 각각:

```bash
# 백엔드 (http://localhost:8000)
cd backend && uvicorn app.main:app --reload --port 8000

# 프론트엔드 (http://localhost:5173)
cd frontend && npm install && npm run dev
```

브라우저에서 **http://localhost:5173** 접속. 프론트 개발 서버가 `/api` 요청을 백엔드(8000)로 넘긴다.
결과 요약 페이지는 **http://localhost:5173/summary**.

테스트:
```bash
python -m pytest backend/tests -q
```

## 4. 시연 절차

화면은 입력 카드 하나와 그 아래 A·B 결과 두 칸이 전부다.

1. http://localhost:5173 을 연다. 노란 "테스트 모드" 배너가 보이면 실제 음성이 인식되지 않는다 (위 표 참고).
2. 위쪽 탭에서 **AI 상담사** 또는 **검색엔진** 을 고른다.
3. **동의 체크박스**를 누른다 (필수 — 안 누르고 버튼을 누르면 체크박스가 노랗게 강조된다).
4. **화자 지역**·**고령자** 를 고르고, 상담이면 **상담 상황**, 검색이면 **찾으려던 것**(선택, hit@3 계산용)을 고른다.
   지역이 "모름/표준어"면 B가 방언 사전을 쓰지 않아 A와 B 결과가 같을 수 있다.
5. **[녹음하기]** (말한 뒤 "녹음 끝내기") 또는 **[음성 파일 올리기]** 에서 파일을 고르면 **바로 분석이 시작**된다.
   음성 파일을 탐색기에서 **브라우저 화면 아무 곳에나 끌어다 놓아도** 같다 (음성이 아닌 파일은 안내 메시지만 뜬다).
   `samples/` 에 파일이 있으면 "샘플 음성으로 해 보기" 에서 골라도 바로 분석한다.
6. 아래 A(회색)·B(보라) 칸에 AI가 들은 문장(달라진 글자 강조), 상담사 답변 또는 검색 결과가 나온다.
   상담은 계속 녹음하면 대화가 이어지고, "대화 처음부터" 로 초기화한다.
7. 참여자가 **"제가 한 말이 맞아요 / 아니에요"**, **"원하던 답이에요(결과예요) / 아니에요"** 를 누른다.
8. 정답 문장(CER 계산)·참여자 코드·연령대·지역·B 전처리 옵션은 입력 카드 아래 **"추가 설정"** 에 있다.
9. 상단 **결과 요약** 에서 전체 실행 수, A·B별 "맞아요" 비율, 평균 CER, hit@3 를 확인하고 **CSV 내보내기** (엑셀에서 바로 열림, UTF-8 BOM).

저장 위치: 음성 `storage/audio/날짜_참여자코드_runid_{raw|pre}.wav` (코드가 없으면 `anon`), 로그 `storage/poc.db` (`runs` 테이블).

## 5. samples 사용법

`samples/` 폴더에 `wav / mp3 / m4a / webm / ogg / flac` 파일을 넣으면 화면의 샘플 드롭다운에 나타난다(서버 재시작 불필요).
AI Hub 등 외부 데이터셋은 재배포가 금지되어 있으므로 `.gitignore` 로 제외되어 있다 — 각자 로컬에만 둔다.

## 6. 외부 공유 (발표·체험용) — `share.bat`

인식(Whisper)·보정(Ollama)이 이 PC 의 CPU/GPU 에서 돌기 때문에 서버는 **이 PC 에서 그대로 실행**하고,
**Tailscale Funnel** 로 인터넷에 HTTPS 주소를 연다. 마이크는 HTTPS 에서만 동작하므로 터널이 필수다.

```
share.bat   ← 더블클릭: 화면 빌드 → 서버 실행 → 공개 주소 열기 → 주소·접속 코드·QR 표시
stop.bat    ← 서버와 공개 주소를 함께 닫음
```

- **주소는 고정**: `https://<PC이름>.<tailnet>.ts.net`.
  매번 바뀌지 않으므로 발표 자료에 미리 넣어 둘 수 있다.
- **접속 코드**: 주소를 아는 아무나 GPU·기록에 접근하지 못하도록 잠근다.
  `.env` 에 `SHARE_ACCESS_CODE=123456` 처럼 정해 두면 고정, 없으면 실행할 때마다 6자리 무작위 코드를 만든다.
  이 코드는 `share.bat` 으로 켤 때만 걸린다. `start.bat`(내 PC 에서 개발·테스트) 은 코드 없이 바로 열린다.
  코드가 없으면 API(기록·CSV·녹음 파일 포함) 전부 401, 화면에는 코드 입력창이 뜬다.
- **QR 코드**: `share_qr.png` 에 `주소/?code=코드` 를 담아 만든다. 휴대폰으로 찍으면 코드 입력 없이 바로 들어가고,
  주소창에서 코드는 자동으로 지워진다. 발표 슬라이드에 이 QR 을 넣으면 된다 (`.gitignore` 처리됨).
- 서버는 `127.0.0.1` 에만 열고 외부 연결은 Funnel 이 HTTPS 로 대신한다 (같은 와이파이의 다른 기기에 직접 노출되지 않음).
- `share.bat` 은 `frontend/dist` 를 빌드해 백엔드가 화면과 API 를 **한 주소**에서 내보낸다 (개발 서버·프록시 불필요).

처음 한 번 확인할 것
1. 작업 표시줄 Tailscale 아이콘으로 로그인되어 있어야 한다.
2. Funnel 이 허용되지 않은 tailnet 이면 `share.bat` 이 허용 링크를 보여 준다 → 브라우저에서 허용 후 다시 실행.

주의
- **PC 가 꺼지거나 절전에 들어가면 접속이 끊긴다.** 발표·체험 기간에는 전원 설정에서 절전을 끈다.
- 인식·보정은 한 번에 하나씩 처리되므로 여러 명이 동시에 쓰면 차례로 기다린다 (GPU 기준 1건 약 1.5~2.5초, CPU 면 5~10초).
- 끝나면 반드시 `stop.bat` 으로 공개 주소를 닫는다 (`tailscale funnel reset`).
- 참여자 음성은 이 PC 에서만 처리되지만(무료 모드), 동의 안내 문구는 그대로 유지한다.

대안: Tailscale 을 쓸 수 없으면 `cloudflared tunnel --url http://localhost:8000` (계정 없이 임시 주소, 실행마다 바뀜).
이때도 `frontend` 에서 `npm run build` 후 백엔드를 `ACCESS_CODE` 환경변수와 함께 띄운다 (예: `set ACCESS_CODE=123456` 후 uvicorn 실행).

## 7. 명세에 없어 정한 것

**공통**
- 명세와 구현 계약서(`.omc/plans/poc-contract.md`)가 다르면 계약서를 따랐다.
- `mock` 공급자(STT·LLM)를 추가: 키 없이 화면·흐름을 검토하기 위함. 결과는 고정 문장이라 실제 성능과 무관.
- 참여자 정보·동의 체크는 브라우저 localStorage에 유지. 동의(`consent=true`) 없이 `/api/transcribe` 를 부르면 400.
- `/summary` 는 프론트 SPA 경로, 데이터는 `/api/summary`. `GET /api/samples`, `GET /api/audio/{파일명}` 를 추가.

**음성 처리**
- ffmpeg: `PATH` 의 ffmpeg 우선, 없으면 `imageio-ffmpeg` 번들 바이너리.
- 전처리 순서: 음량 정규화(ffmpeg `loudnorm=I=-16:TP=-1.5:LRA=11`) → 잡음 제거(noisereduce, 기본 끔) → 긴 무음 단축 → 속도 보정(ffmpeg `atempo=1.15`, 음높이 유지). 각 단계는 요청 필드 `pre_loudnorm / pre_denoise / pre_vad / pre_tempo` 로 켜고 끈다.
- VAD: silero는 torch가 필요해 제외, `webrtcvad-wheels`(30 ms 프레임, 민감도 2) 사용. import 실패 시 numpy 에너지 기반 VAD로 대체. 0.7초 넘는 무음은 앞뒤 0.15초씩 남겨 0.3초로 줄인다. 에너지가 사실상 0인 프레임은 무음으로 본다.
- A 경로와 B 경로는 스레드로 동시에 실행. 각 경로 `ms` 는 자기 작업 시간만(공통 변환 시간은 `convert_ms` 로 따로).

**STT · LLM**
- STT 언어는 `ko` 고정. B 경로 STT 힌트는 "경상도 사투리 대화. 단어, 단어, …" 형식으로 해당 지역 사전 앞쪽 최대 30단어·200자(Whisper prompt 길이 제한 때문).
- 문맥 보정 프롬프트에는 고령 여부, 지역, 상황(상담 시나리오 이름 또는 검색), 해당 지역 사전 전체, "원문에 없는 내용을 지어내지 말 것, 확신이 없으면 원문을 유지할 것" 규칙을 넣고 JSON(`dialect_text`, `standard_text`)만 받는다. 코드펜스·앞뒤 설명이 붙어도 JSON을 찾아 쓰고, 끝내 파싱이 안 되면 STT 원문을 그대로 쓴다. STT 결과가 비면 LLM을 부르지 않는다.
- 지역이 "모름/표준어"면 사전·힌트 없이 오인식만 바로잡도록 한다.
- 상담사: 시나리오 시스템 프롬프트 뒤에 "쉬운 말로 2~3문장, 존댓말, 못 알아들으면 다시 물어보기" 규칙을 덧붙인다. 대화 기록이 상담사 인사말로 시작하면(Anthropic API는 사용자 턴으로 시작해야 함) 앞에 "(상담 전화가 연결되었습니다)" 사용자 턴을 넣는다. 인식 문장이 비면 "(음성이 인식되지 않았습니다)" 로 보낸다.
- 상담에는 A는 `a_text`, B는 `b_standard_text` 를 쓴다. 대화 기록(history)은 프론트가 창별로 들고 있다가 매 요청에 보낸다.

**검색**
- 토큰화: 한글·영문·숫자 외 문자를 지우고 어절마다 음절 bigram(한 글자 어절은 그대로). 문서는 제목+요약+키워드로 색인.
- BM25 점수가 0보다 큰 문서만 최대 3개 반환 — 겹치는 토큰이 없으면 결과가 3개보다 적거나 비어 있을 수 있다(관련 없는 문서를 억지로 채우지 않음).
- hit@3: 정답 문서를 고르지 않았으면 `null`.

**기록 · 집계**
- `run_id` 는 12자리 16진수. 한 번의 녹음 = 한 run(상담 답변·검색 결과·피드백이 모두 그 run 에 붙는다). 같은 버튼을 다시 누르면 마지막 응답으로 덮어쓴다.
- CER: 공백과 문장부호·기호(유니코드 P·S 범주)를 지운 뒤 `jiwer.cer`. 정답이 비면 `null`, 인식 결과가 비면 1.0. B는 `cer`(방언 문장 기준)과 `cer_standard`(표준어 문장 기준)를 모두 기록하고, 요약의 B 평균 CER은 방언 문장 기준(`avg_cer_standard` 도 함께 제공).
- 요약의 "맞아요" 비율 = 해당 버튼을 누른 run 중 "맞아요" 비율(누르지 않은 run 제외). 비율은 0~1, 데이터가 없으면 `null`.
- CSV는 `runs` 테이블 전체(UTF-8 BOM). 검색 결과는 JSON 문자열로 들어간다.
- DB 스키마에 컬럼이 추가되면 서버 시작 때 기존 DB에 자동으로 컬럼을 추가한다(기존 로그 유지).
