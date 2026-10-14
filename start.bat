@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title STT-POC Launcher

echo ============================================
echo   STT POC 시뮬레이터 시작
echo ============================================

rem ---- 1. 필수 프로그램 확인
where python >nul 2>&1 || (echo [오류] Python 이 설치되어 있지 않거나 PATH 에 없습니다. & goto :fail)
where npm >nul 2>&1 || (echo [오류] Node.js^(npm^) 가 설치되어 있지 않거나 PATH 에 없습니다. & goto :fail)

rem ---- 2. 이미 실행 중인지 확인
netstat -ano | findstr ":8000 " | findstr LISTENING >nul && (
    echo [안내] 8000 포트가 이미 사용 중입니다. 먼저 stop.bat 을 실행하세요.
    goto :fail
)
netstat -ano | findstr ":5173 " | findstr LISTENING >nul && (
    echo [안내] 5173 포트가 이미 사용 중입니다. 먼저 stop.bat 을 실행하세요.
    goto :fail
)

rem ---- 3. .env 확인 (없으면 이번 실행만 mock 모드)
if not exist ".env" (
    echo [안내] .env 파일이 없어 mock 모드^(API 키 없이 가짜 인식/답변^)로 실행합니다.
    echo        실제 API 를 쓰려면 .env.example 을 .env 로 복사하고 키를 넣으세요.
    set "STT_PROVIDER=mock"
    set "LLM_PROVIDER=mock"
)

rem ---- 4. 의존성 설치 (처음 한 번만)
python -c "import fastapi, uvicorn, jiwer, rank_bm25, imageio_ffmpeg" >nul 2>&1
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

rem ---- 4-1. 로컬 LLM(Ollama) 사용 시 실행 확인
findstr /b /c:"LLM_PROVIDER=ollama" .env >nul 2>&1 && (
    netstat -ano | findstr ":11434 " | findstr LISTENING >nul || (
        echo [실행] Ollama 를 시작합니다...
        if exist "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe" (
            start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
        ) else (
            echo [경고] Ollama 가 설치되어 있지 않습니다. README 의 "무료 모드" 를 참고하세요.
        )
    )
)

rem ---- 5. 서버 실행 (각각 별도 창)
echo [실행] 백엔드  : http://127.0.0.1:8000
start "STT-POC Backend" /D "%~dp0backend" cmd /k python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo [실행] 프론트엔드: http://localhost:5173
start "STT-POC Frontend" /D "%~dp0frontend" cmd /k npm run dev

rem ---- 6. 프론트엔드가 뜰 때까지 기다린 뒤 브라우저 열기 (최대 60초)
set /a WAIT=0
:waitloop
ping -n 2 127.0.0.1 >nul
set /a WAIT+=1
netstat -ano | findstr ":5173 " | findstr LISTENING >nul && goto :ready
if !WAIT! GEQ 60 (
    echo [경고] 60초 안에 프론트엔드가 뜨지 않았습니다. "STT-POC Frontend" 창을 확인하세요.
    goto :end
)
goto :waitloop

:ready
netstat -ano | findstr ":8000 " | findstr LISTENING >nul || (
    echo [경고] 백엔드가 아직 실행되지 않았습니다. "STT-POC Backend" 창의 오류 메시지를 확인하세요.
)
start "" http://localhost:5173
echo.
echo 실행 완료. 브라우저에서 http://localhost:5173 을 사용하세요. ^(이 PC 전용이라 접속 코드 없음^)
echo 외부에서 접속하게 하려면 share.bat 을 쓰세요. 접속 코드와 QR 은 그 창에 표시됩니다.
echo 종료하려면 stop.bat 을 실행하세요.
goto :end

:fail
echo.
echo 시작하지 못했습니다.
pause
exit /b 1

:end
timeout /t 5 >nul
exit /b 0
