@echo off
setlocal
cd /d "%~dp0"

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
	echo Please install dependencies manually and relaunch.
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
