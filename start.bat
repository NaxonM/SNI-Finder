@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM ---------------------------------------------------------------------------
REM Self-elevation: SNISPF wrong_seq probing needs Administrator (raw packets).
REM We try once; if UAC was denied we fall through and let the Python layer
REM report the precise error to the user. The --elevated marker prevents
REM infinite re-launch loops when elevation fails.
REM ---------------------------------------------------------------------------
net session >nul 2>&1
if errorlevel 1 (
    if /I "%~1"=="--elevated" goto :after_elevation
    echo Requesting Administrator privileges for SNISPF raw packet probing...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '--elevated' -Verb RunAs" >nul 2>&1
    if errorlevel 1 (
        echo UAC elevation was denied or failed. Continuing without admin -- the scanner will warn you.
        timeout /t 2 >nul
    ) else (
        exit /b 0
    )
)

:after_elevation
REM Strip the --elevated marker so it doesn't leak into Python argv.
if /I "%~1"=="--elevated" shift

set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 (
	py -3 -c "import sys" >nul 2>&1
	if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
	where python >nul 2>&1
	if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
	where python3 >nul 2>&1
	if not errorlevel 1 set "PYTHON_CMD=python3"
)
if not defined PYTHON_CMD (
	echo Python was not found. Install Python 3.10+ and relaunch.
	pause
	exit /b 1
)

call :ensure_requirements
if errorlevel 1 (
	echo.
	echo Failed to install required Python packages from requirements.txt.
	echo Please check your internet connection and ensure pip is installed.
	echo You can also install manually by running: pip install -r requirements.txt
	pause
	exit /b 1
)

call %PYTHON_CMD% -c "from sni_finder.settings import load_settings; import sys; s = load_settings(); sys.exit(0 if str(getattr(s, 'vless_source', '')).strip() else 1)"
if errorlevel 1 (
	echo Starting first-time setup wizard...
	call %PYTHON_CMD% scanner.py onboarding
	if errorlevel 1 (
		echo.
		echo Setup was cancelled or failed.
		set EXITCODE=%ERRORLEVEL%
		echo Log file: logs\scanner.log
		pause
		exit /b %EXITCODE%
	)
	call %PYTHON_CMD% -c "from sni_finder.settings import load_settings; import sys; s = load_settings(); sys.exit(0 if str(getattr(s, 'vless_source', '')).strip() else 1)"
	if errorlevel 1 (
		echo.
		echo vless_source is still empty. Please set it and relaunch.
		echo Log file: logs\scanner.log
		pause
		exit /b 1
	)
)

cls
call %PYTHON_CMD% scanner.py
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" (
	echo Scanner exited with an error. Code=%EXITCODE%
) else (
	echo Scanner closed.
)
echo Log file: logs\scanner.log
pause
exit /b %EXITCODE%
endlocal

:ensure_requirements
call %PYTHON_CMD% -c "import requests, socks, rich" >nul 2>&1
if not errorlevel 1 exit /b 0

echo Missing required Python packages. Trying to install from requirements.txt...

call %PYTHON_CMD% -m pip install --disable-pip-version-check -r requirements.txt >nul 2>&1
if not errorlevel 1 exit /b 0

call %PYTHON_CMD% -m pip install --user --disable-pip-version-check -r requirements.txt >nul 2>&1
if not errorlevel 1 exit /b 0

call %PYTHON_CMD% -m ensurepip --upgrade >nul 2>&1
call %PYTHON_CMD% -m pip install --disable-pip-version-check -r requirements.txt >nul 2>&1
if not errorlevel 1 exit /b 0

where pip >nul 2>&1
if errorlevel 1 exit /b 1

pip install --disable-pip-version-check -r requirements.txt >nul 2>&1
if not errorlevel 1 exit /b 0

pip install --user --disable-pip-version-check -r requirements.txt >nul 2>&1
if not errorlevel 1 exit /b 0

exit /b 1
