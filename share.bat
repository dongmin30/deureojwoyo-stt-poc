@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title STT-POC Share

echo ============================================
echo   STT POC 시뮬레이터 - 외부 공유 모드
echo   (이 PC 에서 실행하고 Tailscale Funnel 로 인터넷에 공개)
echo ============================================

set "TS=%ProgramFiles%\Tailscale\tailscale.exe"

rem ---- 1. 필수 프로그램 확인
where python >nul 2>&1 || (echo [오류] Python 이 없습니다. & goto :fail)
where npm >nul 2>&1 || (echo [오류] Node.js^(npm^) 가 없습니다. & goto :fail)
if not exist "%TS%" (echo [오류] Tailscale 이 설치되어 있지 않습니다. README 의 "외부 공유" 를 참고하세요. & goto :fail)
"%TS%" status >nul 2>&1 || (echo [오류] Tailscale 에 로그인되어 있지 않습니다. 작업표시줄 Tailscale 아이콘에서 로그인하세요. & goto :fail)

rem ---- 2. 이미 실행 중인 서버가 있으면 먼저 끄게 한다
netstat -ano | findstr ":8000 " | findstr LISTENING >nul && (
    echo [안내] 서버가 이미 켜져 있습니다. 먼저 stop.bat 을 실행한 뒤 다시 시도하세요.
    goto :fail
)

rem ---- 3. 설정 확인
if not exist ".env" (
    echo [오류] .env 가 없습니다. 외부 공유는 실제 인식 모드로만 합니다. README "무료 모드" 를 참고하세요.
    goto :fail
)

rem ---- 4. 의존성 (처음 한 번만)
python -c "import fastapi, uvicorn, jiwer, rank_bm25, imageio_ffmpeg, qrcode" >nul 2>&1
if errorlevel 1 (
    echo [설치] 백엔드 Python 패키지를 설치합니다...
    python -m pip install -r backend\requirements.txt || (echo [오류] pip 설치 실패 & goto :fail)
)
if not exist "frontend\node_modules" (
    echo [설치] 프론트엔드 패키지를 설치합니다...
    pushd frontend
    call npm install || (popd & echo [오류] npm install 실패 & goto :fail)
    popd
)

rem ---- 5. 로컬 LLM(Ollama) 사용 시 실행 확인
findstr /b /c:"LLM_PROVIDER=ollama" .env >nul 2>&1 && (
    netstat -ano | findstr ":11434 " | findstr LISTENING >nul || (
        echo [실행] Ollama 를 시작합니다...
        if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
    )
)

rem ---- 6. 화면 빌드 (백엔드가 같은 주소에서 내보냄)
echo [빌드] 화면을 빌드합니다...
pushd frontend
call npm run build >nul 2>&1 || (popd & echo [오류] 화면 빌드 실패. frontend 폴더에서 npm run build 로 원인을 확인하세요. & goto :fail)
popd

rem ---- 7. 접속 코드: .env 의 SHARE_ACCESS_CODE (start.bat 에는 적용 안 됨), 없으면 이번 실행용 6자리 무작위
set "CODE="
for /f "usebackq tokens=1,* delims==" %%a in (".env") do if /i "%%a"=="SHARE_ACCESS_CODE" set "CODE=%%b"
if not defined CODE set /a CODE=^(%RANDOM% * 32768 + %RANDOM%^) %% 900000 + 100000
set "ACCESS_CODE=%CODE%"

rem ---- 8. 서버 실행 (이 PC 안에서만 열고, 밖으로는 Funnel 이 HTTPS 로 연결)
echo [실행] 서버를 켭니다...
start "STT-POC Backend (share)" /D "%~dp0backend" cmd /k python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
set /a WAIT=0
:waitloop
ping -n 2 127.0.0.1 >nul
set /a WAIT+=1
curl -s -o nul http://127.0.0.1:8000/api/status && goto :up
if !WAIT! GEQ 60 (echo [오류] 서버가 60초 안에 뜨지 않았습니다. 서버 창의 오류를 확인하세요. & goto :fail)
goto :waitloop
:up

rem ---- 9. Tailscale Funnel 로 공개 (443 HTTPS -> 이 PC 의 8000)
echo [공개] Tailscale Funnel 을 켭니다...
"%TS%" funnel --bg 8000
if errorlevel 1 (
    echo [오류] Funnel 을 켜지 못했습니다. 위 메시지에 나온 링크에서 Funnel 사용을 허용한 뒤 다시 실행하세요.
    goto :fail
)

rem ---- 10. 주소·QR 안내 + 실제로 열리는지 확인
python tools\share_info.py --code %CODE%
echo.
echo 공유 중입니다. 끝내려면 stop.bat 을 실행하세요 (서버와 공개 주소를 함께 닫습니다).
echo 이 PC 가 꺼지거나 절전 모드에 들어가면 외부에서 접속할 수 없습니다.
pause
exit /b 0

:fail
echo.
echo 공유를 시작하지 못했습니다.
pause
exit /b 1
