@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   STT POC 시뮬레이터 종료
echo ============================================

rem ---- 1. 포트를 점유한 프로세스 종료 (백엔드 8000, 프론트엔드 5173)
for %%P in (8000 5173) do (
    for /f "tokens=5" %%I in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do (
        echo [종료] 포트 %%P  PID %%I
        taskkill /PID %%I /T /F >nul 2>&1
    )
)

rem ---- 2. start.bat / share.bat 이 연 서버 창 닫기
taskkill /FI "WINDOWTITLE eq STT-POC Backend*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq STT-POC Frontend*" /T /F >nul 2>&1

rem ---- 2-1. share.bat 의 외부 공개(Tailscale Funnel) 끄기
set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
if exist "%TS%" (
    "%TS%" funnel reset >nul 2>&1 && echo [종료] 외부 공개 주소를 닫았습니다.
)

rem ---- 3. 확인
set LEFT=
netstat -ano | findstr ":8000 " | findstr LISTENING >nul && set LEFT=1
netstat -ano | findstr ":5173 " | findstr LISTENING >nul && set LEFT=1
if defined LEFT (
    echo [경고] 아직 포트를 사용 중인 프로세스가 있습니다. 관리자 권한으로 다시 실행해 보세요.
) else (
    echo 모두 종료되었습니다.
)
timeout /t 3 >nul
